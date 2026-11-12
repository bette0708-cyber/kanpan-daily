// 看盤監看用的報價轉接服務（Cloudflare Worker）
//
// 網頁不能直接讀證交所的即時報價（沒有開放跨網站讀取），所以由這裡代抓再轉給網頁。
// 安全限制：
//   - 只轉接證交所 MIS 即時報價，不能拿來抓別的網站
//   - 只接受台股代號格式，一次最多 40 檔
//   - 同樣的查詢 4 秒內共用同一份結果，避免超過證交所流量限制（約每 5 秒 3 次）
//   - 只回應自己的網頁（ALLOWED_ORIGINS）

const ALLOWED_ORIGINS = [
  "https://bette0708-cyber.github.io",
  "http://localhost:8765",
  "http://127.0.0.1:8765",
];
const SYMBOL = /^(tse|otc)_[0-9a-z]{2,8}\.tw$/;
const CACHE_SECONDS = 4;

function cors(origin) {
  const ok = ALLOWED_ORIGINS.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Vary": "Origin",
  };
}

function json(body, status, origin, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...cors(origin), ...extra },
  });
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(origin) });
    if (request.method !== "GET") return json({ error: "只接受 GET" }, 405, origin);

    const url = new URL(request.url);
    const list = (url.searchParams.get("ex_ch") || "").toLowerCase().split("|").filter(Boolean);
    if (!list.length || list.length > 40 || !list.every((s) => SYMBOL.test(s))) {
      return json({ error: "代號格式不對，例：tse_2330.tw|otc_5386.tw" }, 400, origin);
    }
    const exCh = [...new Set(list)].sort().join("|");

    // 4 秒共用快取（以排序後的代號清單當 key）
    const cache = caches.default;
    const key = new Request(`https://kanpan-cache.local/q?ex_ch=${encodeURIComponent(exCh)}`);
    const hit = await cache.match(key);
    if (hit) {
      const body = await hit.text();
      return new Response(body, {
        headers: { "Content-Type": "application/json; charset=utf-8", "X-Cache": "HIT", ...cors(origin) },
      });
    }

    const upstream = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0&_=${Date.now()}`;
    let r;
    try {
      r = await fetch(upstream, {
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          "Referer": "https://mis.twse.com.tw/stock/index.jsp",
          "Accept": "application/json",
        },
      });
    } catch (e) {
      return json({ error: "連不到證交所" }, 502, origin);
    }
    if (!r.ok) return json({ error: `證交所回應 ${r.status}` }, 502, origin);
    const body = await r.text();
    if (!body.trim().startsWith("{")) return json({ error: "證交所回傳格式不正確" }, 502, origin);

    const res = new Response(body, {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": `public, max-age=${CACHE_SECONDS}`,
      },
    });
    ctx.waitUntil(cache.put(key, res.clone()));
    return new Response(body, {
      headers: { "Content-Type": "application/json; charset=utf-8", "X-Cache": "MISS", ...cors(origin) },
    });
  },
};
