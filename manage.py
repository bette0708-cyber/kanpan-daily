"""自選股管理頁：在手機上直接新增／刪除自選股。

流程：網頁 → Cloudflare Worker（驗 PIN）→ 改 GitHub 上的 自選股.txt
     → GitHub Actions 偵測到變動自動重跑 → 約 3 分鐘後日報／監看／週報都更新。
"""
from __future__ import annotations

import json

import report
from data import stock_list
from monitor import load_conf

EXTRA_CSS = """
.field{display:flex;gap:8px;margin:8px 0}
.field input{flex:1;min-width:0;font:inherit;font-size:16px;padding:9px 12px;border:1px solid var(--line);
  border-radius:10px;background:var(--surface);color:var(--ink)}
.field input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.btn{font:inherit;font-size:15px;border:1px solid var(--line);background:var(--surface);color:var(--ink);
  border-radius:10px;padding:9px 14px;cursor:pointer;white-space:nowrap}
.btn.primary{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.btn.danger{color:var(--dn);border-color:color-mix(in srgb,var(--dn) 50%,transparent)}
.btn:disabled{opacity:.5;cursor:default}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.sug{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.sug:empty{display:none}
.sug button{display:flex;width:100%;justify-content:space-between;gap:8px;font:inherit;text-align:left;
  padding:10px 12px;border:0;border-bottom:1px solid var(--line);background:var(--surface);color:var(--ink);cursor:pointer}
.sug li:last-child button{border-bottom:0}
.sug small{display:inline;color:var(--muted)}
.msg{min-height:1.4em;font-size:14px;margin:6px 0}
.msg.ok{color:var(--up)}.msg.err{color:var(--dn)}
.wl{list-style:none;margin:0;padding:0}
.wl li{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--line)}
.wl li:last-child{border:0}
.wl .nm{flex:1;min-width:0}
.wl .code{margin-left:6px}
.pinrow{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
"""

JS = r"""
(function(){
  const API = (CONF.proxy || '').replace(/\/$/, '');
  const $ = s => document.querySelector(s);
  const byCode = new Map(STOCKS.map(s => [s[0], s]));
  let items = [], pin = '';
  try { pin = localStorage.getItem('kanpan-pin') || ''; } catch (e) {}

  function msg(t, cls){ const m = $('#msg'); m.textContent = t; m.className = 'msg ' + (cls || ''); }
  function showPin(){
    $('#pinbox').hidden = !!pin; $('#pinset').hidden = !pin;
  }
  function renderList(){
    const ul = $('#wl');
    if (!items.length) { ul.innerHTML = '<li class="muted">清單是空的</li>'; return; }
    ul.innerHTML = items.map(it => `<li><span class="nm"><b>${esc(it.name || it.code)}</b><span class="code num muted">${esc(it.code)}</span></span>
      <button class="btn danger" type="button" data-code="${esc(it.code)}" data-name="${esc(it.name)}">刪除</button></li>`).join('');
  }
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function load(){
    try {
      const r = await fetch(API + '/watchlist', {cache: 'no-store'});
      const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status);
      items = j.items; renderList();
    } catch (e) { $('#wl').innerHTML = `<li class="msg err">讀不到清單：${esc(e.message)}</li>`; }
  }

  // 搜尋建議：代號開頭或名稱包含
  $('#q').addEventListener('input', () => {
    const v = $('#q').value.trim().toUpperCase();
    if (!v) { $('#sug').innerHTML = ''; return; }
    const hit = STOCKS.filter(s => s[0].startsWith(v) || s[1].toUpperCase().includes(v)).slice(0, 8);
    $('#sug').innerHTML = hit.map(s => `<li><button type="button" data-code="${s[0]}"><span><b>${esc(s[1])}</b> <span class="num">${s[0]}</span></span>
      <small>${s[2] === 'otc' ? '上櫃' : '上市'}${items.some(i => i.code === s[0]) ? '・已在清單' : ''}</small></button></li>`).join('')
      || '<li class="muted" style="padding:10px 12px">找不到這檔股票</li>';
  });
  $('#sug').addEventListener('click', e => {
    const b = e.target.closest('button[data-code]'); if (!b) return;
    const s = byCode.get(b.dataset.code); $('#q').value = `${s[0]} ${s[1]}`; $('#sug').innerHTML = '';
    send('add', s[0], s[1]);
  });
  $('#addform').addEventListener('submit', e => {
    e.preventDefault();
    const v = $('#q').value.trim(); if (!v) return;
    const code = v.split(/\s+/)[0].toUpperCase();
    let s = byCode.get(code) || STOCKS.find(x => x[1] === v);
    if (!s) { msg('找不到這檔股票，請從下方建議清單點選', 'err'); return; }
    $('#sug').innerHTML = ''; send('add', s[0], s[1]);
  });
  // 刪除：按一次變「確定刪除？」，再按一次才送出
  $('#wl').addEventListener('click', e => {
    const b = e.target.closest('button[data-code]'); if (!b) return;
    if (b.dataset.armed) { send('remove', b.dataset.code, b.dataset.name); return; }
    $('#wl').querySelectorAll('button[data-armed]').forEach(x => { delete x.dataset.armed; x.textContent = '刪除'; });
    b.dataset.armed = '1'; b.textContent = '確定刪除？';
  });
  $('#pinbox').addEventListener('submit', e => {
    e.preventDefault(); pin = $('#pin').value.trim();
    if ($('#remember').checked) { try { localStorage.setItem('kanpan-pin', pin); } catch (x) {} }
    showPin(); msg('PIN 已設定，可以開始增刪', 'ok');
  });
  $('#forget').addEventListener('click', () => { pin = ''; try { localStorage.removeItem('kanpan-pin'); } catch (e) {} showPin(); });

  async function send(action, code, name){
    if (!pin) { msg('請先輸入 PIN 碼', 'err'); $('#pin').focus(); return; }
    msg(action === 'add' ? `加入 ${name}…` : `刪除 ${name}…`);
    document.querySelectorAll('.btn').forEach(b => b.disabled = true);
    try {
      const r = await fetch(API + '/watchlist', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pin, action, code, name})});
      const j = await r.json();
      if (r.status === 403) { pin = ''; try { localStorage.removeItem('kanpan-pin'); } catch (e) {} showPin(); }
      if (!r.ok) throw new Error(j.error || r.status);
      items = j.items; renderList(); $('#q').value = '';
      msg(j.changed ? `${j.message}。約 3 分鐘後日報、監看頁會更新。` : j.message, 'ok');
      if (j.changed) watchRun();
    } catch (e) { msg(e.message, 'err'); }
    document.querySelectorAll('.btn').forEach(b => b.disabled = false);
  }

  let runTimer = null;
  async function status(){
    try {
      const r = await fetch(API + '/status', {cache: 'no-store'}); const j = await r.json();
      const t = j.updated ? new Date(j.updated).toLocaleTimeString('zh-TW', {hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Taipei'}) : '';
      if (j.status === 'completed') {
        $('#run').textContent = j.conclusion === 'success' ? `最近一次雲端更新：${t} 完成 ✔` : `最近一次雲端更新：${t} 失敗 ✘`;
        return true;
      }
      $('#run').textContent = `雲端更新中…（${t} 開始，約 2～3 分鐘）`;
    } catch (e) { $('#run').textContent = '讀不到雲端更新狀態'; return true; }
    return false;
  }
  function watchRun(){
    clearTimeout(runTimer);
    let n = 0;
    const tick = async () => { const done = await status(); if ((!done || n < 2) && n++ < 40) runTimer = setTimeout(tick, 15000); };
    setTimeout(tick, 4000);
  }

  if (!API) { msg('尚未設定雲端服務網址', 'err'); return; }
  showPin(); load(); status();
})();
"""


def build_html(now) -> str:
    conf = load_conf()
    stocks = stock_list()
    body = f"""
<section class="block" id="add" data-nav="新增">
 <h2 class="sec-title">新增自選股</h2>
 <form id="addform" class="field" autocomplete="off">
  <input id="q" type="search" inputmode="search" placeholder="輸入代號或名稱，例：3189 或 景碩" aria-label="股票代號或名稱">
  <button class="btn primary" type="submit">加入</button>
 </form>
 <ul class="sug" id="sug"></ul>
 <p class="msg" id="msg" role="status"></p>
 <form id="pinbox" class="field" hidden>
  <input id="pin" type="password" inputmode="numeric" autocomplete="current-password" placeholder="PIN 碼" aria-label="PIN 碼">
  <label class="pinrow"><input id="remember" type="checkbox" checked> 記住</label>
  <button class="btn" type="submit">確定</button>
 </form>
 <p class="pinrow" id="pinset" hidden>PIN 已記住在這支手機・<button class="btn" id="forget" type="button">清除</button></p>
 <p class="muted" id="run"></p>
</section>
<section class="block" id="list" data-nav="目前清單">
 <h2 class="sec-title">目前清單</h2>
 <ul class="wl" id="wl"><li class="muted">讀取中…</li></ul>
 <p class="muted">刪除要按兩次（第二次是確認）。加權指數固定會分析，不在清單裡。</p>
</section>
<script>const CONF = {json.dumps({"proxy": conf.get("proxy", "")})};
const STOCKS = {json.dumps(stocks, ensure_ascii=False, separators=(",", ":"))};</script>
<script>{JS}</script>"""
    return report.shell("自選股", f"共 {len(stocks):,} 檔可搜尋", body, extra_css=EXTRA_CSS)
