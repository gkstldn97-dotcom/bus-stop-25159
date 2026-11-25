# 서울현대요양병원 정류장 (25-159)

서울 강동구 올림픽로 689, **서울현대요양병원** 정류장(정류소 ID 25-159 · ARS 25159 · 내부 ID 124000059,
천호역2번출구·현대백화점 방면)을 아이폰 홈 화면에 추가해 쓰는 웹앱입니다.
**GitHub Pages**(앱) + **Cloudflare Worker**(중계)로 동작합니다.

- **실시간 도착**: 340 · 3318 · 3321 · 3324 · 3411 · N31 여섯 노선의 도착 예정시간을 앱 안에서 30초마다 자동 갱신.
  서울시 TOPIS(1차)·카카오맵(2차)에서 받아 Cloudflare Worker가 CORS 헤더를 붙여 중계합니다.
  중계 응답이 없으면 마지막으로 받은 정보를 보여 주고, 지도 버튼으로 바로 넘어갈 수 있습니다.
- **노선 안내**: 노선별 배차 간격·첫차·막차를 카드에 함께 표시
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
| `worker/worker.js`, `worker/wrangler.toml` | 도착정보 중계 Cloudflare Worker (`https://bus-stop-25159.mokbo97.workers.dev`) |

## 중계 서버

`app.js`의 `DEFAULT_API`에 배포된 Worker 주소가 들어 있어, 설치하면 바로 실시간 화면으로 시작합니다.
앱 설정(⚙)의 **중계 서버 주소**를 바꾸면 다른 중계로도 붙일 수 있고, 비우면 안내 화면으로 돌아옵니다.

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

중계 상태는 `curl -s 'https://bus-stop-25159.mokbo97.workers.dev/?arsId=25159' | head -c 300` 로 확인합니다.
