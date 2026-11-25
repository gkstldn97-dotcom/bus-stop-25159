# 서울현대요양병원 정류장 (25-159)

서울 강동구 올림픽로 689, **서울현대요양병원** 정류장(정류소 ID 25-159 · ARS 25159 · 내부 ID 124000059,
천호역2번출구·현대백화점 방면)의 실시간 버스 도착 정보를 아이폰 홈 화면 앱으로 보여 주는 프로젝트입니다.

- **노선**: 340(강남역) · 3318(마천동) · 3321(강동구청) · 3324(강동역) · 3411(한티역) · N31(국민대·심야)
- **실시간 도착**: 30초마다 자동 새로고침, 1초 단위 카운트다운, 즐겨찾기(맨 위 고정)
- **보조**: 네이버지도·카카오맵 정류장 화면 바로 열기, 정류장 번호 복사
- 서버 저장 없음. 설정한 중계 서버 주소와 즐겨찾기는 브라우저(localStorage)에만 남습니다.

## 구성

| 경로 | 설명 |
| --- | --- |
| `index.html`, `styles.css`, `app.js`, `sw.js`, `manifest.webmanifest`, 아이콘 | 홈 화면에 추가하는 PWA |
| `worker/worker.js`, `worker/wrangler.toml` | 서울시 도착정보를 받아 CORS 헤더를 붙여 주는 Cloudflare Worker |

## 데이터 출처

브라우저는 서울시 정류장 도착정보를 직접 부를 수 없습니다(CORS 헤더 없음). 그래서 Worker가 대신 받아 옵니다.

1. **서울시 TOPIS** – `topis.seoul.go.kr/map/getBusStn.do?url=/getStationByUidDetourAt&arsId=25159`
   (TOPIS 실시간 버스지도의 정류장 팝업이 쓰는 엔드포인트, **API 키 불필요**)
2. **카카오맵** – `map.kakao.com/bus/stop.json?busstopid=11250621001` (1차 출처 실패 시, 키 불필요)
3. **공공데이터포털** – `ws.bus.go.kr/api/rest/stationinfo/getStationByUid` (선택. `SEOUL_BUS_API_KEY`가 있을 때만)

응답 형식:

```json
{
  "ok": true,
  "stop": { "name": "서울현대요양병원", "arsId": "25159", "stId": "124000059", "direction": "천호역2번출구.현대백화점 방면" },
  "source": "topis",
  "fetchedAt": "2026-11-25T09:47:12.000Z",
  "routes": [
    {
      "route": "340", "dir": "강남역", "category": "trunk", "categoryLabel": "간선",
      "eta1": { "msg": "2분12초후[1번째 전]", "sec": 132, "stops": 1, "state": "running" },
      "eta2": { "msg": "12분52초후[7번째 전]", "sec": 772, "stops": 7, "state": "running" },
      "first": "04:30", "last": "23:10", "term": 7, "low1": true, "last1": false
    }
  ]
}
```

`state`: `running` · `soon` · `waiting` · `ended` · `none`

## 배포

### 1) 중계 서버 (Cloudflare Worker)

```bash
cd worker
npx wrangler login
npx wrangler deploy          # 예: https://bus-stop-25159.<계정>.workers.dev
```

CLI 없이 Cloudflare 대시보드 → Workers & Pages → Create Worker 에서 `worker.js` 내용을 붙여 넣고 Deploy 해도 됩니다.
공공데이터포털 인증키를 마지막 출처로 쓰려면 `npx wrangler secret put SEOUL_BUS_API_KEY`(없어도 동작).

### 2) 앱 (GitHub Pages)

`main` 브랜치 루트를 그대로 Pages로 올립니다(Settings → Pages → Deploy from a branch → `main` / `/ (root)`).
`.nojekyll` 파일이 있어 정적 파일이 그대로 배포됩니다.

배포 후 `app.js`의 `DEFAULT_API` 값을 Worker 주소로 바꾸면 첫 실행부터 바로 연결됩니다.
비워 두면 앱 첫 화면의 **서버 주소 넣기**에서 한 번만 입력하면 됩니다(기기 localStorage 저장).

## 확인

```bash
python3 tests/test_worker.py   # Worker 정규화·CORS·캐시·폴백·오류 (실제 Chromium에서 실행)
python3 tests/test_site.py     # 390×844 화면·버튼·저장·오프라인·스크린샷(verify/)
python3 tools/make_icons.py    # 아이콘 재생성
```

## 참고 링크

- 네이버지도 정류장: <https://map.naver.com/p/bus/bus-station/-/bus-station/109370>
- 카카오맵 정류장: <https://map.kakao.com/?busStopId=11250621001>
