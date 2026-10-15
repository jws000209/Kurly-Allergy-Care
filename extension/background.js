// 컬리 페이지(https)에서 로컬 서버(http)를 직접 부르면 막힐 수 있어, API 호출은 여기서 대신한다.
const API_BASE = "http://127.0.0.1:8000";

// 컬리 장바구니 담기: 상품 페이지를 뒤에서 열고, 그 탭의 content script가 '장바구니 담기'를 누른다.
// 서비스 워커가 중간에 내려가도 이어지도록 작업 목록은 storage.session에 둔다.
async function startAddToCart(productId, openerTabId, context) {
  if (!context || context.product_id !== productId || !context.profileRevision) throw new Error("추천을 다시 받아 주세요.");
  const tab = await chrome.tabs.create({ url: `https://www.kurly.com/goods/${productId}?kac_add=1`, active: false });
  const { jobs = {} } = await chrome.storage.session.get("jobs");
  jobs[tab.id] = { productId, openerTabId, context };
  await chrome.storage.session.set({ jobs });
}

async function validateBeforeCart(tabId) {
  const { jobs = {} } = await chrome.storage.session.get("jobs");
  const context = jobs[tabId]?.context;
  if (!context) throw new Error("담기 요청이 만료되었어요. 다시 추천받아 주세요.");
  const assertCurrent = async () => {
    const { kac } = await chrome.storage.local.get("kac");
    const selected = (kac?.selected || []).slice().sort((a, b) => a - b);
    if (kac?.user?.id !== context.user_id || kac?.profileRevision !== context.profileRevision ||
        JSON.stringify(selected) !== JSON.stringify(context.selected.slice().sort((a, b) => a - b))) {
      throw new Error("프로필이 변경되었어요. 다시 추천받아 주세요.");
    }
  };
  await assertCurrent();
  const response = await fetch(API_BASE + "/api/cart/validate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: context.user_id, member_ids: context.member_ids,
      product_id: context.product_id, excluded_allergens: context.excluded_allergens }),
  });
  const verdict = await response.json();
  if (!response.ok) throw new Error(typeof verdict.detail === "string" ? verdict.detail : "담기 검증에 실패했어요.");
  if (verdict.status !== "PASS") throw new Error(`현재 프로필로 담을 수 없어요: ${verdict.reason}`);
  // 검증 응답을 기다리는 동안에도 선택이 달라질 수 있다.
  await assertCurrent();
  return { ok: true };
}

async function finishAddToCart(tabId, ok, reason, extra) {
  const { jobs = {} } = await chrome.storage.session.get("jobs");
  const job = jobs[tabId];
  if (!job) return;
  delete jobs[tabId];
  await chrome.storage.session.set({ jobs });
  chrome.tabs.sendMessage(job.openerTabId, { type: "kurly-add-result", productId: job.productId, ok, reason, ...extra }).catch(() => {});
  // 성공하면 작업 탭을 닫고, 실패하면 사용자가 직접 처리할 수 있게 앞으로 띄운다.
  if (ok) chrome.tabs.remove(tabId);
  else chrome.tabs.update(tabId, { active: true });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "kurly-add-cart") {
    startAddToCart(message.productId, sender.tab.id, message.context)
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (message.type === "kurly-cart-validate") {
    validateBeforeCart(sender.tab.id)
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, reason: error.message || "담기 검증에 실패했어요." }));
    return true;
  }
  if (message.type === "kurly-add-result") {
    finishAddToCart(sender.tab.id, message.ok, message.reason, { minEa: message.minEa, qty: message.qty });
    return;
  }
  if (message.type !== "api") return;
  fetch(API_BASE + message.path, {
    method: message.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: message.body ? JSON.stringify(message.body) : undefined,
  })
    .then(async (response) => {
      const data = await response.json().catch(() => null);
      if (response.ok) return sendResponse({ ok: true, data });
      const detail = data && data.detail;
      sendResponse({ ok: false, error: typeof detail === "string" ? detail : `요청 실패 (HTTP ${response.status})` });
    })
    .catch(() =>
      sendResponse({ ok: false, error: "백엔드 서버에 연결할 수 없어요. 서버(127.0.0.1:8000)가 켜져 있는지 확인해 주세요." })
    );
  return true; // 비동기 응답
});
