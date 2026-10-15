const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function background(verdict = 'PASS') {
  const local = { kac: { user: { id: 1 }, selected: [2], profileRevision: 'r1' } };
  const session = {};
  const requests = [];
  let listener;
  const sandbox = {
    chrome: {
      runtime: { onMessage: { addListener(fn) { listener = fn; } } },
      storage: {
        local: { async get() { return local; } },
        session: { async get() { return session; }, async set(value) { Object.assign(session, value); } },
      },
      tabs: { async create() { return { id: 7 }; }, async sendMessage() {}, remove() {}, update() {} },
    },
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      return { ok: true, async json() { return { status: verdict, reason: '검증 결과' }; } };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../background.js'), 'utf8'), sandbox);
  const call = (message, tabId = 7) => new Promise(resolve => listener(message, { tab: { id: tabId } }, resolve));
  const context = { user_id: 1, selected: [2], member_ids: [2], excluded_allergens: ['우유'],
    product_id: 'p1', profileRevision: 'r1' };
  const enqueue = () => call({ type: 'kurly-add-cart', productId: 'p1', context }, 5);
  return { local, session, requests, sandbox, call, enqueue, context };
}

test('담기 작업은 서버의 현재 판정과 추천의 추가 회피 성분을 사용한다', async () => {
  const env = background();
  assert.equal((await env.enqueue()).ok, true);
  assert.equal((await env.call({ type: 'kurly-cart-validate' })).ok, true);
  assert.deepEqual(env.requests[0].body, { user_id: 1, member_ids: [2], product_id: 'p1', excluded_allergens: ['우유'] });
});

for (const status of ['BLOCK', 'UNVERIFIED']) {
  test(`${status} 상품은 담기를 허용하지 않는다`, async () => {
    const env = background(status);
    await env.enqueue();
    assert.equal((await env.call({ type: 'kurly-cart-validate' })).ok, false);
  });
}

for (const change of [
  kac => { kac.profileRevision = 'r2'; },
  kac => { kac.selected = [3]; },
  kac => { kac.user = { id: 9 }; },
]) {
  test('작업 탭이 열리는 동안 프로필 선택·내용·계정이 바뀌면 중단한다', async () => {
    const env = background();
    await env.enqueue();
    change(env.local.kac);
    assert.equal((await env.call({ type: 'kurly-cart-validate' })).ok, false);
    assert.equal(env.requests.length, 0);
  });
}

test('서버 응답을 기다리는 동안 프로필이 바뀌어도 중단한다', async () => {
  const env = background();
  await env.enqueue();
  env.sandbox.fetch = async () => {
    env.local.kac.profileRevision = 'r2';
    return { ok: true, async json() { return { status: 'PASS' }; } };
  };
  assert.equal((await env.call({ type: 'kurly-cart-validate' })).ok, false);
});

test('서버 연결 실패와 오래된 작업은 담기를 허용하지 않는다', async () => {
  const env = background();
  assert.equal((await env.call({ type: 'kurly-cart-validate' })).ok, false);
  await env.enqueue();
  env.sandbox.fetch = async () => { throw Error('offline'); };
  assert.equal((await env.call({ type: 'kurly-cart-validate' })).ok, false);
});

function panel() {
  const sent = [];
  const saved = {};
  const root = { appendChild() {}, addEventListener() {}, getElementById() { return null; } };
  const element = () => ({ attachShadow() { return root; }, innerHTML: '' });
  const sandbox = {
    document: { getElementById() { return null; }, createElement: element },
    location: { search: '', pathname: '/' }, URLSearchParams,
    CSSStyleSheet: class { replaceSync() {} },
    setTimeout() {}, setInterval() {},
    chrome: {
      runtime: { onMessage: { addListener() {} }, sendMessage(message, callback) {
        sent.push(message);
        callback({ ok: true });
      } },
      storage: { local: { async set(value) { Object.assign(saved, value); } }, onChanged: { addListener() {} } },
    },
  };
  let source = fs.readFileSync(path.join(__dirname, '../content.js'), 'utf8');
  // 브라우저 없이 실제 함수들을 실행하는 시험용 내보내기. 배포 소스는 변경하지 않는다.
  source = source.replace(/\n  start\(\);\r?\n\}\)\(\);/, '\n  globalThis.testing = { state, handlers, cartButton, addToKurlyCart, invalidateRecommendations };\n})();');
  vm.runInNewContext(source, sandbox);
  const t = sandbox.testing;
  const item = { id: 'p1', name: '만두', source: 'kurly', cross_matched: [], price: 1000, url: '' };
  Object.assign(t.state, { user: { id: 1 }, selected: [2], profileRevision: 'r1', threadId: 't1' });
  const message = { cart: [item], validation: { user_id: 1, member_ids: [2], selected: [2],
    excluded_allergens: ['우유'], profileRevision: 'r1' } };
  t.state.messages = [message];
  return { ...t, item, message, sent, saved };
}

test('프로필 변경은 기존 추천 카드를 비활성화하고 담기 요청도 차단한다', async () => {
  const env = panel();
  assert.match(env.cartButton(env.item, env.message, 0), /data-action="add-cart"/);
  env.invalidateRecommendations();
  assert.match(env.cartButton(env.item, env.message, 0), /disabled/);
  await env.addToKurlyCart('p1', '만두', 0);
  assert.equal(env.sent.length, 0);
});

test('검증 문맥이 없는 예전 저장 카드도 담기를 허용하지 않는다', () => {
  const env = panel();
  assert.match(env.cartButton(env.item, { cart: [env.item] }, 0), /disabled/);
});

test('유효한 추천의 담기 요청은 대상과 추가 제외 조건을 작업 탭에 전달한다', async () => {
  const env = panel();
  await env.addToKurlyCart('p1', '만두', 0);
  assert.equal(env.sent.length, 1);
  assert.equal(env.sent[0].type, 'kurly-add-cart');
  assert.equal(env.sent[0].context.excluded_allergens[0], '우유');
  assert.equal(env.sent[0].context.product_id, 'p1');
  assert.equal(env.saved.kac.profileRevision, 'r1');
});

test('상품 작업 탭은 검증이 실패하면 실제 컬리 버튼을 누르지 않는다', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../content.js'), 'utf8');
  for (const ok of [false, true]) {
    let clicks = 0;
    const reported = [];
    const button = { innerText: '장바구니 담기', click() { clicks++; } };
    const sandbox = {
      location: { search: '?kac_add=1', pathname: '/goods/p1' }, URLSearchParams,
      history: { replaceState() {} },
      document: { getElementById() { return null; }, body: { innerText: '장바구니에 상품을 담았습니다' },
        querySelectorAll() { return [button]; }, querySelector() { return null; } },
      setInterval(fn) { queueMicrotask(fn); return 1; }, clearInterval() {}, setTimeout,
      chrome: { runtime: { sendMessage(message, callback) {
        if (message.type === 'kurly-cart-validate') callback({ ok, reason: '차단 시험' });
        else reported.push(message);
      } } },
    };
    vm.runInNewContext(source, sandbox);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(clicks, ok ? 1 : 0);
    assert.equal(reported[0].ok, ok);
  }
});
