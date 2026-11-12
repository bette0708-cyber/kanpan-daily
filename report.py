"""產生 HTML 看盤報告 —— 手機優先版面。

- 同一份檔案可以直接用電腦瀏覽器打開，也可以發佈成 claude.ai 網頁在手機上看。
- K 線圖用 TradingView lightweight-charts（unpkg 固定版本），買點 B / 賣點 S 標在圖上。
- 台股慣例：紅 = 漲、綠 = 跌。
"""
from __future__ import annotations

import html
import json
import os

import numpy as np
import pandas as pd

from patterns import BUY, SELL, Signal

WEEK = "一二三四五六日"


def esc(s) -> str:
    return html.escape(str(s))


def _nan(v) -> bool:
    return v is None or (isinstance(v, (float, np.floating)) and np.isnan(v))


def fmt_pct(v, digits=2) -> str:
    if _nan(v):
        return "—"
    cls = "up" if v > 0 else "dn" if v < 0 else ""
    return f'<span class="num {cls}">{v:+.{digits}f}%</span>'


def fmt_num(v, digits=2) -> str:
    if _nan(v):
        return "—"
    return f"{v:,.{digits}f}"


def day(ts: pd.Timestamp) -> str:
    return f"{ts:%m/%d}（{WEEK[ts.weekday()]}）"


def row(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<small>{sub}</small>' if sub else ""
    return f'<li class="row"><span>{label}{sub_html}</span><span class="num">{value}</span></li>'


def meter(label: str, score: float | None, big: bool = False) -> str:
    """0~100 分的橫條：中線 = 50 分（多空打平）。"""
    if score is None:
        return f'<div class="meter"><span class="m-label">{esc(label)}</span><span class="num muted">—</span></div>'
    cls = "up" if score >= 57 else "dn" if score <= 43 else "mid"
    return (f'<div class="meter{" big" if big else ""}"><span class="m-label">{esc(label)}</span>'
            f'<span class="bar"><i class="{cls}" style="left:{min(max(score, 2), 98):.0f}%"></i></span>'
            f'<b class="num {cls}">{score:.0f}</b></div>')


# ------------------------------------------------------------------ 大盤評估
def outlook_html(o: dict, world: dict) -> str:
    p = ['<section class="block" id="market" data-nav="大盤"><h2 class="sec-title">大盤漲跌評估</h2>']
    bt = o.get("backtest", {})
    if o.get("target") is not None:
        lab, cls = o["label"]
        n = o["night"]
        p.append(f"""
<div class="verdict {cls}">
  <p class="eyebrow">加權指數 {day(o['target'])} {esc(o['mode'])}走勢</p>
  <p class="v-main">{esc(lab)}</p>
  <p class="v-sub num">綜合分數 {o['score']:+.2f}</p>
</div>
<div class="facts">
  <div><span>台指期夜盤</span>{fmt_pct(o['night_pct'])}</div>
  <div><span>美股加權</span>{fmt_pct(o['us'])}</div>
  <div><span>依夜盤估開盤</span><b class="num">{fmt_num(o['est_open'], 0)}</b></div>
  <div><span>方法收盤命中率</span><b class="num">{f"{bt['hit_close']:.0%}" if bt.get('n') else '—'}</b></div>
</div>
<details><summary>夜盤與美股明細</summary>
 <h3>台指期夜盤（權重 60%）</h3>
 <ul class="list">
  {row('時段', f"{day(o['target'])}前一晚 15:00～05:00")}
  {row('合約', f"TX {esc(n['contract'])}")}
  {row('開 / 高', f"{fmt_num(n['open'], 0)} / {fmt_num(n['high'], 0)}")}
  {row('低 / 收', f"{fmt_num(n['low'], 0)} / {fmt_num(n['close'], 0)}")}
  {row('漲跌', f"{fmt_num(n['chg'], 0)} 點 {fmt_pct(o['night_pct'])}")}
 </ul>
 <h3>美股前一交易日（權重 40%）</h3>
 <ul class="list">{''.join(row(esc(r['name']), fmt_pct(r['pct']), f"{r['date']:%m/%d}・權重 {r['w']:.0%}") for r in o['us_rows'])}
  {row('<b>加權</b>', fmt_pct(o['us']))}</ul>
</details>""")
    else:
        p.append(f'<p class="note">{esc(o.get("pending_note") or "")}</p>')
        if o.get("provisional"):
            lab, cls = o["label"]
            p.append(f'<div class="verdict {cls}"><p class="eyebrow">隔日暫估（僅供參考）</p>'
                     f'<p class="v-main">{esc(lab)}</p><p class="v-sub num">分數 {o["score"]:+.2f}</p></div>'
                     f'<ul class="list">{"".join(row(esc(nm), fmt_pct(v)) for nm, v in o["provisional"])}</ul>')

    rows = []
    for sym, info in world.items():
        if "df" not in info:
            rows.append(row(esc(info["name"]), '<span class="muted">抓取失敗</span>', esc(info["region"])))
            continue
        df = info["df"]
        rows.append(row(esc(info["name"]), fmt_pct(float(info["pct"].iat[-1])),
                        f"{esc(info['region'])}・{df.index[-1]:%m/%d}・{fmt_num(df['close'].iat[-1])}"))
    p.append(f'<details><summary>各國指數</summary><ul class="list">{"".join(rows)}</ul></details>')

    nt = o["night_table"]
    if not nt.empty:
        r2 = "".join(row(f"{d:%m/%d}", fmt_pct(r["chg_pct"]), f"收 {fmt_num(r['close'], 0)}・量 {fmt_num(r['volume'], 0)}")
                     for d, r in nt.tail(10).iloc[::-1].iterrows())
        p.append(f'<details><summary>近 10 個夜盤</summary><p class="muted">日期 = 夜盤歸屬的交易日</p>'
                 f'<ul class="list">{r2}</ul></details>')

    if bt.get("n"):
        r3 = "".join(
            f"<tr><td>{r['date']:%m/%d}</td><td class='num'>{r['score']:+.2f}</td><td>{fmt_pct(r['gap'])}</td>"
            f"<td>{fmt_pct(r['close'])}</td><td>{'✔' if r['hit_gap'] else '✘'}</td><td>{'✔' if r['hit_close'] else '✘'}</td></tr>"
            for _, r in bt["rows"].iterrows())
        p.append(f"""<details><summary>這個評估方法過去準不準？</summary>
<p>近 {bt['n']} 個有方向的交易日：開盤方向命中 <b class="num">{bt['hit_gap']:.0%}</b>、收盤方向命中 <b class="num">{bt['hit_close']:.0%}</b>。
<span class="muted">分數在 ±0.3 內視為持平、不計入。</span></p>
<div class="scroll"><table><tr><th>日期</th><th>分數</th><th>開盤跳空</th><th>收盤</th><th>開</th><th>收</th></tr>{r3}</table></div>
</details>""")
    p.append("</section>")
    return "".join(p)


# ------------------------------------------------------------------ 自選股總覽
def summary_html(items: list[dict]) -> str:
    cards = []
    for it in sorted(items, key=lambda x: -x["m"]["total"]):
        m, last = it["m"], it["last"]
        lab, cls = m["verdict"]
        sig = '<span class="pill">無訊號</span>'
        if last is not None:
            side = "buy" if last.side == BUY else "sell"
            sig = f'<span class="pill {side}">{"買" if side == "buy" else "賣"} {last.date:%m/%d} {esc(last.status)}</span>'
        cards.append(f"""<a class="sum-card" href="#{it['cid']}-card">
 <div class="sum-top"><b>{esc(it['name'])}</b><span class="num">{fmt_num(it['close'])}</span>{fmt_pct(it['chg'])}</div>
 {meter('綜合', m['total'])}{meter('短線', m['short'])}{meter('中長線', m['mid'])}
 <div class="sum-foot"><span class="pill v-{cls}">{esc(lab)}</span>{sig}</div>
</a>""")
    return f"""<section class="block" id="overview" data-nav="總覽"><h2 class="sec-title">自選股總覽</h2>
<p class="muted">依綜合分數排序，點卡片看詳細分析。短線高、中長線低 = 跌深反彈，趨勢還沒翻多。</p>
<div class="sum-grid">{''.join(cards)}</div></section>"""


# ------------------------------------------------------------------ 個股
def masters_block(m: dict) -> str:
    lab, cls = m["verdict"]
    items = "".join(
        f'<li class="method"><div class="m-head"><b>{esc(r["name"])}</b>'
        f'<span class="score num {"up" if r["score"] > 0 else "dn" if r["score"] < 0 else ""}">{r["score"]:+.1f}</span></div>'
        f'<small>{esc(r["master"])}・{esc(r["group"])}</small><p>{esc(r["why"])}</p></li>' for r in m["rows"])
    return f"""
 <div class="scores">
  <p class="eyebrow">大師方法綜合評估<span class="pill v-{cls}">{esc(lab)}</span></p>
  {meter('綜合', m['total'], big=True)}{meter('短線', m['short'])}{meter('中長線', m['mid'])}
 </div>
 <details><summary>{len(m['rows'])} 種方法各自怎麼看</summary>
  <p class="muted">每個方法 −2（偏空）～ +2（偏多），加權後換成 0～100 分，50 分 = 多空打平。</p>
  <ul class="methods">{items}</ul>
 </details>"""


def symbol_html(sym: str, name: str, d: pd.DataFrame, signals: list[Signal], trend: dict,
                bt: dict, chart_id: str, m: dict | None = None) -> str:
    last, prev = d.iloc[-1], d.iloc[-2]
    chg = (last["close"] / prev["close"] - 1) * 100
    tag = {"buy": "買", "sell": "賣", "info": "觀察"}
    pats = "".join(
        f'<li><span class="pill {p.side}">{tag[p.side]}</span><b>{esc(p.name)}</b>　{esc(p.note)}'
        f'<small>（{p.source}）</small></li>' for p in last["patterns"]
    ) or '<li class="muted">今天沒有教學裡的關鍵型態</li>'

    sig = signals[-1] if signals else None
    if sig:
        side = "buy" if sig.side == BUY else "sell"
        ago = len(d) - 1 - d.index.get_loc(sig.date)
        when = "今天" if ago == 0 else f"{ago} 根K線前"
        risk = (f"停損：跌破 <b class='num'>{fmt_num(sig.stop)}</b>" if sig.side == BUY
                else f"回補：站回 <b class='num'>{fmt_num(sig.stop)}</b>")
        sig_html = (f'<div class="signal {side}"><p><b>K線{"買點" if side == "buy" else "賣點"}</b>　{day(sig.date)}・{when}'
                    f'<span class="pill {side}">{esc(sig.status)}</span></p>'
                    f'<p>{esc("、".join(p.name for p in sig.patterns))}・{risk}</p></div>')
    else:
        sig_html = '<div class="signal"><p>近期沒有達門檻的K線買賣訊號</p></div>'

    def bt_txt(k):
        b = bt[k]
        return "樣本不足" if not b["n"] else f"{b['n']} 次・勝率 {b['win']:.0%}・平均 {b['avg'] * 100:+.2f}%"

    hist = "".join(
        f'<li class="row"><span><span class="pill {"buy" if s.side == BUY else "sell"}">{"買" if s.side == BUY else "賣"}</span>'
        f'{s.date:%Y/%m/%d}<small>{esc("、".join(p.name for p in s.patterns))}・{esc(s.status)}・停損 {fmt_num(s.stop)}</small></span>'
        f'<span class="num">{fmt_pct(s.ret5 * 100) if s.ret5 is not None else "—"}</span></li>'
        for s in reversed(signals[-12:]))
    ma = "".join(row(f"MA{n}", fmt_num(v)) for n, v in trend["ma"].items() if v is not None and not np.isnan(v))

    return f"""
<section class="block stock" id="{chart_id}-card" data-nav="{esc(name)}">
 <header class="s-head">
  <div><h2>{esc(name)}</h2><span class="code">{esc(sym)}・{d.index[-1]:%m/%d}</span></div>
  <div class="price"><b class="num">{fmt_num(last['close'])}</b>{fmt_pct(chg)}</div>
 </header>
 <div class="tags"><span class="pill">今日K線：{esc(last['kind'])}</span><span class="pill">{esc(trend['label'])}</span></div>
 {masters_block(m) if m else ""}
 {sig_html}
 <div class="chart" id="{chart_id}"></div>
 <p class="legend muted"><b class="up">B</b> 已確認買點・<b class="dn">S</b> 已確認賣點・帶 ? = 待確認｜線：<i class="l5"></i>MA5 <i class="l20"></i>MA20 <i class="l60"></i>MA60</p>
 <h3>今日型態解讀</h3><ul class="pats">{pats}</ul>
 <details><summary>K線買賣點紀錄與回測</summary>
  <p>已確認訊號，確認後持有 5 日：<br>買點 {bt_txt(BUY)}<br>賣點 {bt_txt(SELL)}</p>
  <ul class="list">{hist}</ul>
  <h3>均線</h3><ul class="list">{ma}</ul>
 </details>
</section>"""


def chart_data(d: pd.DataFrame, signals: list[Signal], bars: int = 120) -> dict:
    t = d.tail(bars)
    first = t.index[0]
    ts = lambda x: x.strftime("%Y-%m-%d")
    candles = [{"time": ts(i), "open": round(r.open, 2), "high": round(r.high, 2), "low": round(r.low, 2),
                "close": round(r.close, 2)} for i, r in t.iterrows()]
    vol = [{"time": ts(i), "value": float(r.volume), "up": bool(r.close >= r.open)} for i, r in t.iterrows()]
    mas = {n: [{"time": ts(i), "value": round(float(v), 2)} for i, v in t[f"ma{n}"].dropna().items()]
           for n in (5, 20, 60)}
    marks = []
    for s in signals:
        if s.date < first or s.status not in ("已確認", "待確認"):
            continue   # 圖上只標「已確認」和最新「待確認」，避免太亂
        marks.append({"time": ts(s.date), "side": s.side,
                      "text": ("B" if s.side == BUY else "S") + ("" if s.status == "已確認" else "?")})
    return {"candles": candles, "vol": vol, "ma": mas, "marks": marks}


# ------------------------------------------------------------------ 回測 / 規則
def masters_html(bt: pd.DataFrame) -> str:
    rows = "".join(
        f"<tr><td>{esc(idx)}</td><td class='num'>{int(r['n']):,}</td><td>{fmt_pct(r['avg5'] * 100)}</td>"
        f"<td class='num'>{'—' if pd.isna(r['win5']) else f'{r.win5:.0%}'}</td><td>{fmt_pct(r['avg20'] * 100)}</td>"
        f"<td class='num'>{'—' if pd.isna(r['win20']) else f'{r.win20:.0%}'}</td></tr>"
        for idx, r in bt.iterrows())
    from masters import METHODS
    mrows = "".join(row(esc(m.name), f"權重 {m.weight}", f"{esc(m.master)}・{esc(m.group)}") for m in METHODS)
    return f"""<section class="block" id="method" data-nav="方法說明"><h2 class="sec-title">方法說明與驗證</h2>
<details><summary>綜合分數過去準不準？（5 年回測）</summary>
<p>自選股＋加權指數近 5 年每日資料（不含籌碼）：分數落在哪個區間，之後 5 天、20 天的平均漲跌與上漲機率。</p>
<div class="scroll"><table><tr><th>分數</th><th>天數</th><th>5日均</th><th>5日漲機率</th><th>20日均</th><th>20日漲機率</th></tr>{rows}</table></div>
<p class="muted">這些是你挑的股票，過去 5 年多數大漲過，所以各區間報酬都偏高；要看的是分數高低之間的相對差距。</p>
</details>
<details><summary>採用的方法與權重</summary><ul class="list">{mrows}</ul></details>
{RULES}
</section>"""


RULES = """<details><summary>K線判斷規則（依你上傳的教學）</summary><ul class="list">
<li class="row"><span>大陽線／大陰線<small>幾乎沒有影線的長實體・IMG_4069、4071</small></span><span>買／賣</span></li>
<li class="row"><span>錘子紅K／錘子黑K<small>下跌後，下影線 ≥ 實體 2 倍，越長越強・IMG_4065</small></span><span>買</span></li>
<li class="row"><span>倒錘紅K／光腳紅K<small>上漲後長上影，上影越長賣壓越強・IMG_4067、4070</small></span><span>賣</span></li>
<li class="row"><span>光頭紅K<small>收在最高、有明顯下影，低位吸納・IMG_4068</small></span><span>買</span></li>
<li class="row"><span>下影線超長<small>下影佔整根 60% 以上，強力支撐・IMG_4063</small></span><span>買</span></li>
<li class="row"><span>看漲吞沒／穿刺線／錘子底<small>下跌後出現・IMG_4061</small></span><span>買</span></li>
<li class="row"><span>看跌吞沒／烏雲蓋頂／鎚子頂<small>上漲後出現・IMG_4061</small></span><span>賣</span></li>
<li class="row"><span>晨星／三白兵／3連陽<small>IMG_4062、4063</small></span><span>買</span></li>
<li class="row"><span>黃昏星／三烏鴉<small>IMG_4062</small></span><span>賣</span></li>
<li class="row"><span>上升三法／看漲旗形<small>續漲形態・IMG_4064</small></span><span>買</span></li>
<li class="row"><span>下降三法／看跌旗形<small>續跌形態・IMG_4064</small></span><span>賣</span></li>
</ul>
<p class="muted">「上漲後／下跌後」= 前 5 根K線漲跌超過 1 倍平均波動。同方向分數加總 ≥ 2 才算買賣點。
單一K線不算數：之後 1～2 天收盤延續且量能不低於 5 日均量 90% 才算確認。停損 = 訊號K線最低點（賣點用最高點）。</p>
</details>"""


# ------------------------------------------------------------------ 樣式與頁面
CSS = """
/* 版面：手機單欄、頂部固定的股票導覽列；桌機加寬成雙欄卡片 */
:root{
  --bg:#eef0f3;--surface:#ffffff;--ink:#14171c;--muted:#5d6470;--line:#dfe3e8;
  --up:#cf2e2a;--dn:#0f8457;--mid:#a07a12;--accent:#2b56c9;
  --font:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"Cascadia Mono",monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0d0f12;--surface:#171a1f;--ink:#e7e9ed;--muted:#9aa1ad;--line:#2a2e36;
  --up:#ff5a52;--dn:#31c584;--mid:#e0b43c;--accent:#86a2ff;color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0d0f12;--surface:#171a1f;--ink:#e7e9ed;--muted:#9aa1ad;--line:#2a2e36;
  --up:#ff5a52;--dn:#31c584;--mid:#e0b43c;--accent:#86a2ff;color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:15px/1.6 var(--font);margin:0;-webkit-text-size-adjust:100%}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--surface)}
.wrap{max-width:1040px;margin:0 auto;padding-block:8px 32px;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3{text-wrap:balance;margin:0}
h1{font-size:20px;font-weight:700}
h2{font-size:18px;font-weight:700}
h3{font-size:14px;color:var(--muted);font-weight:600;margin:14px 0 6px}
p{margin:6px 0}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:500}
.up{color:var(--up)}.dn{color:var(--dn)}.mid{color:var(--mid)}
.muted{color:var(--muted);font-size:13px}
small{display:block;color:var(--muted);font-size:12px;line-height:1.45}

/* 頂部導覽 */
.topbar{position:sticky;top:env(safe-area-inset-top,0px);z-index:9;background:var(--surface);
  border-bottom:1px solid var(--line);margin-left:calc(-1*max(16px,env(safe-area-inset-left,0px)));
  margin-right:calc(-1*max(16px,env(safe-area-inset-right,0px)));
  padding:10px max(16px,env(safe-area-inset-right,0px)) 0 max(16px,env(safe-area-inset-left,0px))}
.topbar .t{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.nav{display:flex;gap:6px;overflow-x:auto;padding:8px 0 10px;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.nav a{flex:none;font-size:13px;padding:5px 12px;border-radius:999px;border:1px solid var(--line);
  color:var(--ink);text-decoration:none;white-space:nowrap}
.nav a.on{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.nav a:focus-visible,summary:focus-visible,.sum-card:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* 區塊 */
.block{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:12px;
  scroll-margin-top:calc(env(safe-area-inset-top,0px) + 104px);min-width:0}
.sec-title{margin-bottom:10px}
.eyebrow{font-size:12px;letter-spacing:.04em;color:var(--muted);margin:0 0 4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.note{background:color-mix(in srgb,var(--accent) 10%,transparent);padding:10px 12px;border-radius:10px;font-size:14px}

/* 大盤結論 */
.verdict{border-radius:12px;padding:14px;text-align:center;background:color-mix(in srgb,var(--muted) 10%,transparent)}
.verdict .eyebrow{justify-content:center}
.verdict .v-main{font-size:30px;font-weight:700;letter-spacing:.08em;margin:2px 0}
.verdict .v-sub{margin:0;color:var(--muted);font-size:13px}
.verdict.up1,.verdict.up2{background:color-mix(in srgb,var(--up) 11%,transparent)}.verdict.up1 .v-main,.verdict.up2 .v-main{color:var(--up)}
.verdict.dn1,.verdict.dn2{background:color-mix(in srgb,var(--dn) 11%,transparent)}.verdict.dn1 .v-main,.verdict.dn2 .v-main{color:var(--dn)}
.facts{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0 4px}
.facts div{border:1px solid var(--line);border-radius:10px;padding:8px 10px;display:flex;flex-direction:column}
.facts span:first-child{font-size:12px;color:var(--muted)}
.facts .num{font-size:17px}

/* 清單列 */
.list{list-style:none;margin:0;padding:0}
.row{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:8px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}
.row>span:first-child{min-width:0}
.row>span:last-child{text-align:right;white-space:nowrap}

details{border-top:1px solid var(--line);margin-top:12px;padding-top:4px}
summary{cursor:pointer;font-weight:600;font-size:14px;color:var(--accent);padding:8px 0;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸";display:inline-block;width:1.1em;transition:transform .15s}
details[open]>summary::before{transform:rotate(90deg)}
@media (prefers-reduced-motion:reduce){summary::before{transition:none}}

/* 分數橫條 */
.meter{display:grid;grid-template-columns:52px 1fr 36px;align-items:center;gap:8px;margin:5px 0;font-size:13px}
.m-label{color:var(--muted)}
.bar{position:relative;height:6px;border-radius:3px;
  background:linear-gradient(90deg,color-mix(in srgb,var(--dn) 35%,transparent),color-mix(in srgb,var(--muted) 20%,transparent) 50%,color-mix(in srgb,var(--up) 35%,transparent))}
.bar::after{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--muted);opacity:.5}
.bar i{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;transform:translate(-50%,-50%);
  background:var(--mid);border:2px solid var(--surface);box-shadow:0 0 0 1px var(--line)}
.bar i.up{background:var(--up)}.bar i.dn{background:var(--dn)}
.meter b{text-align:right}
.meter.big .m-label{color:var(--ink);font-weight:600}.meter.big .bar{height:8px}.meter.big b{font-size:18px;line-height:1}

/* 標籤 */
.pill{display:inline-block;font-size:12px;line-height:1.5;padding:1px 8px;border-radius:999px;
  border:1px solid var(--line);color:var(--muted);white-space:nowrap;margin-right:4px}
.pill.buy{color:var(--up);border-color:color-mix(in srgb,var(--up) 45%,transparent);background:color-mix(in srgb,var(--up) 8%,transparent)}
.pill.sell{color:var(--dn);border-color:color-mix(in srgb,var(--dn) 45%,transparent);background:color-mix(in srgb,var(--dn) 8%,transparent)}
.pill.v-up1,.pill.v-up2{color:var(--up);border-color:currentColor}
.pill.v-dn1,.pill.v-dn2{color:var(--dn);border-color:currentColor}
.pill.v-flat{color:var(--mid);border-color:currentColor}

/* 總覽卡片 */
.sum-grid{display:grid;grid-template-columns:1fr;gap:10px}
.sum-card{display:block;border:1px solid var(--line);border-radius:12px;padding:10px 12px;color:inherit;text-decoration:none;min-width:0}
.sum-card:active{background:color-mix(in srgb,var(--muted) 8%,transparent)}
.sum-top{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}
.sum-top b{flex:1;font-size:16px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sum-foot{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}

/* 個股 */
.s-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.s-head h2{font-size:20px}
.code{font-size:12px;color:var(--muted);font-family:var(--mono)}
.price{text-align:right;display:flex;flex-direction:column}
.price b{font-size:20px}
.tags{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0}
.scores{border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-top:4px}
.signal{border-radius:10px;padding:8px 12px;margin-top:10px;font-size:14px;background:color-mix(in srgb,var(--muted) 8%,transparent)}
.signal p{margin:2px 0}
.signal.buy{background:color-mix(in srgb,var(--up) 8%,transparent)}
.signal.sell{background:color-mix(in srgb,var(--dn) 8%,transparent)}
.chart{height:300px;margin-top:12px;max-width:100%}
.legend{margin-top:4px}
.legend i{display:inline-block;width:12px;height:2px;vertical-align:middle;margin:0 3px 0 6px}
.legend .l5{background:#e08a00}.legend .l20{background:#8b5cf6}.legend .l60{background:#0ea5e9}
.pats{list-style:none;padding:0;margin:0;font-size:14px}
.pats li{padding:6px 0;border-bottom:1px solid var(--line)}.pats li:last-child{border:0}
.methods{list-style:none;padding:0;margin:0}
.method{padding:10px 0;border-bottom:1px solid var(--line)}.method:last-child{border:0}
.m-head{display:flex;justify-content:space-between;gap:8px}
.method p{margin:4px 0 0;font-size:14px}
.score{font-size:15px}

/* 表格（只用在回測，可左右滑） */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap}
th{color:var(--muted);font-weight:600}
.toplink{margin:2px 0 0;font-size:13px}.toplink a{color:var(--accent);text-decoration:none;font-weight:600}
.warn{font-size:12px;color:var(--muted);text-align:center;margin:20px 0 0}

@media (min-width:760px){
  .sum-grid{grid-template-columns:1fr 1fr}
  .chart{height:400px}
  .facts{grid-template-columns:repeat(4,1fr)}
}
"""

JS = """
(function(){
  // 導覽列：依各區塊的 data-nav 產生按鈕，捲動時標示目前位置
  const nav = document.getElementById('nav');
  const secs = [...document.querySelectorAll('[data-nav]')];
  const links = secs.map(s => {
    const a = document.createElement('a');
    a.href = '#' + s.id; a.textContent = s.dataset.nav; nav.appendChild(a); return a;
  });
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => {
      es.forEach(e => {
        if (!e.isIntersecting) return;
        const i = secs.indexOf(e.target);
        links.forEach((a, j) => a.classList.toggle('on', i === j));
        links[i].scrollIntoView({inline:'center', block:'nearest'});
      });
    }, {rootMargin:'-120px 0px -65% 0px'});
    secs.forEach(s => io.observe(s));
  }

  // K 線圖：顏色取自 CSS 變數，深淺色切換時重新套用（套件晚到時由 onerror 備援呼叫 initCharts）
  window.initCharts = initCharts;
  if (window.LightweightCharts) initCharts();
  function initCharts(){
  if (!window.LightweightCharts || window.__chartsDone) return;
  window.__chartsDone = true;
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const made = [];
  function theme(){
    return {up: css('--up'), dn: css('--dn'), ink: css('--muted'), line: css('--line')};
  }
  function paint(o){
    const t = theme();
    o.chart.applyOptions({layout:{background:{color:'transparent'}, textColor:t.ink},
      grid:{vertLines:{color:t.line}, horzLines:{color:t.line}}});
    o.candle.applyOptions({upColor:t.up, downColor:t.dn, borderUpColor:t.up, borderDownColor:t.dn,
      wickUpColor:t.up, wickDownColor:t.dn});
    o.vol.setData(o.c.vol.map(v => ({time:v.time, value:v.value, color:(v.up ? t.up : t.dn) + '66'})));
    o.candle.setMarkers(o.c.marks.map(m => ({time:m.time, text:m.text,
      position: m.side === 'buy' ? 'belowBar' : 'aboveBar', shape: m.side === 'buy' ? 'arrowUp' : 'arrowDown',
      color: m.side === 'buy' ? t.up : t.dn})));
  }
  for (const [id, c] of Object.entries(CHARTS)) {
    const el = document.getElementById(id);
    if (!el) continue;
    const chart = LightweightCharts.createChart(el, {autoSize:true,
      rightPriceScale:{scaleMargins:{top:0.06, bottom:0.24}, borderVisible:false},
      timeScale:{borderVisible:false}, crosshair:{mode:0},
      handleScroll:{vertTouchDrag:false}, handleScale:{axisPressedMouseMove:false}});
    const candle = chart.addCandlestickSeries({priceLineVisible:false});
    candle.setData(c.candles);
    const vol = chart.addHistogramSeries({priceFormat:{type:'volume'}, priceScaleId:'', lastValueVisible:false});
    vol.priceScale().applyOptions({scaleMargins:{top:0.82, bottom:0}});
    const col = {5:'#e08a00', 20:'#8b5cf6', 60:'#0ea5e9'};
    for (const [n, data] of Object.entries(c.ma)) chart.addLineSeries({color:col[n], lineWidth:1,
      priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false}).setData(data);
    const o = {chart, candle, vol, c};
    paint(o);
    const n = c.candles.length;
    chart.timeScale().setVisibleLogicalRange({from: Math.max(0, n - (el.clientWidth < 500 ? 60 : 120)), to: n + 2});
    made.push(o);
  }
  const repaint = () => made.forEach(paint);
  matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', repaint);
  new MutationObserver(repaint).observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
  }
})();
"""


def page(generated: str, outlook_block: str, symbol_blocks: list[str], charts: dict,
         summary_block: str = "", masters_bt_block: str = "") -> str:
    """產生頁面片段（含 title/meta/style/script），瀏覽器可直接開，也符合 claude.ai 網頁發佈格式。"""
    body = outlook_block + summary_block + "".join(symbol_blocks) + masters_bt_block
    return shell("看盤日報", generated, body, charts)


def site_link(title: str) -> str:
    """在 GitHub 網站版（環境變數 KANPAN_SITE=1）加上日報 ↔ 週報互連。"""
    if not os.environ.get("KANPAN_SITE"):
        return ""
    pages = [("看盤日報", "index.html"), ("即時監看", "monitor.html"), ("回測週報", "backtest.html"),
             ("自選股", "watchlist.html")]
    links = "　".join(f'<a href="{href}">{name}</a>' for name, href in pages if name != title)
    return f'<p class="toplink">{links}</p>'


def shell(title: str, generated: str, body: str, charts: dict | None = None, extra_css: str = "") -> str:
    """共用頁框：頂部導覽（依 data-nav 自動產生）、深淺色、手機版面。"""
    # K 線圖套件：先從 jsDelivr 載入，載入失敗再改用 unpkg（同一版本）
    lib = ('<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js" '
           'onerror="var s=document.createElement(\'script\');'
           's.src=\'https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js\';'
           's.onload=function(){window.initCharts&&window.initCharts()};document.head.appendChild(s)"></script>'
           ) if charts else ""
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{esc(title)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&family=Noto+Sans+TC:wght@400;600;700&display=swap">
<style>{CSS}{extra_css}</style>
{lib}
<div class="wrap">
 <header class="topbar">
  <div class="t"><h1>{esc(title)}</h1><span class="muted num">{esc(generated)}</span></div>{site_link(title)}
  <nav class="nav" id="nav" aria-label="跳到區塊"></nav>
 </header>
 {body}
 <p class="warn">投資有風險，入市需謹慎。本報告依教學規則與公開分析方法自動判讀，僅供參考，不構成投資建議。</p>
</div>
<script>const CHARTS = {json.dumps(charts or {}, ensure_ascii=False, separators=(",", ":"))};{JS}</script>
"""
