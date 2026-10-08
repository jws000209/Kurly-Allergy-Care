// Kurly Allergy Care AX — 컬리 페이지 위에 챗봇 패널을 얹는 content script.
// 컬리 스타일과 섞이지 않도록 Shadow DOM 안에 그린다.
(() => {
  if (document.getElementById("kac-host")) return;

  // ---------- 컬리 장바구니 담기 작업 탭 ----------
  // 패널에서 '장바구니에 추가'를 누르면 background가 상품 페이지를 ?kac_add=1 로 뒤에서 연다.
  // 이 탭에서는 패널을 띄우지 않고, 컬리의 '장바구니 담기' 버튼을 대신 눌러 결과만 알린다.
  // 컬리 자체 버튼을 쓰므로 로그인 회원·비회원 장바구니 모두 컬리가 알아서 처리한다.
  if (new URLSearchParams(location.search).get("kac_add") === "1") {
    history.replaceState(null, "", location.pathname); // 새로고침해도 다시 담기지 않게
    const report = (ok, reason, extra = {}) => chrome.runtime.sendMessage({ type: "kurly-add-result", ok, reason, ...extra });
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    // 컬리 상품 데이터(__NEXT_DATA__)의 최소 구매 수량. 상품 페이지는 수량 1로 열려, 2개부터 파는 상품은
    // 그대로 누르면 "최소 구매 수량은 2개 입니다." 경고만 뜨고 담기지 않는다.
    const readMinEa = () => {
      try {
        const data = JSON.parse(document.getElementById("__NEXT_DATA__").textContent);
        return Math.max(1, Number(data.props.pageProps.product.minEa) || 1);
      } catch (error) {
        return 1;
      }
    };
    const waitFor = (find, timeout) =>
      new Promise((resolve) => {
        const started = Date.now();
        const timer = setInterval(() => {
          const found = find();
          if (found || Date.now() - started > timeout) {
            clearInterval(timer);
            resolve(found || null);
          }
        }, 300);
      });
    (async () => {
      const button = await waitFor(
        () => [...document.querySelectorAll("button")].find((b) => b.innerText.trim() === "장바구니 담기"), 15000);
      if (!button) return report(false, "상품 페이지에서 '장바구니 담기' 버튼을 찾지 못했어요. 품절이거나 판매 종료된 상품일 수 있어요.");
      if (document.body.innerText.includes("상품을 선택해주세요")) {
        return report(false, "옵션을 골라야 하는 상품이라 상품 페이지를 열어 두었어요. 옵션을 선택해 직접 담아 주세요.");
      }
      const minEa = readMinEa();
      const plus = document.querySelector('button[aria-label="Stepper plus"]');
      const qty = () => Number(((plus && plus.parentElement.innerText) || "").replace(/[^\d]/g, "")) || 1;
      for (let i = 0; plus && qty() < minEa && i < 20; i++) {
        plus.click();
        await sleep(200);
      }
      if (qty() < minEa) {
        return report(false, `최소 ${minEa}개부터 살 수 있는 상품인데 수량을 맞추지 못했어요. 열어 둔 상품 페이지에서 수량을 ${minEa}개로 바꿔 담아 주세요.`, { minEa });
      }
      button.click();
      const done = await waitFor(() => {
        const text = document.body.innerText;
        if (text.includes("장바구니에 상품을 담았습니다")) return "ok";
        if (/최소 구매 수량은/.test(text)) return "min";
        return null;
      }, 6000);
      if (done === "min") return report(false, `최소 구매 수량 경고가 떴어요. 열어 둔 상품 페이지에서 수량을 확인해 주세요.`, { minEa });
      report(done === "ok", done === "ok" ? "" : "담기 결과를 확인하지 못했어요. 열어 둔 상품 페이지에서 확인해 주세요.",
        { minEa, qty: qty() });
    })();
    return;
  }

  const KURLY_CART_URL = "https://www.kurly.com/cart";
  // "장바구니 보여줘", "장바구니로 가줘" 같은 말은 서버에 보내지 않고 바로 컬리 장바구니로 이동한다.
  const GO_CART_PATTERN = /장바구니\s*(페이지)?\s*(로|으로|에|를|좀)?\s*(가|이동|보여|열어|확인|들어|봐|볼래)/;
  const goCart = () => { location.href = KURLY_CART_URL; };

  const state = {
    open: false,
    tab: "chat", // chat | profile
    user: null,
    members: [],
    selected: [], // 장보기 대상으로 고른 구성원 id
    allergens: [],
    messages: [], // {role: 'user'|'bot', text, cart?: 추천 상품, profiles?: 검증 기준 프로필}
    added: [], // 컬리 장바구니에 담은 상품 id
    adding: {}, // 상품 id → 'pending' (담는 중)
    threadId: null,
    draft: "",
    busy: false,
    editing: null, // {id, name, allergens: [], strict, relation, order, aliasText, preview: [], showAlias}
    profileOptions: { relations: ["본인", "아빠", "엄마", "아들", "딸", "할아버지", "할머니", "기타"], orders: ["첫째", "둘째", "셋째", "막내"] },
    page: null, // {product, verdict}
    error: "",
  };

  // ---------- helpers ----------
  const api = (path, method, body) =>
    new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "api", path, method, body }, (response) => {
        if (chrome.runtime.lastError || !response) return reject(new Error("확장 프로그램을 새로고침해 주세요."));
        response.ok ? resolve(response.data) : reject(new Error(response.error));
      });
    });

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const won = (n) => `${Number(n || 0).toLocaleString("ko-KR")}원`;
  const newThread = () => `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  const persist = () =>
    chrome.storage.local.set({
      kac: { user: state.user, selected: state.selected, messages: state.messages.slice(-40), added: state.added, threadId: state.threadId },
    });

  // ---------- 컬리 로그인 정보 읽기 ----------
  // 컬리 상단 메뉴는 로그인하면 "OOO 님", 아니면 "로그인"을 보여 준다. 클래스명이 해시라 글자로 찾는다.
  function readKurlySession() {
    // 10/07: 컬리가 '고객센터'를 자기 드롭다운 칸으로 감싸, 바로 위 칸에는 '고객센터'만 있다.
    // 그래서 위로 몇 칸 올라가며 "OOO 님"이나 "로그인"이 보이는 첫 칸을 상단 메뉴로 본다.
    const help = [...document.querySelectorAll("a")].find((a) => a.textContent.trim() === "고객센터");
    let menu = help;
    for (let i = 0; menu && i < 4; i++) {
      menu = menu.parentElement;
      const text = menu ? menu.innerText : "";
      const match = text.match(/([^\s/|]+)\s*님/);
      if (match) return { name: match[1] };
      if (/(^|\n)\s*로그인\s*($|\n)/.test(text)) return { loggedOut: true };
    }
    return null; // 상단 메뉴가 없는 화면: 판단 보류
  }

  let syncing = false;
  let retryAt = 0;
  async function syncKurlyLogin() {
    const session = readKurlySession();
    if (!session || syncing || Date.now() < retryAt) return;
    if (session.loggedOut) {
      if (state.user) logout();
      return;
    }
    if (state.user && state.user.name === session.name) return;
    syncing = true;
    try {
      await login(session.name);
    } catch (error) {
      retryAt = Date.now() + 10000; // 서버가 꺼져 있을 때 매초 재시도하지 않는다
      fail(error.message);
    }
    syncing = false;
  }

  // ---------- 컬리 상품 페이지 읽기 ----------
  function readPageProduct() {
    const match = location.pathname.match(/^\/goods\/(\d+)/);
    if (!match) return null;
    let data = null;
    try {
      const product = JSON.parse(document.getElementById("__NEXT_DATA__").textContent).props.pageProps.product;
      if (product && String(product.no) === match[1]) data = product;
    } catch (_) {}
    const fromTable = (label) => {
      for (const dt of document.querySelectorAll("dt")) {
        if (dt.textContent.trim() === label) return dt.nextElementSibling ? dt.nextElementSibling.innerText.trim() : "";
      }
      return "";
    };
    // categoryNames는 ["CATEGORY", "밀키트", "밀키트", "한식 국/탕/찌개류"]처럼 온다. 내부값 'CATEGORY'와 중복은 뺀다.
    const categories = data && Array.isArray(data.categoryNames)
      ? [...new Set(data.categoryNames.filter((c) => typeof c === "string" && c && c !== "CATEGORY"))]
      : [];
    return {
      id: match[1],
      name: (data && data.name) || document.title.replace(/\s*-\s*마켓컬리$/, ""),
      price: data ? data.discountedPrice ?? data.basePrice ?? null : null,
      category: categories.join(">"),
      allergy_label: (data && data.allergy) || fromTable("알레르기정보"),
      url: location.origin + location.pathname,
      image: (data && data.mainImageUrl) || "",
    };
  }

  async function checkPage() {
    const product = readPageProduct();
    if (!product || !state.user) {
      state.page = null;
      return render();
    }
    try {
      const verdict = await api("/api/check", "POST", { user_id: state.user.id, member_ids: state.selected, product });
      api(`/api/users/${state.user.id}/interactions`, "POST", { product_id: product.id, event: "view" }).catch(() => {});
      state.page = { product, verdict };
    } catch (_) {
      state.page = null;
    }
    render();
  }

  // ---------- actions ----------
  async function loadMembers() {
    state.members = await api(`/api/users/${state.user.id}/members`);
    const ids = state.members.map((m) => m.id);
    state.selected = state.selected.filter((id) => ids.includes(id));
  }

  async function login(name) {
    state.user = await api("/api/login", "POST", { name });
    state.threadId = newThread();
    state.messages = [];
    state.added = [];
    await loadMembers();
    if (!state.members.length) state.tab = "profile";
    persist();
    checkPage();
  }

  function logout() {
    Object.assign(state, { user: null, members: [], selected: [], messages: [], added: [], page: null, tab: "chat" });
    persist();
    render();
  }

  async function send(text) {
    text = text.trim();
    if (!text || state.busy) return;
    if (GO_CART_PATTERN.test(text)) {
      state.messages.push({ role: "user", text }, { role: "bot", text: "컬리 장바구니로 이동할게요." });
      state.draft = "";
      persist();
      return goCart();
    }
    state.messages.push({ role: "user", text });
    state.draft = "";
    state.busy = true;
    state.scrollBottom = true;
    render();
    try {
      const result = await api("/api/chat", "POST", {
        user_id: state.user.id, thread_id: state.threadId, message: text, selected_member_ids: state.selected,
      });
      const isCartAction = result.intent.action !== "question";
      state.messages.push({
        role: "bot", text: result.reply,
        cart: isCartAction ? result.cart : null, profiles: result.constraints.profiles || [],
        after: isCartAction ? result.notes_after || [] : [],
      });
    } catch (error) {
      state.messages.push({ role: "bot", text: error.message });
    }
    state.busy = false;
    state.scrollBottom = true;
    persist();
    render();
    const draft = root.getElementById("draft");
    if (draft) draft.focus();
  }

  async function saveMember() {
    const { id, name, allergens, strict, relation, order, aliasText } = state.editing;
    if (!name.trim()) return fail("프로필 이름을 입력해 주세요.");
    const aliases = aliasText.split(/[,\n]/).map((a) => a.trim()).filter(Boolean);
    const body = { name: name.trim(), allergens, strict, relation, order, aliases };
    const saved = id ? await api(`/api/members/${id}`, "PUT", body) : await api(`/api/users/${state.user.id}/members`, "POST", body);
    if (!id) state.selected.push(saved.id);
    state.editing = null;
    await loadMembers();
    persist();
    checkPage();
  }

  // 관계·순서로 자동 생성될 '부르는 이름'을 서버에서 받아 미리 보여 준다 (입력란 포커스를 잃지 않게 그 부분만 갱신)
  async function refreshAliasPreview() {
    const e = state.editing;
    if (!e || !e.showAlias) return;
    try {
      e.preview = e.name.trim() ? await api("/api/aliases/preview", "POST", { name: e.name, relation: e.relation, order: e.order }) : [];
    } catch (_) {
      e.preview = [];
    }
    const box = root.getElementById("alias-preview");
    if (box) box.innerHTML = aliasChips(e.preview);
  }

  const aliasChips = (list) =>
    list && list.length
      ? list.map((a) => `<span class="alias-chip">${esc(a)}</span>`).join("")
      : '<span class="meta">이름을 입력하고 관계를 고르면 여기에 보여요.</span>';

  // 확장이 켜질 때 서버가 꺼져 있었으면 알레르겐 목록이 비어 있다. 프로필 화면을 열 때 다시 받아 온다.
  async function ensureAllergens() {
    if (state.allergens.length) return;
    try {
      state.allergens = await api("/api/allergens");
      state.profileOptions = await api("/api/profile-options").catch(() => state.profileOptions);
      state.error = "";
    } catch (_) {}
    render();
  }

  function editMember(member) {
    ensureAllergens();
    state.editing = member
      ? { id: member.id, name: member.name, allergens: [...member.allergens], strict: member.strict,
          relation: member.relation || "", order: member.order || "", aliasText: (member.aliases || []).join(", "),
          preview: member.auto_aliases || [], showAlias: false }
      : { id: null, name: "", allergens: [], strict: false, relation: "", order: "", aliasText: "", preview: [], showAlias: false };
    render();
  }

  async function deleteMember(id) {
    await api(`/api/members/${id}`, "DELETE");
    state.editing = null;
    await loadMembers();
    persist();
    checkPage();
  }

  function fail(message) {
    state.error = message;
    render();
  }

  // ---------- views ----------
  const STATUS = {
    PASS: { label: "PASS", cls: "pass" },
    BLOCK: { label: "BLOCK", cls: "block" },
    UNVERIFIED: { label: "정보 확인 필요", cls: "unverified" },
  };

  // 컬리 이미지 서버의 작은 크기(목록용 360x468)를 쓴다. 다른 형식의 주소는 그대로 둔다.
  const thumbUrl = (url) => url.replace("%3E720x%3E936/cropcenter/720x936", "%3E360x%3E468/cropcenter/360x468");

  // button(item, i): 행 오른쪽에 붙일 버튼 HTML
  function itemRows(items, button) {
    return items
      .map(
        (item, i) => `${
          // 묶음 추천('점심이랑 과자')이면 묶음이 바뀔 때마다 제목을 단다. 번호는 전체 순서 그대로 ('2번 빼줘'용)
          item.group && (i === 0 || items[i - 1].group !== item.group) ? `<li class="group-head">${esc(item.group)}</li>` : ""
        }
      <li class="item">
        <span class="no">${i + 1}</span>
        ${item.image ? `<a class="thumb" href="${esc(item.url)}" target="_blank" rel="noopener"><img src="${esc(thumbUrl(item.image))}" alt=""></a>` : ""}
        <div class="item-body">
          <a class="item-name" href="${esc(item.url)}" target="_blank" rel="noopener" title="${esc(item.name)}">${esc(item.name)}</a>
        </div>
        <div class="side">${item.price ? `<span class="price">${esc(won(item.price))}</span>` : ""}${button(item, i)}</div>
        ${item.cross_matched.length ? `<div class="warn item-warn">주의: ${esc(item.cross_matched.join(", "))} 혼입 가능 표시</div>` : ""}
      </li>`
      )
      .join("");
  }

  // 컬리 장바구니에 담을 수 있는 건 컬리 페이지에서 수집한 실제 상품뿐이다. 시연용 가상 상품은 검색으로 안내한다.
  function cartButton(item) {
    if (item.source === "mock") {
      return `<a class="mini-link" href="${esc(item.url)}" target="_blank" rel="noopener">컬리에서 찾기</a>`;
    }
    if (item.needs_option) {
      // 옵션(택1 등)을 골라야 하는 상품은 자동으로 담을 수 없어 상품 페이지에서 고르게 한다.
      return `<a class="mini-link" href="${esc(item.url)}" target="_blank" rel="noopener" title="옵션을 골라 직접 담아 주세요">옵션 고르기</a>`;
    }
    if (state.adding[item.id]) return '<button class="mini" disabled>담는 중…</button>';
    if (state.added.includes(item.id)) {
      return `<button class="mini" data-action="add-cart" data-id="${esc(item.id)}" data-name="${esc(item.name)}" title="한 번 더 담기">담김 ✓</button>`;
    }
    return `<button class="mini primary" data-action="add-cart" data-id="${esc(item.id)}" data-name="${esc(item.name)}">장바구니에 추가</button>`;
  }

  function addToKurlyCart(id, name) {
    state.adding[id] = name || "상품";
    render();
    chrome.runtime.sendMessage({ type: "kurly-add-cart", productId: id }, (response) => {
      if (chrome.runtime.lastError || !response || !response.ok) {
        delete state.adding[id];
        fail("장바구니 담기를 시작하지 못했어요. 확장 프로그램을 새로고침해 주세요.");
      }
    });
    // 작업 탭이 끝내 응답하지 않을 때를 대비한다.
    setTimeout(() => {
      if (!state.adding[id]) return;
      delete state.adding[id];
      fail("장바구니 담기 결과를 받지 못했어요. 컬리 장바구니에서 확인해 주세요.");
    }, 30000);
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message.type !== "kurly-add-result" || !state.adding[message.productId]) return;
    const name = state.adding[message.productId];
    delete state.adding[message.productId];
    if (message.minEa) {
      // 수집 데이터에 없던 최소 구매 수량이면 서버 상품 DB를 고쳐 다음 추천부터 가격·예산을 맞춘다
      api(`/api/products/${encodeURIComponent(message.productId)}/min-ea`, "POST", { min_ea: message.minEa }).catch(() => {});
    }
    if (message.ok) {
      if (state.user) api(`/api/users/${state.user.id}/interactions`, "POST", {
        product_id: message.productId, event: "cart"
      }).catch(() => {});
      if (!state.added.includes(message.productId)) state.added.push(message.productId);
      const count = message.qty > 1 ? ` ${message.qty}개를` : "을(를)";
      const why = message.minEa > 1 ? ` (최소 구매 수량 ${message.minEa}개)` : "";
      state.messages.push({ role: "bot", text: `'${name}'${count} 컬리 장바구니에 담았어요.${why}`, goCart: true });
      state.scrollBottom = true;
      persist();
      render();
    } else {
      fail(message.reason);
    }
  });

  function pageBanner() {
    if (!state.page) return "";
    const { product, verdict } = state.page;
    if (!state.selected.length) {
      return `<div class="banner unverified"><b>이 상품</b> ${esc(product.name)}<br>프로필을 선택하면 알레르기 판정을 보여 드려요.</div>`;
    }
    const status = STATUS[verdict.status];
    return `<div class="banner ${status.cls}">
      <span class="badge ${status.cls}">${status.label}</span> <b>${esc(product.name)}</b><br>
      ${esc(verdict.profiles.join("·"))} 기준 · ${esc(verdict.reason)}
    </div>`;
  }

  function chatView() {
    const messages = state.messages
      .map(
        (m) => `<div class="msg ${m.role}">${esc(m.text).replace(/\n/g, "<br>")}${
          m.goCart ? '<div><button class="mini primary go-cart" data-action="go-cart">장바구니 보기</button></div>' : ""
        }${
          m.cart && m.cart.length
            ? `<div class="recs-title">추천 상품</div><ol class="items">${itemRows(m.cart, cartButton)}</ol>${
                (m.after || []).map((n) => `<p class="after-note">${esc(n)}</p>`).join("")}`
            : ""
        }</div>`
      )
      .join("");
    const empty = `<div class="msg bot">안녕하세요, ${esc(state.user.name)}님. 위에서 장보기 대상을 고르고 원하는 조건을 말씀해 주세요. 추천 상품의 '장바구니에 추가'를 누르면 컬리 장바구니에 바로 담기고, '장바구니 보여줘'라고 말하면 컬리 장바구니로 이동해요.</div>`;
    return `
      ${pageBanner()}
      <div class="messages" id="messages">${messages || empty}${state.busy ? '<div class="msg bot">조건을 확인하고 있어요…</div>' : ""}</div>
      <div class="composer">
        <textarea id="draft" rows="1" placeholder="예) 첫째 간식 2만원 이내 5개">${esc(state.draft)}</textarea>
        <button class="primary" data-action="send" ${state.busy ? "disabled" : ""}>전송</button>
      </div>`;
  }

  // 프로필 이름과 관리 알레르겐 사이의 '관계·부르는 이름' 영역. 기본은 접혀 있고 토글로 펼친다.
  function aliasSection(e) {
    const userAliases = e.aliasText.split(/[,\n]/).filter((a) => a.trim()).length;
    const summary = [e.relation, e.order].filter(Boolean).join(" · ") || "설정 안 함";
    const checks = (list, key, current) =>
      list.map((v) => `<label><input type="checkbox" data-${key}="${esc(v)}" ${current === v ? "checked" : ""}>${esc(v)}</label>`).join("");
    return `<div class="alias-box">
      <button class="alias-toggle" data-action="toggle-alias" aria-expanded="${e.showAlias}">
        <span>${e.showAlias ? "▾" : "▸"} 관계·부르는 이름 <span class="meta">(선택)</span></span>
        <span class="meta">${esc(summary)}${userAliases ? ` · 직접 별칭 ${userAliases}개` : ""}</span>
      </button>
      ${e.showAlias ? `<div class="alias-body">
        <div class="sub">관계</div>
        <div class="checks">${checks(state.profileOptions.relations, "relation", e.relation)}</div>
        <div class="sub">순서 <span class="meta">(자녀일 때)</span></div>
        <div class="checks four">${checks(state.profileOptions.orders, "order", e.order)}</div>
        <div class="sub">자동으로 알아듣는 이름</div>
        <div id="alias-preview" class="alias-chips">${aliasChips(e.preview)}</div>
        <label class="sub">직접 추가 <span class="meta">(쉼표로 구분)</span>
          <input id="member-aliases" value="${esc(e.aliasText)}" placeholder="예) 민준, 준이" maxlength="120"></label>
        <p class="meta">챗봇에 "큰애", "남편"처럼 말해도 이 프로필로 알아들어요. 한 이름이 여러 프로필에 겹치거나 등록되지 않은 사람이면 추천 전에 되물어요.</p>
      </div>` : ""}
    </div>`;
  }

  function profileView() {
    if (state.editing) {
      const e = state.editing;
      return `<div class="pane">
        <label class="field">프로필 이름<input id="member-name" value="${esc(e.name)}" placeholder="아빠, 엄마, 첫째 아들…" maxlength="20"></label>
        ${aliasSection(e)}
        <div class="field">관리 알레르겐 (국내 표시대상 19종)</div>
        ${state.allergens.length ? "" : `<p class="warn">서버에 연결되지 않아 알레르겐 목록을 불러오지 못했어요.
          backend 폴더의 run.bat을 실행한 뒤 <button data-action="reload-allergens">다시 불러오기</button></p>`}
        <div class="checks">${state.allergens
          .map((a) => `<label><input type="checkbox" data-allergen="${esc(a)}" ${e.allergens.includes(a) ? "checked" : ""}>${esc(a)}</label>`)
          .join("")}</div>
        <label class="strict"><input type="checkbox" id="member-strict" ${e.strict ? "checked" : ""}>
          '혼입 가능' 표시 상품도 제외 (엄격 모드)</label>
        <div class="row">
          <button class="primary" data-action="save-member">저장</button>
          <button data-action="cancel-edit">취소</button>
          ${e.id ? `<button class="danger" data-action="delete-member" data-id="${e.id}">삭제</button>` : ""}
        </div>
      </div>`;
    }
    const list = state.members
      .map(
        (m) => `<li class="member">
        <div><b>${esc(m.name)}</b>${m.strict ? ' <span class="tag">엄격</span>' : ""}
          <div class="meta">${m.allergens.length ? esc(m.allergens.join(", ")) : "등록된 알레르겐 없음"}</div>
          ${(m.relation || m.order || (m.aliases || []).length)
            ? `<div class="meta alias-line">부르는 이름: ${esc([...(m.auto_aliases || []), ...(m.aliases || [])].slice(0, 6).join(", "))}${
                (m.auto_aliases || []).length + (m.aliases || []).length > 6 ? " …" : ""}</div>`
            : ""}</div>
        <button data-action="edit-member" data-id="${m.id}">수정</button>
      </li>`
      )
      .join("");
    return `<div class="pane">
      <ul class="members">${list || '<li class="meta">아직 등록된 가족 구성원이 없어요.</li>'}</ul>
      <button class="primary wide" data-action="add-member">+ 구성원 추가</button>
    </div>`;
  }

  function loginView() {
    return `<div class="pane">
      <p><b>Kurly Allergy Care</b><br>가족 알레르기 프로필에 맞춰 상품을 추천해 드려요.</p>
      <p>마켓컬리에 로그인하면 자동으로 시작됩니다.</p>
      <a class="button" href="https://www.kurly.com/member/login">마켓컬리 로그인</a>
    </div>`;
  }

  function render() {
    const launcherStatus = state.page && state.selected.length ? STATUS[state.page.verdict.status] : null;
    let body;
    if (!state.user) body = loginView();
    else {
      const chips = state.members
        .map((m) => `<button class="chip ${state.selected.includes(m.id) ? "on" : ""}" data-action="toggle-member" data-id="${m.id}">${esc(m.name)}</button>`)
        .join("");
      const tabs = [["chat", "챗봇"], ["profile", "가족 프로필"]]
        .map(([id, label]) => `<button class="tab ${state.tab === id ? "on" : ""}" data-action="tab" data-tab="${id}">${label}</button>`)
        .join("");
      const view = state.tab === "profile" ? profileView() : chatView();
      body = `
        <div class="chips"><span class="meta">장보기 대상</span>${chips || '<span class="meta">프로필을 먼저 추가해 주세요</span>'}</div>
        <div class="tabs">${tabs}</div>
        ${view}`;
    }
    const scrollTop = (root.getElementById("messages") || {}).scrollTop;
    view.innerHTML = `
      <button class="launcher" data-action="toggle" aria-label="Kurly Allergy Care 열기">
        ${launcherStatus ? `<span class="badge ${launcherStatus.cls}">${launcherStatus.label}</span>` : ""}알레르기 케어
      </button>
      <section class="panel" ${state.open ? "" : "hidden"}>
        <header><b>Kurly Allergy Care</b>
          <span>${state.user ? `<button class="link" data-action="reset">새 대화</button>` : ""}<button class="link" data-action="toggle" aria-label="닫기">✕</button></span>
        </header>
        ${state.error ? `<div class="error">${esc(state.error)}</div>` : ""}
        ${body}
      </section>`;
    state.error = "";
    const messages = root.getElementById("messages");
    // 새 메시지가 왔을 때만 맨 아래로 내리고, 그 밖에는 보던 위치를 유지한다.
    if (messages) messages.scrollTop = state.scrollBottom || scrollTop === undefined ? messages.scrollHeight : scrollTop;
    state.scrollBottom = false;
  }

  // ---------- events ----------
  const handlers = {
    toggle: () => { state.open = !state.open; render(); },
    tab: (el) => { state.tab = el.dataset.tab; state.editing = null; render(); },
    reset: () => { state.threadId = newThread(); state.messages = []; persist(); render(); },
    "add-cart": (el) => addToKurlyCart(el.dataset.id, el.dataset.name),
    "go-cart": goCart,
    send: () => send(state.draft),
    "toggle-member": (el) => {
      const id = Number(el.dataset.id);
      state.selected = state.selected.includes(id) ? state.selected.filter((s) => s !== id) : [...state.selected, id];
      persist();
      checkPage();
    },
    "add-member": () => editMember(null),
    "reload-allergens": () => ensureAllergens(),
    "edit-member": (el) => editMember(state.members.find((m) => m.id === Number(el.dataset.id))),
    "toggle-alias": () => {
      state.editing.showAlias = !state.editing.showAlias;
      render();
      refreshAliasPreview();
    },
    "cancel-edit": () => { state.editing = null; render(); },
    "save-member": saveMember,
    "delete-member": (el) => deleteMember(Number(el.dataset.id)),
  };

  const host = document.createElement("div");
  host.id = "kac-host";
  const root = host.attachShadow({ mode: "open" });
  const view = document.createElement("div");
  root.appendChild(view);

  root.addEventListener("click", (event) => {
    const el = event.target.closest("[data-action]");
    if (!el) return;
    Promise.resolve(handlers[el.dataset.action](el)).catch((error) => fail(error.message));
  });
  root.addEventListener("input", (event) => {
    const el = event.target;
    if (el.id === "draft") state.draft = el.value;
    else if (el.id === "member-name") state.editing.name = el.value;
    else if (el.id === "member-strict") state.editing.strict = el.checked;
    else if (el.id === "member-aliases") state.editing.aliasText = el.value;
    else if (el.dataset.relation !== undefined || el.dataset.order !== undefined) {
      // 체크박스지만 하나만 고를 수 있다 (다시 누르면 해제)
      const key = el.dataset.relation !== undefined ? "relation" : "order";
      state.editing[key] = el.checked ? el.dataset[key] : "";
      render();
      refreshAliasPreview();
    }
    else if (el.dataset.allergen) {
      const list = state.editing.allergens.filter((a) => a !== el.dataset.allergen);
      state.editing.allergens = el.checked ? [...list, el.dataset.allergen] : list;
    }
  });
  root.addEventListener("change", (event) => {
    if (event.target.id === "member-name") refreshAliasPreview(); // 이름을 다 쓴 뒤 자동 별칭 갱신
  });
  root.addEventListener("keydown", (event) => {
    event.stopPropagation(); // 컬리 페이지 단축키와 충돌 방지
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return; // 한글 조합 중 Enter 무시
    if (event.target.id === "draft") { event.preventDefault(); send(state.draft); }
  });

  // 페이지 CSP의 영향을 받지 않도록 <style> 대신 constructable stylesheet를 쓴다.
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(`
    :host { all: initial; }
    * { box-sizing: border-box; font-family: "Noto Sans KR", "Malgun Gothic", sans-serif; }
    [hidden] { display: none !important; }
    button { cursor: pointer; font-size: 13px; border: 1px solid #ddd; background: #fff; color: #333; border-radius: 6px; padding: 6px 10px; }
    button:disabled { opacity: .5; cursor: default; }
    button.primary { background: #5f0080; border-color: #5f0080; color: #fff; }
    button.danger { color: #c62828; border-color: #e8b4b4; }
    button.link { border: 0; background: none; color: #fff; padding: 4px 6px; }
    button.wide, a.button { width: 100%; margin-top: 8px; padding: 10px; }
    a.button { display: block; text-align: center; text-decoration: none; background: #5f0080; color: #fff; border-radius: 6px; font-size: 13px; }
    button.mini, a.mini-link { font-size: 11px; padding: 3px 8px; white-space: nowrap; }
    a.mini-link { color: #5f0080; border: 1px solid #5f0080; border-radius: 6px; text-decoration: none; }
    .side { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
    .go-cart { margin-top: 6px; }
    .group-head { list-style: none; font-size: 12px; font-weight: 700; color: #5f0080; margin-top: 4px; }
    .after-note { margin: 8px 2px 0; font-size: 12px; color: #666; line-height: 1.5; }
    .recs-title { margin-top: 8px; font-weight: 700; color: #5f0080; }
    .launcher { position: fixed; right: 24px; bottom: 24px; z-index: 2147483646; background: #5f0080; color: #fff; border: 0;
      border-radius: 24px; padding: 12px 18px; font-size: 14px; font-weight: 700; box-shadow: 0 4px 14px rgba(0,0,0,.25); }
    .launcher .badge { margin-right: 6px; }
    .panel { position: fixed; right: 24px; bottom: 80px; z-index: 2147483647; width: 380px; height: min(640px, calc(100vh - 110px));
      background: #fff; color: #333; font-size: 13px; line-height: 1.5; border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,.25);
      display: flex; flex-direction: column; overflow: hidden; }
    header { background: #5f0080; color: #fff; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; font-size: 15px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 8px 12px; border-bottom: 1px solid #eee; }
    .chip { border-radius: 14px; padding: 3px 10px; }
    .chip.on { background: #5f0080; border-color: #5f0080; color: #fff; }
    .tabs { display: flex; border-bottom: 1px solid #eee; }
    .tab { flex: 1; border: 0; border-radius: 0; padding: 9px 0; color: #777; border-bottom: 2px solid transparent; }
    .tab.on { color: #5f0080; font-weight: 700; border-bottom-color: #5f0080; }
    .pane { padding: 14px; overflow-y: auto; flex: 1; }
    .messages { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; background: #faf7fb; }
    .msg { max-width: 92%; padding: 8px 11px; border-radius: 12px; word-break: keep-all; overflow-wrap: anywhere; }
    .msg.user { align-self: flex-end; background: #5f0080; color: #fff; }
    .msg.bot { align-self: flex-start; background: #fff; border: 1px solid #eee; }
    .composer { display: flex; gap: 6px; padding: 8px 12px 12px; }
    textarea, input:not([type=checkbox]) { flex: 1; width: 100%; font-size: 13px; padding: 8px; border: 1px solid #ccc; border-radius: 6px; resize: none; color: #333; background: #fff; }
    .items { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    /* 번호 · 사진 · 상품명 · (가격+버튼) 한 줄, '주의'는 아래에 전체 너비로 — 가운데 칸이 세로로 길어지지 않게 */
    .item { display: grid; grid-template-columns: auto auto minmax(0, 1fr) auto; column-gap: 8px; row-gap: 6px;
            align-items: start; padding: 8px; border: 1px solid #eee; border-radius: 8px; background: #fff; }
    .item-body { min-width: 0; }
    .item-name { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.35; }
    .price { font-size: 15px; font-weight: 800; color: #111; white-space: nowrap; }
    .item-warn { grid-column: 1 / -1; line-height: 1.4; }
    .thumb { flex: none; width: 56px; height: 72px; border-radius: 6px; overflow: hidden; background: #f4f4f4; }
    .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .item a { color: #333; font-weight: 700; text-decoration: none; }
    .item a:hover { text-decoration: underline; }
    .no { flex: none; width: 20px; height: 20px; border-radius: 50%; background: #f1e6f5; color: #5f0080; font-weight: 700; font-size: 12px; text-align: center; line-height: 20px; }
    .meta { color: #888; font-size: 12px; }
    .warn { color: #b26a00; font-size: 12px; }
    .badge { flex: none; display: inline-block; font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 10px; color: #fff; }
    .badge.pass { background: #2e7d32; } .badge.block { background: #c62828; } .badge.unverified { background: #8a6d00; }
    .banner { padding: 8px 12px; font-size: 12px; border-bottom: 1px solid #eee; }
    .banner.pass { background: #edf7ee; } .banner.block { background: #fdecea; } .banner.unverified { background: #fff8e1; }
    .error { background: #fdecea; color: #c62828; padding: 8px 12px; }
    .field { display: block; font-weight: 700; margin-bottom: 10px; }
    .field input { margin-top: 4px; font-weight: 400; }
    .checks { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 12px; }
    .checks label, .strict { display: flex; align-items: center; gap: 4px; }
    .strict { margin-bottom: 12px; }
    .alias-box { border: 1px solid #e6dcec; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }
    .alias-toggle { width: 100%; border: 0; border-radius: 0; background: #faf7fb; display: flex; justify-content: space-between;
      align-items: center; gap: 8px; padding: 8px 10px; font-weight: 700; color: #333; text-align: left; }
    .alias-body { padding: 4px 10px 10px; }
    .alias-body .sub { display: block; font-weight: 700; font-size: 12px; margin: 8px 0 4px; }
    .alias-body .sub input { margin-top: 4px; font-weight: 400; }
    .alias-body .checks { margin-bottom: 4px; }
    .checks.four { grid-template-columns: repeat(4, 1fr); }
    .alias-chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .alias-chip { font-size: 11px; background: #f1e6f5; color: #5f0080; border-radius: 10px; padding: 1px 8px; }
    .alias-line { margin-top: 2px; }
    .row { display: flex; gap: 6px; }
    .members { list-style: none; margin: 0; padding: 0; }
    .member { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid #eee; }
    .tag { font-size: 11px; color: #5f0080; border: 1px solid #5f0080; border-radius: 8px; padding: 0 5px; }
    @media (max-width: 480px) { .panel { right: 8px; left: 8px; width: auto; } }
  `);
  root.adoptedStyleSheets = [sheet];

  // ---------- 컬리 장바구니와 '담김' 표시 맞추기 ----------
  // 패널의 '담김 ✓'은 담기 성공 기록이라, 컬리 장바구니에서 빼도 그대로 남았다.
  // 컬리 장바구니 화면(/cart)이 열려 있으면 거기 보이는 상품 목록으로 담김 표시를 맞춘다 (빼면 바로 풀린다).
  function syncCartPage() {
    if (location.pathname !== "/cart") return;
    const total = document.body.innerText.match(/전체선택\s*\d+\s*\/\s*(\d+)/);
    if (!total) return; // 목록을 아직 그리는 중
    const ids = [...new Set([...document.querySelectorAll('a[href*="/goods/"]')]
      .map((a) => (a.getAttribute("href").match(/\/goods\/(\d+)/) || [])[1]).filter(Boolean))];
    if (Number(total[1]) > 0 && !ids.length) return;
    if (ids.length === state.added.length && ids.every((id) => state.added.includes(id))) return;
    state.added = ids;
    persist();
    render();
  }
  // 다른 컬리 탭(장바구니 화면)에서 바뀐 담김 표시를 이 탭에도 반영한다
  chrome.storage.onChanged.addListener((changes, area) => {
    const added = area === "local" && changes.kac && changes.kac.newValue && changes.kac.newValue.added;
    if (!added || JSON.stringify(added) === JSON.stringify(state.added)) return;
    state.added = added;
    render();
  });

  // ---------- start ----------
  async function start() {
    document.documentElement.appendChild(host);
    const saved = (await chrome.storage.local.get("kac")).kac || {};
    Object.assign(state, {
      user: saved.user || null, selected: saved.selected || [], messages: saved.messages || [],
      added: saved.added || [], threadId: saved.threadId || newThread(),
    });
    render();
    try {
      state.allergens = await api("/api/allergens");
      state.profileOptions = await api("/api/profile-options").catch(() => state.profileOptions);
      if (state.user) await loadMembers();
    } catch (error) {
      state.error = error.message;
    }
    await syncKurlyLogin();
    checkPage();

    // 컬리는 SPA라 주소만 바뀌는 이동이 있다. 상품 정보가 그려질 시간을 두고 다시 판정한다.
    let lastPath = location.pathname;
    setInterval(() => {
      syncKurlyLogin(); // 컬리 로그인·로그아웃을 따라간다
      syncCartPage();
      if (location.pathname === lastPath) return;
      lastPath = location.pathname;
      setTimeout(checkPage, 1500);
    }, 1000);
  }

  start();
})();
