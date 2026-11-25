#!/usr/bin/env python3
"""worker/worker.js 를 실제 Chromium(ES module)에서 실행해 검증한다.

- 상류 응답은 tests/fixtures/ 의 실제 수집본으로 대체(mock fetch)
- 응답 형식, CORS 헤더, 캐시, 폴백, 오류 경로를 확인
실행: python3 builds/bus-stop-25159/tests/test_worker.py
"""
import json
import os
import pathlib
import threading
import functools
import http.server
import socketserver
import sys

# 이 런타임의 Chromium은 sysroot의 공유 라이브러리를 필요로 한다.
os.environ.setdefault("LD_LIBRARY_PATH", "/workspace/.local/sysroot/lib")

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
PORT = 8791


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    httpd.serve_forever()
    return httpd


def main():
    from playwright.sync_api import sync_playwright

    topis = json.loads((FIX / "topis-live.json").read_text(encoding="utf-8"))
    kakao = json.loads((FIX / "kakao-live.json").read_text(encoding="utf-8"))

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(("PASS  " if cond else "FAIL  ") + name + (("  → " + str(detail)) if detail and not cond else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{PORT}/tests/harness.html")
        page.wait_for_function("window.__ready === true", timeout=10000)

        def run(path, upstream):
            """upstream: list of (matcher, payload-or-None) — 첫 매칭을 반환, None이면 500"""
            return page.evaluate(
                """async ({path, upstream}) => {
                  const calls = [];
                  window.fetch = async (url) => {
                    const u = String(url && url.url ? url.url : url);
                    calls.push(u);
                    const hit = upstream.find(([m]) => u.includes(m));
                    if (!hit) return new Response('nope', {status: 404});
                    const payload = hit[1];
                    if (payload === null) return new Response('boom', {status: 500});
                    return new Response(JSON.stringify(payload), {status: 200, headers: {'content-type': 'application/json'}});
                  };
                  const res = await window.__worker.fetch(new Request('https://relay.test' + path), {});
                  let body = null;
                  try { body = await res.json(); } catch (e) { body = null; }
                  return {
                    status: res.status,
                    headers: Object.fromEntries(res.headers.entries()),
                    body,
                    calls,
                  };
                }""",
                {"path": path, "upstream": upstream},
            )

        # 1) TOPIS 정상
        r = run("/?arsId=25159", [["topis.seoul.go.kr", topis], ["map.kakao.com", kakao]])
        b = r["body"] or {}
        check("topis: 200", r["status"] == 200, r["status"])
        check("topis: CORS *", r["headers"].get("access-control-allow-origin") == "*", r["headers"])
        check("topis: ok/source", b.get("ok") is True and b.get("source") == "topis", b.get("source"))
        check("topis: 정류장 이름", (b.get("stop") or {}).get("name") == "서울현대요양병원", (b.get("stop") or {}).get("name"))
        routes = {x["route"]: x for x in (b.get("routes") or [])}
        check("topis: 6개 노선", len(routes) == 6, list(routes))
        check("topis: 340 간선", routes.get("340", {}).get("category") == "trunk", routes.get("340", {}).get("category"))
        check("topis: 3318 지선", routes.get("3318", {}).get("category") == "branch")
        check("topis: N31 심야", routes.get("N31", {}).get("category") == "night")
        check("topis: 방향 표기", routes.get("340", {}).get("dir") == "강남역", routes.get("340", {}).get("dir"))
        e1 = routes.get("340", {}).get("eta1") or {}
        check("topis: eta1 초 계산", isinstance(e1.get("sec"), (int, float)) and e1["sec"] > 0, e1)
        check("topis: eta1 정류장 수", e1.get("stops") is None or (isinstance(e1.get("stops"), int) and e1["stops"] >= 1), e1)
        check("topis: 첫차/막차", routes.get("340", {}).get("first") == "04:30" and routes.get("340", {}).get("last") == "23:10",
              (routes.get("340", {}).get("first"), routes.get("340", {}).get("last")))
        check("topis: 배차간격", routes.get("340", {}).get("term") == 7, routes.get("340", {}).get("term"))
        check("topis: N31 운행종료 처리", (routes.get("N31", {}).get("eta1") or {}).get("state") == "ended",
              routes.get("N31", {}).get("eta1"))
        check("topis: fetchedAt ISO", isinstance(b.get("fetchedAt"), str) and "T" in (b.get("fetchedAt") or ""))

        # 2) 캐시: TTL 안에서는 상류를 다시 부르지 않는다
        r2 = run("/?arsId=25159", [["topis.seoul.go.kr", topis]])
        check("cache: 두 번째 호출은 업스트림 0회", len(r2["calls"]) == 0, r2["calls"])
        check("cache: x-cache hit", r2["headers"].get("x-cache") == "hit", r2["headers"].get("x-cache"))

        # 3) TOPIS 실패 → 카카오 폴백 (캐시를 비우기 위해 새 페이지 컨텍스트 사용)
        ctx = browser.new_context()
        page2 = ctx.new_page()
        page2.goto(f"http://127.0.0.1:{PORT}/tests/harness.html")
        page2.wait_for_function("window.__ready === true", timeout=10000)

        def run2(path, upstream, worker_url="https://relay.test"):
            return page2.evaluate(
                """async ({path, upstream}) => {
                  const calls = [];
                  window.fetch = async (url) => {
                    const u = String(url && url.url ? url.url : url);
                    calls.push(u);
                    const hit = upstream.find(([m]) => u.includes(m));
                    if (!hit) return new Response('nope', {status: 404});
                    if (hit[1] === null) return new Response('boom', {status: 500});
                    return new Response(JSON.stringify(hit[1]), {status: 200});
                  };
                  const res = await window.__worker.fetch(new Request('https://relay.test' + path), {});
                  let body = null; try { body = await res.json(); } catch (e) {}
                  return {status: res.status, headers: Object.fromEntries(res.headers.entries()), body, calls};
                }""",
                {"path": path, "upstream": upstream},
            )

        r = run2("/?arsId=25159", [["topis.seoul.go.kr", None], ["map.kakao.com", kakao]])
        b = r["body"] or {}
        check("fallback: 카카오로 성공", r["status"] == 200 and b.get("source") == "kakao", (r["status"], b.get("source")))
        check("fallback: 카카오 노선 6개", len(b.get("routes") or []) == 6, [x["route"] for x in (b.get("routes") or [])])
        check("fallback: 카카오 eta 초", all(isinstance((x.get("eta1") or {}).get("sec"), (int, float)) or
                                             (x.get("eta1") or {}).get("state") in ("none", "ended")
                                             for x in (b.get("routes") or [])))
        check("fallback: TOPIS 시도 기록", any(a.get("source") == "topis" for a in (b.get("attempts") or [])), b.get("attempts"))

        # 4) 모든 출처 실패 → 502
        ctx2 = browser.new_context()
        page3 = ctx2.new_page()
        page3.goto(f"http://127.0.0.1:{PORT}/tests/harness.html")
        page3.wait_for_function("window.__ready === true", timeout=10000)
        r = page3.evaluate(
            """async () => {
              window.fetch = async () => new Response('down', {status: 503});
              const res = await window.__worker.fetch(new Request('https://relay.test/?arsId=25159'), {});
              let body = null; try { body = await res.json(); } catch (e) {}
              return {status: res.status, headers: Object.fromEntries(res.headers.entries()), body};
            }"""
        )
        check("error: 502", r["status"] == 502, r["status"])
        check("error: CORS 유지", r["headers"].get("access-control-allow-origin") == "*")
        check("error: ok=false + attempts", (r["body"] or {}).get("ok") is False and len((r["body"] or {}).get("attempts") or []) >= 2,
              r["body"])

        # 5) 지원하지 않는 정류장 400
        r = page3.evaluate(
            """async () => {
              window.fetch = async () => new Response('{}', {status: 200});
              const res = await window.__worker.fetch(new Request('https://relay.test/?arsId=99999'), {});
              return {status: res.status, body: await res.json()};
            }"""
        )
        check("guard: 미지원 arsId 400", r["status"] == 400, r["status"])

        # 6) OPTIONS preflight / health
        r = page3.evaluate(
            """async () => {
              const r1 = await window.__worker.fetch(new Request('https://relay.test/?arsId=25159', {method: 'OPTIONS'}), {});
              const r2 = await window.__worker.fetch(new Request('https://relay.test/health'), {});
              return {o: r1.status, oh: Object.fromEntries(r1.headers.entries()), h: r2.status, hb: await r2.json()};
            }"""
        )
        check("cors: OPTIONS 204", r["o"] == 204, r["o"])
        check("cors: allow-methods", "GET" in (r["oh"].get("access-control-allow-methods") or ""), r["oh"])
        check("health: ok", r["h"] == 200 and (r["hb"] or {}).get("ok") is True, r["hb"])

        check("콘솔/페이지 오류 0건", len(errors) == 0, errors[:3])
        browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
