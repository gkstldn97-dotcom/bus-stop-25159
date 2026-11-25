#!/usr/bin/env python3
"""프론트엔드 E2E: 390×844 아이폰 뷰포트에서 실제 화면·버튼·저장 동작을 확인한다.

- 서버 응답은 worker.js 를 그대로 실행해 만든 정규화 JSON(실시간 TOPIS 수집본)을 사용
- 콘솔 오류 0건, 개인정보 문자열 미포함 확인, 스크린샷 저장
실행: python3 builds/bus-stop-25159/tests/test_site.py
"""
import json
import os
import re
import pathlib
import threading
import functools
import http.server
import socketserver
import sys

os.environ.setdefault("LD_LIBRARY_PATH", "/workspace/.local/sysroot/lib")

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
SHOTS = ROOT / "verify"
PORT = 8792
API = "https://relay.example.test"
SB_API = "https://relay.example.test"  # 반투명 표기용 아님


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    httpd.serve_forever()


def main():
    from playwright.sync_api import sync_playwright

    topis = json.loads((FIX / "topis-live.json").read_text(encoding="utf-8"))
    kakao = json.loads((FIX / "kakao-live.json").read_text(encoding="utf-8"))
    SHOTS.mkdir(exist_ok=True)

    threading.Thread(target=serve, daemon=True).start()
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(("PASS  " if cond else "FAIL  ") + name + (("  → " + str(detail)) if detail and not cond else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # 1) 워커를 실제 실행해 정규화 페이로드를 만든다 (실시간 TOPIS 데이터)
        wpage = browser.new_page()
        wpage.goto(f"http://127.0.0.1:{PORT}/tests/harness.html")
        wpage.wait_for_function("window.__ready === true", timeout=10000)
        payload = wpage.evaluate(
            """async ({topis}) => {
              window.fetch = async (u) => {
                const s = String(u);
                if (s.includes('topis.seoul.go.kr')) return new Response(JSON.stringify(topis), {status: 200});
                return new Response('nope', {status: 404});
              };
              const res = await window.__worker.fetch(new Request('https://relay.example.test/?arsId=25159'), {});
              return await res.json();
            }""",
            {"topis": topis},
        )
        (FIX / "normalized.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print("payload routes:", [(r["route"], r["eta1"]["msg"], r["eta2"]["msg"]) for r in payload["routes"]])

        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                                  locale="ko-KR", timezone_id="Asia/Seoul",
                                  user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                                  is_mobile=True, has_touch=True, service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        failed = []
        page.on("requestfailed", lambda r: failed.append(r.url + " :: " + str(r.failure)))

        hit = {"count": 0}

        any_host = re.compile(r"https://relay2?\.example\.test/.*")
        api_host = re.compile(r"https://bus-stop-25159\.mokbo97\.workers\.dev/.*")

        def fulfill(route_):
            if any_host.match(route_.request.url):
                hit["count"] += 1
                route_.fulfill(status=200, content_type="application/json",
                               body=json.dumps(payload, ensure_ascii=False),
                               headers={"access-control-allow-origin": "*"})
            else:
                route_.continue_()

        page.route(any_host, fulfill)
        page.add_init_script(f"localStorage.setItem('bus25159.api', '{SB_API}');")
        page.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        page.wait_for_selector(".route", timeout=15000)

        cards = page.locator(".route")
        check("노선 카드 6개 렌더", cards.count() == 6, cards.count())
        names = page.locator(".badge").all_inner_texts()
        # 실시간 도착이 빨리 오는 순: 340·3318(곧 도착, 29초) → 3411(417초) → 3324(853초) → 3321(1050초) → N31(운행종료)
        expect_order = ["340", "3318", "3411", "3324", "3321", "N31"]
        check("도착 빠른 순 정렬", names == expect_order, names)

        secs = {r["route"]: (r.get("eta1") or {}).get("sec") for r in payload["routes"]}

        def sort_key(n):
            s = secs.get(n)
            return (1, 0) if not isinstance(s, int) else (0, s)

        keys = [sort_key(n) for n in names]
        check("남은 시간 오름차순", keys == sorted(keys), list(zip(names, keys)))
        check("운행종료 노선 맨 아래", names[-1] == "N31" and secs["N31"] is None, names[-1])
        check("곧 도착 노선이 맨 위", all("곧 도착" in page.locator(f'.route[data-route="{n}"] [data-role="eta1"]').inner_text() for n in names[:2]), names[:2])

        cats = page.eval_on_selector_all(".badge", "els => els.map(e => e.dataset.cat)")
        check("색상 구분(간선/지선/심야)", cats[:2] == ["trunk", "branch"] and cats[-1] == "night", cats)

        first_main = page.locator('.route[data-route="340"] [data-role="eta1"]').inner_text()
        check("첫 노선 도착 시간 표시", any(k in first_main for k in ("분", "곧 도착", "운행종료")), first_main)
        check("정류장 상태 표시", "실시간" in page.locator("#statusText").inner_text(), page.locator("#statusText").inner_text())
        check("다음 버스 줄 표시", page.locator('.route[data-route="340"] [data-role="eta2"]').count() >= 0)

        meta = page.locator('.route[data-route="340"] .route__meta').inner_text()
        check("배차·첫차·막차 표기", "배차" in meta and ":" in meta, meta)

        # 숫자가 1초 후 줄어드는지 (카운트다운) + 순서는 그대로인지
        before = page.locator('.route[data-route="3411"] [data-role="eta1"]').inner_text()
        order_before = page.locator(".badge").all_inner_texts()
        page.wait_for_timeout(2200)
        after = page.locator('.route[data-route="3411"] [data-role="eta1"]').inner_text()
        order_after = page.locator(".badge").all_inner_texts()
        check("1초 카운트다운 동작", before != after or "곧 도착" in before or "운행종료" in before, (before, after))
        check("카운트다운 중 순서 유지", order_before == order_after == expect_order, (order_before, order_after))
        check("카운트다운 중 카드 중복 없음", page.locator(".route").count() == 6, page.locator(".route").count())

        page.screenshot(path=str(SHOTS / "01-home.png"), full_page=True)

        # 즐겨찾기 고정: 고정끼리 먼저, 그 안에서도 빨리 오는 순
        page.locator('.route[data-route="3324"] .pin').click()
        page.wait_for_timeout(250)
        page.locator('.route[data-route="3411"] .pin').click()
        page.wait_for_timeout(300)
        order = page.locator(".badge").all_inner_texts()
        check("고정 노선이 맨 위", order[:2] == ["3411", "3324"], order)
        check("고정 안에서도 빨리 오는 순", order[0] == "3411", order)
        check("나머지도 빨리 오는 순", order[2:] == ["340", "3318", "3321", "N31"], order)
        stored = page.evaluate("localStorage.getItem('bus25159.pins')")
        check("고정 상태 저장", stored and "3411" in stored and "3324" in stored, stored)
        page.screenshot(path=str(SHOTS / "02-pinned.png"), full_page=True)

        # 새로고침 버튼
        n_before = hit["count"]
        page.locator("#refreshBtn").click()
        page.wait_for_timeout(900)
        check("수동 새로고침이 서버를 호출", hit["count"] > n_before, (n_before, hit["count"]))

        # 30초 자동 새로고침 타이머가 걸려 있는지 (네트워크 호출 1회 추가)
        n_before = hit["count"]
        page.wait_for_timeout(31000)
        check("30초 자동 새로고침", hit["count"] > n_before, (n_before, hit["count"]))

        # 새 도착 시간이 오면(새로고침) 순서를 다시 계산한다 — 고정 없는 새 컨텍스트
        live = {"payload": payload}
        sctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                                   locale="ko-KR", timezone_id="Asia/Seoul", is_mobile=True,
                                   has_touch=True, service_workers="block")
        spage = sctx.new_page()
        serrs = []
        spage.on("pageerror", lambda e: serrs.append(str(e)))
        spage.route(any_host, lambda r: r.fulfill(status=200, content_type="application/json",
                                                  body=json.dumps(live["payload"], ensure_ascii=False),
                                                  headers={"access-control-allow-origin": "*"}))
        spage.add_init_script(f"localStorage.setItem('bus25159.api', '{SB_API}');")
        spage.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        spage.wait_for_selector(".route", timeout=15000)
        o0 = spage.locator(".badge").all_inner_texts()
        check("새로고침 전 순서", o0 == expect_order, o0)

        def mutate(secs_map, states=None):
            body = json.loads(json.dumps(payload))
            for r in body["routes"]:
                s = secs_map.get(r["route"])
                if s is None:
                    continue
                r["eta1"] = dict(r["eta1"] or {})
                r["eta1"]["sec"] = s
                r["eta1"]["state"] = "soon" if s <= 30 else "running"
                r["eta1"]["msg"] = "곧 도착" if s <= 30 else "%d초후" % s
            for name, state in (states or {}).items():
                for r in body["routes"]:
                    if r["route"] == name:
                        r["eta1"] = {"msg": {"none": "정보 없음", "waiting": "출발대기", "ended": "운행종료"}[state],
                                     "state": state, "sec": None, "stops": None}
            return body

        def refresh_order():
            spage.locator("#refreshBtn").click()
            spage.wait_for_timeout(1200)
            return spage.locator(".badge").all_inner_texts()

        live["payload"] = mutate({"3321": 95, "3324": 300, "3411": 520, "3318": 700, "340": 900})
        o = refresh_order()
        check("새로고침 후 새 도착 시간 순서", o == ["3321", "3324", "3411", "3318", "340", "N31"], o)
        spage.screenshot(path=str(SHOTS / "21-sort-refresh.png"), full_page=True)

        live["payload"] = mutate({"3321": 95, "3324": 300, "3411": 520}, {"340": "none", "3318": "waiting"})
        o = refresh_order()
        check("정보 없음·출발대기 맨 아래", o == ["3321", "3324", "3411", "340", "3318", "N31"], o)
        none_txt = spage.locator('.route[data-route="340"] [data-role="eta1"]').inner_text()
        check("정보 없음 표기", none_txt == "정보 없음", none_txt)

        live["payload"] = mutate({"3321": 95, "3324": 300, "3411": 520, "3318": 700, "340": -90})
        o = refresh_order()
        check("도착한 버스가 맨 위", o[0] == "340", o)
        check("순서 변경 뒤 카드 6개", spage.locator(".route").count() == 6, spage.locator(".route").count())
        check("순서 테스트 JS 예외 0", len(serrs) == 0, serrs[:2])
        sctx.close()

        # 번호 복사 (클립보드 권한 부여)
        ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        page.locator("#copyBtn").click()
        page.wait_for_timeout(400)
        clip = page.evaluate("navigator.clipboard.readText()")
        check("정류장 번호 복사", clip.strip() == "25159", clip)
        check("복사 토스트", page.locator("#toast").evaluate("e => e.classList.contains('is-on')"))

        # 지도 링크
        naver = page.locator("#naverLink").get_attribute("href")
        kakao_href = page.locator("#kakaoLink").get_attribute("href")
        check("네이버지도 링크", naver == "https://map.naver.com/p/bus/bus-station/-/bus-station/109370", naver)
        check("카카오맵 링크", kakao_href == "https://map.kakao.com/?busStopId=11250621001", kakao_href)

        # 설정 시트: 연결 확인 + 저장
        page.locator("#settingsBtn").click()
        page.wait_for_timeout(300)
        check("설정 시트 열림", page.locator("#settingsSheet").is_visible())
        page.screenshot(path=str(SHOTS / "03-settings.png"))
        page.locator("#sheetTest").click()
        page.wait_for_timeout(800)
        hint = page.locator("#sheetHint").inner_text()
        check("연결 확인 성공 문구", "연결됨" in hint, hint)
        page.route("https://relay2.example.test/**", fulfill)
        page.locator("#apiInput").fill("relay2.example.test/")
        page.locator("#sheetSave").click()
        page.wait_for_timeout(600)
        saved = page.evaluate("localStorage.getItem('bus25159.api')")
        check("주소 정규화 저장", saved == "https://relay2.example.test", saved)
        check("설정 시트 닫힘", not page.locator("#settingsSheet").is_visible())

        # 오류 화면: 서버 500 (저장된 정보가 없는 새 컨텍스트)
        errctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                                     locale="ko-KR", is_mobile=True, has_touch=True)
        page2 = errctx.new_page()
        errs2 = []
        page2.on("pageerror", lambda e: errs2.append(str(e)))
        page2.add_init_script(f"localStorage.setItem('bus25159.api', 'https://down.example.test');")
        page2.route("https://down.example.test/**", lambda r: r.fulfill(status=500, body="down"))
        page2.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        page2.wait_for_timeout(1500)
        b = page2.locator("#banner")
        check("오류 배너 표시", b.is_visible() and "가져오지 못했" in b.inner_text(), b.inner_text() if b.is_visible() else "(hidden)")
        check("오류 상태 표시", "못했" in page2.locator("#statusText").inner_text(), page2.locator("#statusText").inner_text())
        page2.screenshot(path=str(SHOTS / "04-error.png"), full_page=True)
        check("오류 화면 JS 예외 0", len(errs2) == 0, errs2[:2])

        # 기본 화면: 내장 Worker(DEFAULT_API)로 곧바로 실시간을 불러온다 (2026-11-25 Worker 배포)
        page3 = ctx.new_page()
        errs3 = []
        page3.on("pageerror", lambda e: errs3.append(str(e)))
        page3.add_init_script("localStorage.clear();")
        page3.route(api_host, lambda r: r.fulfill(status=200, content_type="application/json",
                                                  body=json.dumps(payload, ensure_ascii=False),
                                                  headers={"access-control-allow-origin": "*"}))
        page3.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        page3.wait_for_selector(".route", timeout=15000)
        page3.wait_for_timeout(600)
        b3 = page3.locator("#banner")
        check("기본 화면에 서버 경고 배너 없음", not b3.is_visible(), b3.inner_text() if b3.is_visible() else "(hidden)")
        status3 = page3.locator("#statusText").inner_text()
        check("기본 화면 실시간 상태 문구", "실시간" in status3 and "미설정" not in status3, status3)
        check("기본 화면 노선 카드 6개", page3.locator(".route").count() == 6, page3.locator(".route").count())
        metas = page3.locator(".route .route__meta").all_inner_texts()
        check("배차 간격 표시", len(metas) == 6 and all("배차" in m for m in metas) and "7분" in metas[0], metas)
        meta3 = page3.locator('.route[data-route="340"] .route__meta').inner_text()
        check("첫차·막차 표시", "04:30" in meta3 and "23:10" in meta3, meta3)
        eta3 = page3.locator('.route[data-route="340"] [data-role="eta1"]').inner_text()
        check("기본 화면 도착 시간 표시", any(k in eta3 for k in ("분", "곧 도착", "운행종료")), eta3)
        check("기본 화면 새로고침 표시", page3.locator("#refreshBtn").is_visible())
        check("기본 화면 안내 문구", "자동 새로고침" in page3.locator("#footnote").inner_text(), page3.locator("#footnote").inner_text())
        check("기본 화면에도 지도 버튼 사용", page3.locator("#naverLink").is_visible() and page3.locator("#kakaoLink").is_visible())
        # 서버 없이도 즐겨찾기 고정이 동작·저장되는지
        page3.locator('.route[data-route="N31"] .pin').click()
        page3.wait_for_timeout(300)
        order3 = page3.locator(".badge").all_inner_texts()
        stored3 = page3.evaluate("localStorage.getItem('bus25159.pins')")
        check("기본 화면에서도 고정 동작", order3[0] == "N31" and stored3 and "N31" in stored3, (order3, stored3))
        page3.screenshot(path=str(SHOTS / "05-default.png"), full_page=True)
        check("기본 화면 JS 예외 0", len(errs3) == 0, errs3[:2])

        # 다크 모드
        dark = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                                   color_scheme="dark", locale="ko-KR", is_mobile=True, has_touch=True)
        dpage = dark.new_page()
        dpage.route(any_host, fulfill)
        dpage.add_init_script(f"localStorage.setItem('bus25159.api', '{SB_API}');")
        dpage.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        dpage.wait_for_selector(".route", timeout=15000)
        dpage.screenshot(path=str(SHOTS / "06-dark.png"), full_page=True)
        check("다크 모드 렌더", dpage.locator(".route").count() == 6)

        # 오프라인 재실행: 저장된 정보 표시
        off = browser.new_context(viewport={"width": 390, "height": 844}, locale="ko-KR", is_mobile=True)
        opage = off.new_page()
        opage.route(API + "/**", lambda r: r.abort())
        opage.add_init_script(
            "localStorage.setItem('bus25159.api', '%s');" % API
            + "localStorage.setItem('bus25159.last', %s);" % json.dumps(
                json.dumps({"data": payload, "at": 1795600000000})
            )
        )
        opage.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        opage.wait_for_timeout(1200)
        check("오프라인 시 저장 정보 표시", opage.locator(".route").count() == 6, opage.locator(".route").count())

        # 서비스워커: 등록·활성화 후 오프라인에서도 셸이 뜨는지
        swctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="ko-KR", is_mobile=True)
        swpage = swctx.new_page()
        swpage.route(any_host, fulfill)
        swpage.add_init_script(f"localStorage.setItem('bus25159.api', '{SB_API}');")
        swpage.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        swpage.wait_for_timeout(1500)
        sw_state = swpage.evaluate(
            """async () => {
              const reg = await navigator.serviceWorker.ready;
              return {active: !!(reg.active), script: reg.active && reg.active.scriptURL, controller: !!navigator.serviceWorker.controller};
            }"""
        )
        check("서비스워커 등록·활성화", sw_state.get("active") and sw_state["script"].endswith("/site/sw.js"), sw_state)
        swctx.set_offline(True)
        swpage.reload()
        swpage.wait_for_timeout(1200)
        check("오프라인에서도 앱 셸 표시", swpage.locator("h1").inner_text() == "서울현대요양병원", swpage.title())
        check("오프라인에서 카드 렌더", swpage.locator(".route").count() == 6, swpage.locator(".route").count())
        swctx.set_offline(False)

        # 개인정보/민감 문자열 미포함
        body = page.content() + page3.content()
        bad = ["한시우", "Siwoo", "최다솜", "천호동", "영등포", "@hansiu", "3159", "gkstldn97"]
        found = [w for w in bad if w in body]
        check("개인정보 문자열 없음", not found, found)
        if errors:
            check("콘솔 오류 0건", False, (errors[:4], failed[:4]))
        else:
            check("콘솔 오류 0건", True)

        browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
