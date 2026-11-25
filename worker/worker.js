/**
 * 서울현대요양병원(25-159) 정류장 실시간 도착정보 중계 워커
 *
 *   GET /            → 기본 정류장(25159)
 *   GET /?arsId=25159
 *   GET /health
 *
 * 서울시 정류장 도착정보는 다른 출처(서울시 TOPIS·카카오)에서 CORS 헤더 없이
 * 내려오기 때문에 브라우저에서 직접 부를 수 없다. 이 워커가 대신 받아
 * Access-Control-Allow-Origin: * 로 감싸 돌려준다.
 *
 * 데이터 출처(순서대로 시도, 키 없이 동작):
 *   1) topis  : topis.seoul.go.kr/map/getBusStn.do  (서울시 교통정보센터 실시간 정류장 팝업 API)
 *   2) kakao  : map.kakao.com/bus/stop.json          (카카오맵 정류장 도착정보)
 *   3) go.kr  : ws.bus.go.kr/api/rest/...            (공공데이터포털 인증키가 env에 있을 때만)
 */

const STOPS = {
  '25159': {
    arsId: '25159',
    stId: '124000059',
    kakaoId: '11250621001',
    name: '서울현대요양병원',
    direction: '천호역2번출구.현대백화점 방면',
  },
};

const DEFAULT_ARS_ID = '25159';
const UPSTREAM_TIMEOUT_MS = 6000;
const CACHE_TTL_MS = 8000;

const CORS_HEADERS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, OPTIONS',
  'access-control-allow-headers': 'content-type',
  'access-control-max-age': '86400',
};

/* ── 응답 캐시: 같은 순간에 여러 기기가 새로고침해도 상류는 한 번만 부른다 ── */
const cache = new Map(); // arsId -> { at, payload }
const inflight = new Map(); // arsId -> Promise

/* ── 유틸 ───────────────────────────────────────────────────────────── */

function jsonResponse(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=10',
      ...CORS_HEADERS,
      ...extraHeaders,
    },
  });
}

async function fetchJson(url, timeoutMs = UPSTREAM_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: {
        'user-agent':
          'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
        accept: 'application/json, text/plain, */*',
        'accept-language': 'ko-KR,ko;q=0.9',
        referer: 'https://topis.seoul.go.kr/map/openBusMap.do',
      },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch (e) {
      throw new Error('JSON 파싱 실패: ' + text.slice(0, 80));
    }
  } finally {
    clearTimeout(timer);
  }
}

/** "2분12초후[1번째 전]" 같은 문구를 초·정류장 수로 바꾼다. */
function parseArrivalMessage(msg, fallbackSec) {
  const text = String(msg == null ? '' : msg).trim();
  const num = (v) => {
    const n = Number(String(v == null ? '' : v).replace(/[^0-9.-]/g, ''));
    return Number.isFinite(n) ? n : null;
  };

  let state = 'running';
  if (!text) state = 'none';
  else if (text.includes('운행종료')) state = 'ended';
  else if (text.includes('출발대기')) state = 'waiting';
  else if (text.includes('곧 도착') || text.includes('도착예정')) state = 'soon';

  let sec = null;
  const minMatch = text.match(/(\d+)\s*분/);
  const secMatch = text.match(/(\d+)\s*초/);
  if (minMatch || secMatch) {
    sec = (minMatch ? Number(minMatch[1]) * 60 : 0) + (secMatch ? Number(secMatch[1]) : 0);
  }
  if (sec == null) {
    const fb = num(fallbackSec);
    if (fb != null && fb > 0) sec = fb;
  }
  if (state === 'soon' && (sec == null || sec > 60)) sec = sec == null ? 0 : sec;

  const stopsMatch = text.match(/\[(\d+)\s*번째\s*전\]/);
  const stops = stopsMatch ? Number(stopsMatch[1]) : null;

  return { msg: text, state, sec, stops };
}

/** 노선 종류(간선·지선·심야…) 판별. routeType 코드와 노선명을 함께 본다. */
function routeCategory(routeName, routeTypeCode) {
  const name = String(routeName || '').trim();
  if (/^N\d/i.test(name)) return { label: '심야', key: 'night' };
  switch (String(routeTypeCode || '')) {
    case '3':
      return { label: '간선', key: 'trunk' };
    case '4':
      return { label: '지선', key: 'branch' };
    case '5':
      return { label: '순환', key: 'circular' };
    case '6':
    case '12':
      return { label: '광역', key: 'express' };
    case '11':
      return { label: '직행', key: 'wide' };
    case '13':
      return { label: '공항', key: 'airport' };
    case '14':
      return { label: '마을', key: 'village' };
    case '15':
      return { label: '심야', key: 'night' };
    default:
      return { label: '일반', key: 'normal' };
  }
}

function hhmm(v) {
  const s = String(v == null ? '' : v).trim();
  if (!/^\d{3,4}$/.test(s)) return null;
  const padded = s.padStart(4, '0');
  return padded.slice(0, 2) + ':' + padded.slice(2);
}

/** 서울시 TOPIS / 공공데이터포털 행(row) → 앱 공통 형식 */
function normalizeSeoulRows(rows, stop) {
  const out = [];
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const route = String(row.rtNm || row.busRouteNm || '').trim();
    if (!route) continue;
    const cat = routeCategory(route, row.routeType);
    out.push({
      route,
      dir: String(row.adirection || row.stationNm || '').trim(),
      category: cat.key,
      categoryLabel: cat.label,
      routeId: String(row.busRouteId || ''),
      eta1: parseArrivalMessage(row.arrmsg1, row.traTime1),
      eta2: parseArrivalMessage(row.arrmsg2, row.traTime2),
      low1: String(row.busType1 || '') === '1' || String(row.busType1 || '') === '2',
      last1: String(row.isLast1 || '') === '1',
      last2: String(row.isLast2 || '') === '1',
      first: hhmm(row.firstTm),
      last: hhmm(row.lastTm),
      term: /^\d+$/.test(String(row.term || '')) ? Number(row.term) : null,
      congestion1: Number(row.congetion1) > 0 ? Number(row.congetion1) : null,
      congestion2: Number(row.congetion2) > 0 ? Number(row.congetion2) : null,
      vehicle1: String(row.plainNo1 || '').trim() || null,
      vehicle2: String(row.plainNo2 || '').trim() || null,
      sectNm: String(row.sectNm || '').trim() || null,
      nextStop: String(row.nxtStn || '').trim() || null,
    });
  }
  return out;
}

/** 카카오맵 정류장 JSON → 앱 공통 형식 */
function normalizeKakaoStop(data, stop) {
  const lines = Array.isArray(data && data.lines) ? data.lines : [];
  const out = [];
  for (const line of lines) {
    // 카카오는 "N31 (심야)"처럼 꼬리표를 붙여 준다 — 배지에는 노선 번호만 쓴다.
    const route = String((line && line.name) || '').replace(/\s*\([^)]*\)\s*$/, '').trim();
    if (!route) continue;
    const a = line.arrival || {};
    const key = String(line.busLineType || '').toUpperCase();
    const cat =
      /^N\d/i.test(route)
        ? { label: '심야', key: 'night' }
        : key === 'BLUE'
          ? { label: '간선', key: 'trunk' }
          : key === 'GREEN'
            ? { label: '지선', key: 'branch' }
            : key === 'RED'
              ? { label: '광역', key: 'express' }
              : key === 'YELLOW'
                ? { label: '순환', key: 'circular' }
                : { label: '일반', key: 'normal' };

    const mk = (sec, stops, running) => {
      const s = Number(sec);
      const has = Number.isFinite(s) && s >= 0 && running !== false;
      if (!has) return { msg: '', state: 'none', sec: null, stops: null };
      if (s <= 20) return { msg: '곧 도착', state: 'soon', sec: s, stops: stops ?? null };
      const m = Math.floor(s / 60);
      const r = s % 60;
      const label = m > 0 ? `${m}분${r > 0 ? `${r}초` : ''}후` : `${r}초후`;
      return {
        msg: label + (stops ? `[${stops}번째 전]` : ''),
        state: 'running',
        sec: s,
        stops: Number.isFinite(Number(stops)) ? Number(stops) : null,
      };
    };

    // 카카오의 direction 은 정류장 방면(이 정류장의 진행 방향)이라 노선 종점이 아니다.
    out.push({
      route,
      dir: '',
      category: cat.key,
      categoryLabel: cat.label,
      routeId: String(line.id || ''),
      eta1: mk(a.arrivalTime, a.busStopCount, a.collectStatus !== 'NONE'),
      eta2: mk(a.arrivalTime2, a.busStopCount2, a.collectStatus2 !== 'NONE'),
      low1: false,
      last1: Boolean(a.lastVehicle),
      last2: Boolean(a.lastVehicle2),
      first: null,
      last: null,
      term: null,
      congestion1: null,
      congestion2: null,
      vehicle1: String(a.vehicleNumber || '').trim() || null,
      vehicle2: String(a.vehicleNumber2 || '').trim() || null,
      sectNm: null,
      nextStop: String(a.nextBusStopName || '').trim() || null,
    });
  }
  return out;
}

/* ── 출처별 수집 ────────────────────────────────────────────────────── */

async function fromTopis(stop) {
  const url =
    'https://topis.seoul.go.kr/map/getBusStn.do?url=/getStationByUidDetourAt&arsId=' +
    encodeURIComponent(stop.arsId);
  const data = await fetchJson(url);
  const rows = Array.isArray(data && data.rows) ? data.rows : [];
  if (!rows.length) throw new Error('TOPIS 응답에 도착정보가 없습니다');
  return {
    source: 'topis',
    stopName: String(rows[0].stNm || stop.name),
    stId: String(rows[0].stId || stop.stId),
    routes: normalizeSeoulRows(rows, stop),
  };
}

async function fromKakao(stop) {
  if (!stop.kakaoId) throw new Error('카카오 정류장 ID 없음');
  const url = 'https://map.kakao.com/bus/stop.json?busstopid=' + encodeURIComponent(stop.kakaoId);
  const data = await fetchJson(url);
  const routes = normalizeKakaoStop(data, stop);
  if (!routes.length) throw new Error('카카오 응답에 도착정보가 없습니다');
  return { source: 'kakao', stopName: String(data.name || stop.name), stId: stop.stId, routes };
}

async function fromDataGoKr(stop, env) {
  const key = env && env.SEOUL_BUS_API_KEY;
  if (!key) throw new Error('SEOUL_BUS_API_KEY 미설정');
  const url =
    'https://ws.bus.go.kr/api/rest/stationinfo/getStationByUid?serviceKey=' +
    encodeURIComponent(key) +
    '&arsId=' +
    encodeURIComponent(stop.arsId) +
    '&resultType=json';
  const data = await fetchJson(url);
  const body = data && data.msgBody;
  let list = body && body.itemList;
  if (!list) throw new Error('공공데이터 응답에 도착정보가 없습니다');
  if (!Array.isArray(list)) list = [list];
  return {
    source: 'data.go.kr',
    stopName: String((list[0] && list[0].stNm) || stop.name),
    stId: String((list[0] && list[0].stId) || stop.stId),
    routes: normalizeSeoulRows(list, stop),
  };
}

/* ── 라우팅 ─────────────────────────────────────────────────────────── */

async function collect(stop, env) {
  const attempts = [];
  for (const fn of [fromTopis, fromKakao]) {
    try {
      const got = await fn(stop);
      if (got && got.routes.length) return { ...got, attempts };
    } catch (e) {
      attempts.push({ source: fn.name.replace('from', '').toLowerCase(), error: String(e && e.message) });
    }
  }
  try {
    const got = await fromDataGoKr(stop, env);
    if (got && got.routes.length) return { ...got, attempts };
  } catch (e) {
    attempts.push({ source: 'data.go.kr', error: String(e && e.message) });
  }
  const err = new Error('모든 출처 실패');
  err.attempts = attempts;
  throw err;
}

async function handleArrivals(stop, env) {
  const now = Date.now();
  const hit = cache.get(stop.arsId);
  if (hit && now - hit.at < CACHE_TTL_MS) return { payload: hit.payload, cached: true };
  if (inflight.has(stop.arsId)) {
    const payload = await inflight.get(stop.arsId);
    return { payload, cached: true };
  }

  const task = (async () => {
    const got = await collect(stop, env);
    const payload = {
      ok: true,
      stop: {
        name: got.stopName || stop.name,
        arsId: stop.arsId,
        stId: got.stId,
        direction: stop.direction,
      },
      source: got.source,
      fetchedAt: new Date().toISOString(),
      routes: got.routes,
      attempts: got.attempts.length ? got.attempts : undefined,
    };
    cache.set(stop.arsId, { at: Date.now(), payload });
    return payload;
  })();

  inflight.set(stop.arsId, task);
  try {
    const payload = await task;
    return { payload, cached: false };
  } finally {
    inflight.delete(stop.arsId);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return jsonResponse({ ok: false, error: 'GET만 지원합니다' }, 405);
    }
    if (url.pathname === '/health' || url.pathname === '/healthz') {
      return jsonResponse({ ok: true, service: 'bus-stop-25159', stops: Object.keys(STOPS) });
    }

    const arsId = (url.searchParams.get('arsId') || DEFAULT_ARS_ID).trim();
    const stop = STOPS[arsId];
    if (!stop) {
      return jsonResponse(
        { ok: false, error: '지원하지 않는 정류장입니다', supported: Object.keys(STOPS) },
        400,
      );
    }

    try {
      const { payload, cached } = await handleArrivals(stop, env);
      return jsonResponse(payload, 200, { 'x-cache': cached ? 'hit' : 'miss' });
    } catch (e) {
      return jsonResponse(
        {
          ok: false,
          error: '도착정보를 가져오지 못했습니다',
          detail: String((e && e.message) || e),
          attempts: (e && e.attempts) || [],
          fetchedAt: new Date().toISOString(),
        },
        502,
      );
    }
  },
};
