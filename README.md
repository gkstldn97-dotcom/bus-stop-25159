# 서울현대요양병원 정류장 (25-159)

서울 강동구 올림픽로 689, **서울현대요양병원** 정류장(정류소 ID 25-159 · ARS 25159 · 내부 ID 124000059,
천호역2번출구·현대백화점 방면)을 아이폰 홈 화면에 추가해 쓰는 정적 웹앱입니다.
**서버 없이 GitHub Pages만으로** 동작합니다.

- **노선 안내**: 340(강남역) · 3318(마천동) · 3321(강동구청) · 3324(강동역) · 3411(한티역) · N31(국민대·심야)
  — 노선별 배차 간격·첫차·막차를 카드로 표시
- **즐겨찾기 고정**: 카드의 ★를 누르면 자주 타는 노선이 맨 위로 (기기 localStorage 저장)
- **1초 점프**: 하단의 **네이버지도** / **카카오맵** 버튼으로 정류장 실시간 도착 화면을 바로 엽니다.
  (네이버지도 <https://map.naver.com/p/bus/bus-station/-/bus-station/109370>,
  카카오맵 <https://map.kakao.com/?busStopId=11250621001> — 둘 다 2026-11-25에 실제 정류장 화면이 열리는 것을 확인)
- **정류장 번호 복사**: 25159를 한 번에 복사
- 서버 저장 없음. 설정값과 즐겨찾기는 브라우저(localStorage)에만 남습니다.

## 구성

| 경로 | 설명 |
| --- | --- |
| `index.html`, `styles.css`, `app.js`, `sw.js`, `manifest.webmanifest`, 아이콘 | 홈 화면에 추가하는 PWA (Pages 루트) |
| `worker/worker.js`, `worker/wrangler.toml` | (선택) 앱 안에서도 도착 시간을 보고 싶을 때 쓰는 Cloudflare Worker |

## 선택: 앱 안에서 실시간 도착 시간 보기

기본 화면은 노선 안내 + 지도 바로가기만 씁니다. 도착 시간을 앱 안에서도 보고 싶으면:

1. Cloudflare Workers에 `worker/worker.js`를 배포합니다(아래 참고) → `https://bus-stop-25159.<계정>.workers.dev`
2. 앱 오른쪽 위 **설정(⚙)** → **중계 서버 주소**에 그 주소를 넣고 저장하면
   30초마다 자동 갱신되는 도착 시간 카드로 바뀝니다. 주소를 비우면 다시 안내 화면으로 돌아옵니다.

브라우저는 서울시 도착정보를 직접 부를 수 없습니다(CORS 헤더 없음). Worker가 대신 받아 옵니다.

1. **서울시 TOPIS** – `topis.seoul.go.kr/map/getBusStn.do?url=/getStationByUidDetourAt&arsId=25159` (API 키 불필요)
2. **카카오맵** – `map.kakao.com/bus/stop.json?busstopid=11250621001` (1차 출처 실패 시, 키 불필요)
3. **공공데이터포털** – `ws.bus.go.kr/api/rest/stationinfo/getStationByUid` (선택. `SEOUL_BUS_API_KEY`가 있을 때만)

```bash
cd worker
npx wrangler login
npx wrangler deploy
```

## 배포

`main` 브랜치 루트를 그대로 Pages로 올립니다(Settings → Pages → Deploy from a branch → `main` / `/ (root)`).
`.nojekyll` 파일이 있어 정적 파일이 그대로 배포됩니다.

- 주소: <https://gkstldn97-dotcom.github.io/bus-stop-25159/>
- 아이폰 Safari에서 열고 **공유 → 홈 화면에 추가**

## 확인

```bash
python3 tests/test_site.py     # 390×844 화면·버튼·저장·오프라인·스크린샷(verify/)
python3 tests/test_worker.py   # (선택) Worker 정규화·CORS·캐시·폴백·오류
python3 tools/make_icons.py    # 아이콘 재생성
```
