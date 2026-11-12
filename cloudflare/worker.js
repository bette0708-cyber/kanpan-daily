// 看盤系統的雲端小幫手（Cloudflare Worker）
//
// 1. GET  /?ex_ch=tse_2330.tw|otc_5386.tw   即時報價轉接（證交所 MIS）
//      網頁不能直接讀證交所的即時報價，由這裡代抓。只接受台股代號、一次最多 40 檔、
//      同樣查詢 4 秒內共用結果（證交所約每 5 秒 3 次的流量限制）。
// 2. GET  /watchlist                         讀目前的自選股（GitHub repo 裡的 自選股.txt）
//    POST /watchlist {pin, action, code, name}  新增／刪除自選股（要 PIN 碼）
//      改完會 commit 到 GitHub，GitHub Actions 偵測到 自選股.txt 變動就自動重跑、更新網頁。
// 3. GET  /status                            最近一次雲端更新的狀態
//
// 需要的密鑰（用 `npx wrangler secret put 名稱` 設定，不寫在程式裡）：
//   GH_TOKEN   GitHub 細粒度金鑰：只對 kanpan-daily 這個 repo 有 Contents 讀寫權限
//   WATCH_PIN  自選股管理的 PIN 碼
// 只回應自己的網頁（ALLOWED_ORIGINS）。

const ALLOWED_ORIGINS = [
  "https://bette0708-cyber.github.io",
  "http://localhost:8765",
  "http://127.0.0.1:8765",
];
const SYMBOL = /^(tse|otc)_[0-9a-z]{2,8}\.tw$/;
const STOCK_CODE = /^(\d{4}|00\d{2,4}[A-Z]?)$/;
const CACHE_SECONDS = 4;
const FILE = "自選股.txt";

function cors(origin) {
  const ok = ALLOWED_ORIGINS.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...cors(origin) },
  });
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(origin) });
    const url = new URL(request.url);
    try {
      if (url.pathname === "/watchlist") {
        if (request.method === "GET") return json(await readList(env), 200, origin);
        if (request.method === "POST") return await editList(request, env, origin);
        return json({ error: "不支援的操作" }, 405, origin);
      }
      if (url.pathname === "/status" && request.method === "GET") return json(await lastRun(env), 200, origin);
      if (url.pathname === "/" && request.method === "GET") return await quote(url, origin, ctx);
      return json({ error: "找不到這個功能" }, 404, origin);
    } catch (e) {
      return json({ error: e.message || "發生錯誤" }, 500, origin);
    }
  },
};

// ------------------------------------------------------------------ 即時報價
async function quote(url, origin, ctx) {
  const list = (url.searchParams.get("ex_ch") || "").toLowerCase().split("|").filter(Boolean);
  if (!list.length || list.length > 40 || !list.every((s) => SYMBOL.test(s))) {
    return json({ error: "代號格式不對，例：tse_2330.tw|otc_5386.tw" }, 400, origin);
  }
  const exCh = [...new Set(list)].sort().join("|");
  const cache = caches.default;
  const key = new Request(`https://kanpan-cache.local/q?ex_ch=${encodeURIComponent(exCh)}`);
  const hit = await cache.match(key);
  if (hit) {
    return new Response(await hit.text(), {
      headers: { "Content-Type": "application/json; charset=utf-8", "X-Cache": "HIT", ...cors(origin) },
    });
  }
  const upstream = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0&_=${Date.now()}`;
  const r = await fetch(upstream, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Referer": "https://mis.twse.com.tw/stock/index.jsp",
      "Accept": "application/json",
    },
  });
  if (!r.ok) return json({ error: `證交所回應 ${r.status}` }, 502, origin);
  const body = await r.text();
  if (!body.trim().startsWith("{")) return json({ error: "證交所回傳格式不正確" }, 502, origin);
  const res = new Response(body, {
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": `public, max-age=${CACHE_SECONDS}` },
  });
  ctx.waitUntil(cache.put(key, res.clone()));
  return new Response(body, {
    headers: { "Content-Type": "application/json; charset=utf-8", "X-Cache": "MISS", ...cors(origin) },
  });
}

// ------------------------------------------------------------------ GitHub 自選股
const b64enc = (s) => {
  let bin = "";
  for (const b of new TextEncoder().encode(s)) bin += String.fromCharCode(b);
  return btoa(bin);
};
const b64dec = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\s/g, "")), (c) => c.charCodeAt(0)));
const codeOf = (line) => (line.split("#")[0].trim().split(/\s+/)[0] || "");

async function gh(env, path, init = {}) {
  if (!env.GH_TOKEN) throw new Error("還沒設定 GitHub 金鑰（GH_TOKEN）");
  const r = await fetch(`https://api.github.com/repos/${env.REPO}${path}`, {
    ...init,
    headers: {
      "Authorization": `Bearer ${env.GH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "User-Agent": "kanpan-proxy",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
  return r;
}

async function getFile(env) {
  const r = await gh(env, `/contents/${encodeURIComponent(FILE)}?ref=main`);
  if (!r.ok) throw new Error(`讀取 GitHub 自選股失敗（${r.status}）`);
  const j = await r.json();
  return { text: b64dec(j.content).replace(/^﻿/, ""), sha: j.sha };
}

function parse(text) {
  return text.split(/\r?\n/).filter((l) => codeOf(l)).map((l) => ({
    code: codeOf(l), name: (l.split("#")[1] || "").trim(),
  }));
}

async function readList(env) {
  const { text } = await getFile(env);
  return { items: parse(text) };
}

async function editList(request, env, origin) {
  let body;
  try { body = await request.json(); } catch { return json({ error: "資料格式不對" }, 400, origin); }
  const { pin, action } = body;
  const code = String(body.code || "").trim().toUpperCase();
  const name = String(body.name || "").replace(/[#\r\n]/g, "").trim().slice(0, 20);
  if (!env.WATCH_PIN) return json({ error: "還沒設定 PIN 碼（WATCH_PIN）" }, 500, origin);
  if (typeof pin !== "string" || pin !== env.WATCH_PIN) {
    await new Promise((r) => setTimeout(r, 1500));   // 放慢猜密碼
    return json({ error: "PIN 碼不對" }, 403, origin);
  }
  if (!["add", "remove"].includes(action)) return json({ error: "不支援的操作" }, 400, origin);
  if (!STOCK_CODE.test(code)) return json({ error: "股票代號格式不對（4 碼股票或 00 開頭 ETF）" }, 400, origin);

  for (let attempt = 0; attempt < 2; attempt++) {
    const { text, sha } = await getFile(env);
    const lines = text.split(/\r?\n/);
    const exists = lines.some((l) => codeOf(l) === code);
    let next, msg;
    if (action === "add") {
      if (exists) return json({ ok: true, changed: false, message: `${name || code} 已經在清單裡`, items: parse(text) }, 200, origin);
      while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
      lines.push(`${code}  # ${name || code}`);
      next = lines.join("\n") + "\n";
      msg = `自選股：新增 ${name} ${code}（網頁）`;
    } else {
      if (!exists) return json({ ok: true, changed: false, message: `${code} 不在清單裡`, items: parse(text) }, 200, origin);
      next = lines.filter((l) => codeOf(l) !== code).join("\n").replace(/\n*$/, "\n");
      msg = `自選股：刪除 ${name} ${code}（網頁）`;
    }
    const r = await gh(env, `/contents/${encodeURIComponent(FILE)}`, {
      method: "PUT",
      body: JSON.stringify({ message: msg, content: b64enc(next), sha, branch: "main" }),
    });
    if (r.ok) return json({ ok: true, changed: true, message: msg.replace("自選股：", "已"), items: parse(next) }, 200, origin);
    if (r.status !== 409) throw new Error(`寫入 GitHub 失敗（${r.status}）`);
  }
  return json({ error: "剛好有其他更新在進行，請再試一次" }, 409, origin);
}

async function lastRun(env) {
  const r = await gh(env, "/actions/runs?per_page=1");
  if (!r.ok) throw new Error(`讀取更新狀態失敗（${r.status}）`);
  const run = (await r.json()).workflow_runs?.[0];
  if (!run) return { status: "none" };
  return { status: run.status, conclusion: run.conclusion, event: run.event,
           created: run.created_at, updated: run.updated_at, title: run.display_title };
}
