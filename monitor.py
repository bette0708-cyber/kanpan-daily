"""盤中即時監看頁：盯住昨天（含）以前算好的關鍵價位，盤中每 5 秒比對即時報價。

不是每 5 秒重新預測——教學的方法看日K，盤中K線還沒收完，訊號要收盤才算數。
這一頁回答的是：
  - 待確認的買點：今天股價有沒有站上訊號那天的收盤、量有沒有跟上（＝正在確認）
  - 任何訊號：有沒有跌破停損價（買點）／站回訊號高點（賣點）
  - 大盤：即時漲跌和早上的評估方向是否一致

即時報價來源：證交所 MIS（約每 5 秒更新），經由 Cloudflare Worker 轉接（cloudflare/worker.js）。
轉接網址設定在 監看設定.json 的 "proxy"。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import report
from patterns import BUY

HERE = Path(__file__).resolve().parent
CONF_FILE = HERE / "監看設定.json"


def ex_code(sym: str) -> str:
    if sym == "^TWII":
        return "tse_t00.tw"
    code, _, suf = sym.partition(".")
    return f"{'otc' if suf == 'TWO' else 'tse'}_{code.lower()}.tw"


def watch_item(sym: str, name: str, d: pd.DataFrame, sigs: list, m: dict) -> dict:
    last = d.iloc[-1]
    sig = None
    for s in reversed(sigs):
        age = len(d) - 1 - d.index.get_loc(s.date)
        if age > 4:
            break
        if s.status in ("待確認", "已確認"):
            sig = {"side": "buy" if s.side == BUY else "sell", "date": f"{s.date:%m/%d}", "status": s.status,
                   "stop": round(float(s.stop), 2), "close": round(float(d.at[s.date, "close"]), 2),
                   "patterns": "、".join(p.name for p in s.patterns)}
            break
    vol5 = None
    if sym != "^TWII" and d["volume"].tail(5).sum() > 0:
        vol5 = round(float(d["volume"].tail(5).mean()) / 1000, 1)   # 股 → 張
    f = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 2)
    return {"sym": sym, "ex": ex_code(sym), "name": name, "date": f"{d.index[-1]:%m/%d}",
            "prev": f(last["close"]), "ma5": f(last.get("ma5")), "ma20": f(last.get("ma20")), "vol5": vol5,
            "total": round(m["total"]), "short": round(m["short"]), "mid": round(m["mid"]),
            "verdict": m["verdict"][0], "sig": sig}


def load_conf() -> dict:
    try:
        return json.loads(CONF_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"proxy": ""}


EXTRA_CSS = """
.live{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);flex-wrap:wrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none}
.dot.on{background:var(--dn);animation:blink 1.6s infinite}
.dot.err{background:var(--up)}
@keyframes blink{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.dot.on{animation:none}}
.btn{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--surface);color:var(--ink);
  border-radius:999px;padding:4px 12px;cursor:pointer}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.alerts{list-style:none;margin:0;padding:0}
.alerts li{display:flex;gap:8px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--line);font-size:14px}
.alerts li:last-child{border:0}
.alerts time{font-family:var(--mono);font-size:12px;color:var(--muted);flex:none}
.cards{display:grid;gap:10px;grid-template-columns:1fr}
.card{border:1px solid var(--line);border-radius:12px;padding:10px 12px;min-width:0}
.card.alert-stop{border-color:color-mix(in srgb,var(--dn) 60%,transparent);background:color-mix(in srgb,var(--dn) 6%,transparent)}
.card.alert-confirm{border-color:color-mix(in srgb,var(--up) 60%,transparent);background:color-mix(in srgb,var(--up) 6%,transparent)}
.c-top{display:flex;align-items:baseline;gap:8px}
.c-top b.nm{flex:1;font-size:16px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.c-top .px{font-size:18px}
.c-sub{display:flex;flex-wrap:wrap;gap:2px 12px;font-size:12px;color:var(--muted);margin-top:2px}
.c-status{margin-top:6px;font-size:14px}
.lv{position:relative;height:28px;margin:8px 4px 2px}
.lv .track{position:absolute;left:0;right:0;top:12px;height:4px;border-radius:2px;background:var(--line)}
.lv .mk{position:absolute;top:4px;transform:translateX(-50%);font-size:10px;color:var(--muted);white-space:nowrap}
.lv .mk::after{content:"";position:absolute;left:50%;top:12px;width:2px;height:12px;background:currentColor;transform:translateX(-50%)}
.lv .mk.stop{color:var(--dn)}.lv .mk.trig{color:var(--mid)}
.lv .now{position:absolute;top:8px;width:12px;height:12px;border-radius:50%;transform:translateX(-50%);
  background:var(--ink);border:2px solid var(--surface)}
.idx{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.idx div{border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.idx span{display:block;font-size:12px;color:var(--muted)}
.idx b{font-size:18px}
@media (min-width:760px){.cards{grid-template-columns:1fr 1fr}.idx{grid-template-columns:repeat(4,1fr)}}
"""

JS = r"""
(function(){
  const W = WATCH, PROXY = (CONF.proxy || '').replace(/\/$/, '');
  const ex = W.map(w => w.ex).join('|');
  const $ = s => document.querySelector(s);
  const q = {}, prevKind = {}, alerts = [];
  let timer = null, paused = false;

  const tpe = () => { const d = new Date(); return new Date(d.getTime() + (d.getTimezoneOffset() + 480) * 60000); };
  const hhmm = d => d.toTimeString().slice(0, 8);
  function open(){ const t = tpe(), wd = t.getDay(); if (wd === 0 || wd === 6) return false;
    const m = t.getHours() * 60 + t.getMinutes(); return m >= 8 * 60 + 30 && m <= 13 * 60 + 35; }
  const n = s => { const v = parseFloat(s); return isFinite(v) && v > 0 ? v : null; };
  const fmt = (v, d = 2) => v == null ? '—' : v.toLocaleString('zh-TW', {minimumFractionDigits: d, maximumFractionDigits: d});
  const pct = (a, b) => (a != null && b) ? (a / b - 1) * 100 : null;
  const pctHtml = v => v == null ? '<span class="num">—</span>' :
    `<span class="num ${v > 0 ? 'up' : v < 0 ? 'dn' : ''}">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;

  function price(m){
    let p = n(m.z);
    if (p == null) { const b = n((m.b || '').split('_')[0]), a = n((m.a || '').split('_')[0]);
      p = b && a ? (a + b) / 2 : (b || a); }
    return p;
  }
  function candle(o, h, l, p){
    if (!o || !h || !l || !p) return '—';
    const r = h - l; if (r <= 0) return '一字線';
    const body = Math.abs(p - o), up = h - Math.max(o, p), lo = Math.min(o, p) - l;
    if (body <= 0.1 * r) return '十字線（多空拉鋸）';
    const c = p > o ? '紅' : '黑';
    if (lo >= 2 * body && up <= body) return `錘子${c}K（下影長，有買盤撐）`;
    if (up >= 2 * body && lo <= body) return `倒錘${c}K（上影長，有賣壓）`;
    if (up <= 0.1 * r && lo <= 0.1 * r) return p > o ? '長紅（買方主導）' : '長黑（賣方主導）';
    return p > o ? '小紅' : '小黑';
  }
  function pace(v){
    const t = tpe(); let m = t.getHours() * 60 + t.getMinutes() - 9 * 60;
    m = Math.max(10, Math.min(270, m)); return v ? v / (m / 270) : null;
  }
  function judge(w, m){
    const p = m.p, s = w.sig;
    const vp = w.vol5 && m.v ? pace(m.v) / w.vol5 : null;
    const volTxt = vp == null ? '' : `・預估量 ${(vp * 100).toFixed(0)}% 均量`;
    if (!s) {
      if (w.ma20 && p) return {k: 'none', cls: '', txt: `近期無K線訊號・${p >= w.ma20 ? '在' : '在'}月線 ${fmt(w.ma20)} ${p >= w.ma20 ? '之上' : '之下'}${volTxt}`};
      return {k: 'none', cls: '', txt: '近期無K線訊號'};
    }
    if (s.side === 'buy') {
      if (p < s.stop) return {k: 'stop', cls: 'dn', txt: `⚠ 跌破停損 ${fmt(s.stop)}（${s.date} 買點：${s.patterns}）`};
      if (s.status === '已確認') return {k: 'hold', cls: 'up', txt: `買點已確認（${s.date}），守住停損 ${fmt(s.stop)}${volTxt}`};
      if (p > s.close && (vp == null || vp >= 0.9)) return {k: 'confirm', cls: 'up', txt: `✔ 確認中：站上 ${fmt(s.close)}、量也跟上${volTxt}`};
      if (p > s.close) return {k: 'price', cls: 'mid', txt: `價格已站上 ${fmt(s.close)}，量還不夠${volTxt}`};
      return {k: 'wait', cls: 'mid', txt: `買點待確認：要收在 ${fmt(s.close)} 之上${volTxt}`};
    }
    if (p > s.stop) return {k: 'stop', cls: 'up', txt: `⚠ 站回 ${fmt(s.stop)}，${s.date} 賣點失效`};
    if (s.status === '已確認') return {k: 'hold', cls: 'dn', txt: `賣點已確認（${s.date}），站回 ${fmt(s.stop)} 才失效${volTxt}`};
    if (p < s.close && (vp == null || vp >= 0.9)) return {k: 'confirm', cls: 'dn', txt: `✔ 賣點確認中：跌破 ${fmt(s.close)}、量也跟上${volTxt}`};
    if (p < s.close) return {k: 'price', cls: 'mid', txt: `價格已跌破 ${fmt(s.close)}，量還不夠${volTxt}`};
    return {k: 'wait', cls: 'mid', txt: `賣點待確認：要收在 ${fmt(s.close)} 之下${volTxt}`};
  }
  function levels(w, m){
    const s = w.sig, p = m.p; if (!p) return '';
    const pts = [p, m.h, m.l, w.prev].filter(Boolean); if (s) pts.push(s.stop, s.close);
    let lo = Math.min(...pts), hi = Math.max(...pts); const pad = (hi - lo) * 0.08 || p * 0.01; lo -= pad; hi += pad;
    const x = v => ((v - lo) / (hi - lo) * 100).toFixed(1) + '%';
    let h = `<div class="lv" aria-hidden="true"><span class="track"></span>`;
    if (s) h += `<span class="mk stop" style="left:${x(s.stop)}">停損</span><span class="mk trig" style="left:${x(s.close)}">確認</span>`;
    h += `<span class="mk" style="left:${x(w.prev)}">昨收</span><span class="now" style="left:${x(p)}"></span></div>`;
    return h;
  }

  function render(){
    const t = $('#t00');
    const idx = W.find(w => w.sym === '^TWII'), mi = idx && q[idx.ex];
    if (mi) {
      const c = pct(mi.p, mi.y);
      const dir = OUTLOOK.score == null ? null : (OUTLOOK.score > 0 ? 1 : -1);
      const ok = dir == null || c == null ? '—' : (Math.sign(c) === dir ? '✔ 方向一致' : '✘ 方向相反');
      t.innerHTML = `<div><span>加權指數</span><b class="num">${fmt(mi.p)}</b></div><div><span>漲跌</span><b>${pctHtml(c)}</b></div>`
        + `<div><span>早上評估</span><b>${OUTLOOK.label || '—'}</b></div><div><span>目前比對</span><b>${ok}</b></div>`;
    }
    const rows = W.filter(w => w.sym !== '^TWII').map(w => {
      const m = q[w.ex]; if (!m || !m.p) return {w, html: `<div class="card"><div class="c-top"><b class="nm">${w.name}</b><span class="muted">等待報價…</span></div></div>`, rank: 9};
      const j = judge(w, m), c = pct(m.p, m.y || w.prev);
      // 第一次拿到報價時就已是「停損／確認中」也列出來；之後只在狀態改變時提醒（並震動）
      if (prevKind[w.ex] !== j.k && (j.k === 'stop' || j.k === 'confirm')) addAlert(w, j, prevKind[w.ex] !== undefined);
      prevKind[w.ex] = j.k;
      const rank = {stop: 0, confirm: 1, price: 2, wait: 3, hold: 4, none: 5}[j.k];
      return {w, rank, html: `<div class="card ${j.k === 'stop' ? 'alert-stop' : j.k === 'confirm' ? 'alert-confirm' : ''}">
        <div class="c-top"><b class="nm">${w.name}</b><span class="num px">${fmt(m.p)}</span>${pctHtml(c)}</div>
        <div class="c-sub"><span>盤中K線：${candle(m.o, m.h, m.l, m.p)}</span><span>高 ${fmt(m.h)}・低 ${fmt(m.l)}</span>
          <span>量 ${m.v ? m.v.toLocaleString('zh-TW') : '—'} 張</span><span>綜合 ${w.total} 分</span><span>${m.t || ''}</span></div>
        ${levels(w, m)}
        <p class="c-status ${j.cls}">${j.txt}</p></div>`};
    }).sort((a, b) => a.rank - b.rank);
    $('#cards').innerHTML = rows.map(r => r.html).join('');
    $('#alerts').innerHTML = alerts.length ? alerts.map(a => `<li><time>${a.time}</time><span class="${a.cls}"><b>${a.name}</b>　${a.txt}</span></li>`).join('')
      : '<li class="muted">目前沒有需要注意的變化。跌破停損、或待確認的訊號開始確認時，會列在這裡。</li>';
  }
  function addAlert(w, j, changed){
    alerts.unshift({time: hhmm(tpe()), name: w.name, txt: j.txt, cls: j.cls});
    alerts.splice(30);
    if (changed) { try { navigator.vibrate && navigator.vibrate(200); } catch (e) {}
      document.title = '⚠ ' + w.name + '｜即時監看'; setTimeout(() => document.title = '即時監看', 8000); }
  }

  async function poll(){
    if (!PROXY) { setState('err', '尚未設定報價轉接服務'); return; }
    const ctl = new AbortController(), to = setTimeout(() => ctl.abort(), 12000);   // 12 秒沒回應就放棄、下一輪重試
    try {
      const r = await fetch(`${PROXY}/?ex_ch=${encodeURIComponent(ex)}`, {cache: 'no-store', signal: ctl.signal});
      const j = await r.json();
      if (!r.ok || !j.msgArray) throw new Error(j.error || r.status);
      for (const m of j.msgArray) {
        const key = `${m.ex}_${m.ch}`.toLowerCase();
        const p = price(m);
        q[key] = {p: p ?? q[key]?.p, y: n(m.y), o: n(m.o), h: n(m.h), l: n(m.l), v: n(m.v), t: m.t};
      }
      setState(open() ? 'on' : '', open() ? `即時更新中・${hhmm(tpe())}` : `非交易時間・顯示最後報價（${hhmm(tpe())}）`);
      render();
    } catch (e) {
      setState('err', e.name === 'AbortError' ? '報價回應太慢，馬上重試…' : `報價暫時抓不到（${e.message}），會自動重試`);
    } finally { clearTimeout(to); }
  }
  function setState(cls, txt){ $('#dot').className = 'dot ' + cls; $('#state').textContent = txt; }
  // 一打開就先抓一次（有些 App 內建瀏覽器一開始會把頁面當成「在背景」）；
  // 在背景時放慢成每分鐘一次，回到畫面立刻更新。
  let busy = false;
  async function loop(){
    clearTimeout(timer);
    if (paused || busy) return;
    busy = true;
    try { await poll(); } finally {
      busy = false;
      if (!paused) timer = setTimeout(loop, document.hidden || !open() ? 60000 : 5000);
    }
  }
  $('#pause').addEventListener('click', () => {
    paused = !paused; $('#pause').textContent = paused ? '繼續更新' : '暫停';
    if (!paused) loop(); else { clearTimeout(timer); setState('', '已暫停'); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loop(); });
  window.addEventListener('pageshow', () => loop());
  window.addEventListener('focus', () => loop());
  render(); loop();
})();
"""


def build_html(watch: list[dict], o: dict, now) -> str:
    conf = load_conf()
    outlook = {"label": None, "score": None}
    if o.get("target") is not None:
        outlook = {"label": f"{o['label'][0]}（{o['target']:%m/%d}）", "score": round(float(o["score"]), 3)}
    elif o.get("provisional"):
        outlook = {"label": f"{o['label'][0]}（暫估）", "score": round(float(o["score"]), 3)}
    body = f"""
<section class="block" id="live" data-nav="即時">
 <div class="live"><span class="dot" id="dot"></span><span id="state">連線中…</span>
  <button class="btn" id="pause" type="button">暫停</button></div>
 <div class="idx" id="t00" style="margin-top:10px"><div><span>加權指數</span><b class="num">—</b></div></div>
 <p class="muted">盤中每 5 秒更新（證交所即時報價）。關鍵價位是用 {report.esc(watch[0]['date'] if watch else '')} 收盤算好的；盤中K線還沒收完，訊號要等收盤才算數。</p>
</section>
<section class="block" id="alertbox" data-nav="提醒"><h2 class="sec-title">提醒</h2><ul class="alerts" id="alerts"></ul></section>
<section class="block" id="list" data-nav="自選股"><h2 class="sec-title">自選股即時狀態</h2>
 <p class="muted">排序：跌破停損 → 正在確認 → 價到量不足 → 待確認 → 已確認 → 無訊號。橫條上「停損」「確認」是關鍵價位，黑點是現價。</p>
 <div class="cards" id="cards"></div>
</section>
<script>const WATCH = {json.dumps(watch, ensure_ascii=False)};const CONF = {json.dumps({"proxy": conf.get("proxy", "")})};
const OUTLOOK = {json.dumps(outlook, ensure_ascii=False)};</script>
<script>{JS}</script>"""
    return report.shell("即時監看", f"價位基準 {now:%m/%d %H:%M}", body, extra_css=EXTRA_CSS)
