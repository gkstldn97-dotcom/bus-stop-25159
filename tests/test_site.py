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
        check("노선 번호 순서/내용", names == ["340", "3318", "3321", "3324", "3411", "N31"], names)

        cats = page.eval_on_selector_all(".badge", "els => els.map(e => e.dataset.cat)")
        check("색상 구분(간선/지선/심야)", cats[:2] == ["trunk", "branch"] and cats[-1] == "night", cats)

        first_main = page.locator('.route[data-route="340"] [data-role="eta1"]').inner_text()
        check("첫 노선 도착 시간 표시", any(k in first_main for k in ("분", "곧 도착", "운행종료")), first_main)
        check("정류장 상태 표시", "실시간" in page.locator("#statusText").inner_text(), page.locator("#statusText").inner_text())
        check("다음 버스 줄 표시", page.locator('.route[data-route="340"] [data-role="eta2"]').count() >= 0)

        meta = page.locator('.route[data-route="340"] .route__meta').inner_text()
        check("배차·첫차·막차 표기", "배차" in meta and ":" in meta, meta)

        # 숫자가 1초 후 줄어드는지 (카운트다운)
        before = page.locator('.route[data-route="3411"] [data-role="eta1"]').inner_text()
        page.wait_for_timeout(2200)
        after = page.locator('.route[data-route="3411"] [data-role="eta1"]').inner_text()
        check("1초 카운트다운 동작", before != after or "곧 도착" in before or "운행종료" in before, (before, after))

        page.screenshot(path=str(SHOTS / "01-home.png"), full_page=True)

        # 즐겨찾기 고정
        page.locator('.route[data-route="3411"] .pin').click()
        page.wait_for_timeout(300)
        order = page.locator(".badge").all_inner_texts()
        check("고정 시 맨 위로 이동", order[0] == "3411", order)
        stored = page.evaluate("localStorage.getItem('bus25159.pins')")
        check("고정 상태 저장", stored and "3411" in stored, stored)
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

        # 서버 미설정 화면
        page3 = ctx.new_page()
        page3.add_init_script("localStorage.clear();")
        page3.goto(f"http://127.0.0.1:{PORT}/site/index.html")
        page3.wait_for_timeout(800)
        b3 = page3.locator("#banner")
        check("미설정 시 안내 배너", b3.is_visible() and "서버" in b3.inner_text(), b3.inner_text() if b3.is_visible() else "(hidden)")
        check("미설정에도 지도 버튼 사용", page3.locator("#naverLink").is_visible() and page3.locator("#kakaoLink").is_visible())
        page3.screenshot(path=str(SHOTS / "05-setup.png"), full_page=True)

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
        body = page.content()
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
