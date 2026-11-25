/* 서울현대요양병원(25-159) 정류장 도착정보 앱 */
(() => {
  "use strict";

  /* 배포한 Cloudflare Worker 주소. 비워 두면 첫 실행 때 설정 화면에서 입력한다. */
  const DEFAULT_API = "";

  const STOP = { name: "서울현대요양병원", arsId: "25159" };
  const REFRESH_MS = 30000;
  const FETCH_TIMEOUT_MS = 9000;

  /* 정류장 표지판에 적힌 노선과 배차·첫차·막차(서울시 TOPIS 실측값).
     중계 서버 없이 GitHub Pages만으로 써도 이 안내는 그대로 보인다. */
  const STATIC_ROUTES = [
    { route: "340", dir: "강남역", category: "trunk", categoryLabel: "간선", first: "04:30", last: "23:10", term: 7 },
    { route: "3318", dir: "마천동", category: "branch", categoryLabel: "지선", first: "04:30", last: "23:10", term: 12 },
    { route: "3321", dir: "강동구청", category: "branch", categoryLabel: "지선", first: "04:30", last: "23:40", term: 14 },
    { route: "3324", dir: "강동역", category: "branch", categoryLabel: "지선", first: "04:20", last: "23:20", term: 13 },
    { route: "3411", dir: "한티역", category: "branch", categoryLabel: "지선", first: "04:30", last: "23:10", term: 11 },
    { route: "N31", dir: "국민대", category: "night", categoryLabel: "심야", first: "23:30", last: "03:50", term: 38 },
  ];

  const LS = {
    api: "bus25159.api",
    last: "bus25159.last",
    pins: "bus25159.pins",
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    status: $("status"),
    statusText: $("statusText"),
    refresh: $("refreshBtn"),
    banner: $("banner"),
    routes: $("routes"),
    footnote: $("footnote"),
    sheet: $("settingsSheet"),
    apiInput: $("apiInput"),
    sheetHint: $("sheetHint"),
    toast: $("toast"),
    arsBtn: $("arsBtn"),
    copyBtn: $("copyBtn"),
  };

  const state = {
    api: readLS(LS.api) || DEFAULT_API,
    data: null,
    fetchedAt: 0,
    pins: readPins(),
    loading: false,
    error: null,
    timer: null,
    tick: null,
  };

  /* ── 저장소 ─────────────────────────────────────── */
  function readLS(key) {
    try {
      return localStorage.getItem(key) || "";
    } catch (e) {
      return "";
    }
  }

  function writeLS(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      /* 저장 불가(사생활 보호 모드 등) — 조용히 넘어간다 */
    }
  }

  function readPins() {
    try {
      const raw = JSON.parse(readLS(LS.pins) || "[]");
      return Array.isArray(raw) ? raw.filter((x) => typeof x === "string") : [];
    } catch (e) {
      return [];
    }
  }

  function savePins() {
    writeLS(LS.pins, JSON.stringify(state.pins));
  }

  /* ── 유틸 ───────────────────────────────────────── */
  function apiUrl() {
    const base = (state.api || "").trim().replace(/\/+$/, "");
    if (!base) return "";
    return base + "/?arsId=" + encodeURIComponent(STOP.arsId);
  }

  function normApiBase(value) {
    let v = (value || "").trim();
    if (!v) return "";
    if (!/^https?:\/\//i.test(v)) v = "https://" + v;
    return v.replace(/\/+$/, "");
  }

  function timeLabel(ms) {
    const d = new Date(ms);
    return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }

  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.toast.classList.remove("is-on"), 1700);
  }

  function setStatus(kind, text) {
    el.status.dataset.state = kind;
    el.statusText.textContent = text;
  }

  /* ── 도착 시간 표기 ─────────────────────────────── */
  function formatEta(eta, remainingSec) {
    if (!eta) return { main: "정보 없음", sub: "", kind: "none" };
    const st = eta.state || "running";
    if (st === "ended") return { main: "운행종료", sub: "", kind: "ended" };
    if (st === "waiting") return { main: "출발대기", sub: "", kind: "waiting" };
    if (st === "none") return { main: "정보 없음", sub: "", kind: "none" };

    let sec = typeof remainingSec === "number" ? remainingSec : eta.sec;
    if (typeof sec !== "number" || !isFinite(sec)) {
      return { main: eta.msg || "정보 없음", sub: "", kind: "none" };
    }
    if (sec < -60) return { main: "도착", sub: "", kind: "soon" };
    if (sec <= 30) {
      return { main: "곧 도착", sub: eta.stops ? eta.stops + "번째 전" : "", kind: "soon" };
    }
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    const main = m >= 10 ? m + "분" : m + "분 " + String(s).padStart(2, "0") + "초";
    return { main, sub: eta.stops ? "후 · " + eta.stops + "번째 전" : "후", kind: "running" };
  }

  function shortEta(eta, remainingSec) {
    const f = formatEta(eta, remainingSec);
    if (["ended", "none", "waiting"].includes(f.kind)) return f.main;
    if (f.kind === "soon") return "곧 도착";
    return f.main.replace(" ", "") + (f.sub ? " · " + f.sub.replace("후 · ", "") : "");
  }

  /* ── 렌더 ───────────────────────────────────────── */
  function orderedRoutes(routes) {
    const pinned = [];
    const rest = [];
    for (const r of routes) {
      if (state.pins.includes(r.route)) pinned.push(r);
      else rest.push(r);
    }
    pinned.sort((a, b) => state.pins.indexOf(a.route) - state.pins.indexOf(b.route));
    return pinned.concat(rest);
  }

  function routeCard(route, elapsedSec) {
    const li = document.createElement("li");
    li.className = "route";
    li.dataset.route = route.route;
    if (state.pins.includes(route.route)) li.classList.add("is-pinned");
    if ((route.eta1 || {}).state === "ended" && (route.eta2 || {}).state === "ended") li.classList.add("is-ended");

    const rem1 = typeof (route.eta1 || {}).sec === "number" ? route.eta1.sec - elapsedSec : undefined;
    const rem2 = typeof (route.eta2 || {}).sec === "number" ? route.eta2.sec - elapsedSec : undefined;
    const f1 = formatEta(route.eta1, rem1);
    const f2 = shortEta(route.eta2, rem2);

    const badge = document.createElement("div");
    badge.className = "badge";
    badge.dataset.cat = route.category || "normal";
    badge.textContent = route.route;

    const body = document.createElement("div");
    body.className = "route__body";

    const top = document.createElement("div");
    top.className = "route__top";
    const dir = document.createElement("span");
    dir.className = "route__dir";
    const known = STATIC_ROUTES.find((r) => r.route === route.route);
    const dirText = route.dir || (known ? known.dir : "");
    dir.textContent = dirText ? dirText + " 방면" : route.categoryLabel || "방면 정보 없음";
    top.appendChild(dir);

    if (route.low1) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = "저상";
      top.appendChild(chip);
    }
    if (route.last1) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = "막차";
      top.appendChild(chip);
    }

    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "pin";
    pin.setAttribute("aria-label", route.route + " 노선 고정");
    pin.setAttribute("aria-pressed", state.pins.includes(route.route) ? "true" : "false");
    pin.textContent = state.pins.includes(route.route) ? "★" : "☆";
    pin.addEventListener("click", () => togglePin(route.route));
    top.appendChild(pin);
    body.appendChild(top);

    const eta = document.createElement("div");
    eta.className = "eta";
    const main = document.createElement("span");
    main.className = "eta__main";
    main.dataset.state = f1.kind;
    main.dataset.role = "eta1";
    main.textContent = f1.main;
    eta.appendChild(main);
    if (f1.sub) {
      const sub = document.createElement("span");
      sub.className = "eta__sub";
      sub.dataset.role = "eta1sub";
      sub.textContent = f1.sub;
      eta.appendChild(sub);
    }
    body.appendChild(eta);

    if (!["ended", "none"].includes((route.eta2 || {}).state || "none")) {
      const next = document.createElement("div");
      next.className = "eta__next";
      next.dataset.role = "eta2";
      next.textContent = "다음 " + f2;
      body.appendChild(next);
    }

    const metaParts = [];
    if (route.term) metaParts.push("배차 " + route.term + "분");
    if (route.first && route.last) metaParts.push(route.first + " ~ " + route.last);
    else if (route.last) metaParts.push("막차 " + route.last);
    if (metaParts.length) {
      const meta = document.createElement("div");
      meta.className = "route__meta";
      meta.textContent = metaParts.join(" · ");
      body.appendChild(meta);
    }

    li.appendChild(badge);
    li.appendChild(body);
    return li;
  }

  function render() {
    const data = state.data;
    if (!data || !Array.isArray(data.routes) || !data.routes.length) {
      renderStatic();
      return;
    }
    const elapsed = Math.max(0, Math.round((Date.now() - state.fetchedAt) / 1000));
    const frag = document.createDocumentFragment();
    for (const route of orderedRoutes(data.routes)) frag.appendChild(routeCard(route, elapsed));
    el.routes.replaceChildren(frag);
  }

  /* 실시간 데이터가 없을 때: 정류장 안내(노선·배차·첫차·막차)와 즐겨찾기 고정 */
  function renderStatic() {
    const frag = document.createDocumentFragment();
    for (const route of orderedRoutes(STATIC_ROUTES)) {
      const li = document.createElement("li");
      li.className = "route is-static";
      li.dataset.route = route.route;
      if (state.pins.includes(route.route)) li.classList.add("is-pinned");

      const badge = document.createElement("div");
      badge.className = "badge";
      badge.dataset.cat = route.category;
      badge.textContent = route.route;

      const body = document.createElement("div");
      body.className = "route__body";

      const top = document.createElement("div");
      top.className = "route__top";
      const dir = document.createElement("span");
      dir.className = "route__dir";
      dir.textContent = route.dir + " 방면";
      top.appendChild(dir);

      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = "pin";
      pin.setAttribute("aria-label", route.route + " 노선 고정");
      pin.setAttribute("aria-pressed", state.pins.includes(route.route) ? "true" : "false");
      pin.textContent = state.pins.includes(route.route) ? "★" : "☆";
      pin.addEventListener("click", () => togglePin(route.route));
      top.appendChild(pin);
      body.appendChild(top);

      const eta = document.createElement("div");
      eta.className = "eta";
      const main = document.createElement("span");
      main.className = "eta__main";
      main.dataset.state = "static";
      main.dataset.role = "term";
      main.textContent = route.term ? "배차 " + route.term + "분" : route.categoryLabel;
      eta.appendChild(main);
      body.appendChild(eta);

      if (route.first && route.last) {
        const meta = document.createElement("div");
        meta.className = "route__meta";
        meta.textContent = "첫차 " + route.first + " · 막차 " + route.last;
        body.appendChild(meta);
      }

      li.appendChild(badge);
      li.appendChild(body);
      frag.appendChild(li);
    }
    el.routes.replaceChildren(frag);
  }

  function renderSkeleton() {
    if (el.routes.children.length) return;
    const frag = document.createDocumentFragment();
    for (let i = 0; i < 4; i++) {
      const li = document.createElement("li");
      li.className = "skeleton";
      frag.appendChild(li);
    }
    el.routes.replaceChildren(frag);
  }

  /* 1초마다 도착 시간만 갱신 (카드 전체를 다시 그리지 않는다) */
  function tickRender() {
    if (!state.data || !state.fetchedAt) return;
    const elapsed = Math.max(0, Math.round((Date.now() - state.fetchedAt) / 1000));
    for (const route of state.data.routes) {
      const li = el.routes.querySelector('[data-route="' + cssEscape(route.route) + '"]');
      if (!li) continue;
      const rem1 = typeof (route.eta1 || {}).sec === "number" ? route.eta1.sec - elapsed : undefined;
      const rem2 = typeof (route.eta2 || {}).sec === "number" ? route.eta2.sec - elapsed : undefined;
      const f1 = formatEta(route.eta1, rem1);
      const main = li.querySelector('[data-role="eta1"]');
      if (main) {
        main.textContent = f1.main;
        main.dataset.state = f1.kind;
      }
      const sub = li.querySelector('[data-role="eta1sub"]');
      if (sub) sub.textContent = f1.sub;
      const next = li.querySelector('[data-role="eta2"]');
      if (next && !["ended", "none"].includes((route.eta2 || {}).state || "none")) {
        next.textContent = "다음 " + shortEta(route.eta2, rem2);
      }
    }
  }

  function cssEscape(v) {
    return String(v).replace(/["\\]/g, "\\$&");
  }

  /* ── 배너 ───────────────────────────────────────── */
  function showBanner(kind) {
    const b = el.banner;
    if (kind === "error") {
      b.hidden = false;
      b.innerHTML =
        "<strong>도착 정보를 가져오지 못했어요</strong>잠시 후 자동으로 다시 시도합니다. 계속 실패하면 설정에서 서버 주소를 확인해 주세요." +
        '<div class="banner__actions"><button type="button" class="primary" id="bannerRetry">다시 시도</button>' +
        '<button type="button" id="bannerSettings">설정</button></div>';
      $("bannerRetry").addEventListener("click", () => load(true));
      $("bannerSettings").addEventListener("click", openSettings);
      return;
    }
    b.hidden = true;
    b.innerHTML = "";
  }

  /* ── 데이터 ─────────────────────────────────────── */
  /* 중계 서버 없이 쓰는 기본 모드: 정류장 안내 + 지도 바로가기 중심 */
  function enterStaticMode() {
    state.data = null;
    state.fetchedAt = 0;
    state.error = null;
    showBanner(null);
    setStatus("idle", "정류장 안내 · 노선 " + STATIC_ROUTES.length + "개");
    el.refresh.hidden = true;
    el.refresh.disabled = true;
    el.footnote.textContent = "실시간 도착은 아래 지도 버튼에서 · ★ 누르면 맨 위 고정";
    render();
  }

  async function load(manual) {
    if (state.loading) return;
    const url = apiUrl();
    if (!url) {
      enterStaticMode();
      return;
    }

    state.loading = true;
    el.refresh.hidden = false;
    el.refresh.disabled = true;
    el.refresh.classList.add("is-spinning");
    if (!state.data) renderSkeleton();
    setStatus("loading", "도착 정보 불러오는 중…");

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(url, { signal: controller.signal, cache: "no-store" });
      const body = await res.json().catch(() => null);
      if (!res.ok || !body || body.ok !== true) {
        throw new Error((body && body.error) || "HTTP " + res.status);
      }
      state.data = body;
      state.fetchedAt = Date.now();
      state.error = null;
      writeLS(LS.last, JSON.stringify({ data: body, at: state.fetchedAt }));
      showBanner(null);
      render();
      setStatus("ok", "실시간 · " + timeLabel(state.fetchedAt) + " 갱신");
      el.footnote.textContent =
        (body.source === "kakao" ? "카카오맵 도착정보 기준" : body.source === "data.go.kr" ? "공공데이터포털 기준" : "서울시 버스 도착정보 기준") +
        " · 30초마다 자동 새로고침";
      if (manual) toast("새로고침 완료");
    } catch (e) {
      state.error = String((e && e.message) || e);
      if (state.data && state.fetchedAt) {
        setStatus("stale", "연결 지연 · " + timeLabel(state.fetchedAt) + " 정보");
        if (manual) toast("연결하지 못했어요 · 이전 정보 표시");
      } else {
        setStatus("error", "도착 정보를 가져오지 못했어요");
        showBanner("error");
        render();
        if (manual) toast("연결하지 못했어요");
      }
    } finally {
      clearTimeout(timer);
      state.loading = false;
      el.refresh.disabled = false;
      el.refresh.classList.remove("is-spinning");
      scheduleNext();
    }
  }

  function scheduleNext() {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      if (document.visibilityState === "visible") load(false);
      else scheduleNext();
    }, REFRESH_MS);
  }

  /* ── 즐겨찾기 ───────────────────────────────────── */
  function togglePin(route) {
    const i = state.pins.indexOf(route);
    if (i >= 0) {
      state.pins.splice(i, 1);
      toast(route + " 고정 해제");
    } else {
      state.pins.push(route);
      toast(route + " 맨 위로 고정");
    }
    savePins();
    render();
  }

  /* ── 번호 복사 ──────────────────────────────────── */
  async function copyArs() {
    const text = STOP.arsId;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      toast("정류장 번호 " + text + " 복사됨");
    } catch (e) {
      toast("복사하지 못했어요");
    }
  }

  /* ── 설정 시트 ──────────────────────────────────── */
  function openSettings() {
    el.apiInput.value = state.api || "";
    el.sheetHint.textContent = "";
    el.sheetHint.dataset.kind = "";
    el.sheet.hidden = false;
    setTimeout(() => el.apiInput.focus(), 120);
  }

  function closeSettings() {
    el.sheet.hidden = true;
  }

  function sheetHint(text, kind) {
    el.sheetHint.textContent = text;
    el.sheetHint.dataset.kind = kind || "";
  }

  async function testConnection() {
    const base = normApiBase(el.apiInput.value);
    if (!base) return sheetHint("주소를 입력해 주세요.", "err");
    sheetHint("확인 중…");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const res = await fetch(base + "/?arsId=" + STOP.arsId, { signal: controller.signal, cache: "no-store" });
      const body = await res.json().catch(() => null);
      if (res.ok && body && body.ok === true && Array.isArray(body.routes)) {
        sheetHint("연결됨 · " + body.routes.length + "개 노선 · " + (body.source || ""), "ok");
      } else {
        sheetHint("응답이 올바르지 않아요 (HTTP " + res.status + ")", "err");
      }
    } catch (e) {
      sheetHint("연결하지 못했어요 · 주소를 확인해 주세요", "err");
    } finally {
      clearTimeout(timer);
    }
  }

  function saveSettings() {
    const base = normApiBase(el.apiInput.value);
    state.api = base;
    writeLS(LS.api, base);
    closeSettings();
    if (!base) {
      toast("정류장 안내로 볼게요");
      enterStaticMode();
      return;
    }
    toast("저장했어요");
    state.data = null;
    el.routes.innerHTML = "";
    load(false);
  }

  /* ── 초기화 ─────────────────────────────────────── */
  function bind() {
    el.refresh.addEventListener("click", () => load(true));
    el.arsBtn.addEventListener("click", copyArs);
    el.copyBtn.addEventListener("click", copyArs);
    $("settingsBtn").addEventListener("click", openSettings);
    $("sheetClose").addEventListener("click", closeSettings);
    $("sheetTest").addEventListener("click", testConnection);
    $("sheetSave").addEventListener("click", saveSettings);
    el.sheet.addEventListener("click", (e) => {
      if (e.target === el.sheet) closeSettings();
    });
    el.apiInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        saveSettings();
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && Date.now() - state.fetchedAt > 10000) load(false);
    });
  }

  function restoreLast() {
    if (!state.api) return;
    try {
      const saved = JSON.parse(readLS(LS.last) || "null");
      if (saved && saved.data && Array.isArray(saved.data.routes) && saved.at) {
        state.data = saved.data;
        state.fetchedAt = saved.at;
        render();
        setStatus("stale", "저장된 정보 · " + timeLabel(saved.at));
      }
    } catch (e) {
      /* 무시 */
    }
  }

  function registerSW() {
    if (!("serviceWorker" in navigator)) return;
    if (!/^https?:$/.test(location.protocol)) return;
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  }

  function init() {
    bind();
    restoreLast();
    if (!state.data) render();
    state.tick = setInterval(tickRender, 1000);
    load(false);
    registerSW();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
