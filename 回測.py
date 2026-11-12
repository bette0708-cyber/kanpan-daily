"""每週回測：哪一種方法的預測最準？

用法：
  python 回測.py            # 產生 報告/回測週報.html 並打開
  python 回測.py --no-open

三個部分：
  1. 個股方法排行：12 種方法（含你的教學、綜合分數）過去預測 隔日/5日/20日 漲跌的準確度
  2. 大盤預測方式排行：夜盤、美股、費半、台積電ADR… 哪個最能預測台股隔天開盤/收盤
  3. 實際紀錄：每天排程存下來的預測，跟後來真正的漲跌比對（最可信，但要累積幾週才有意義）

準確度用「平衡命中率」= (看多時真的漲的比例 + 看空時真的跌的比例) ÷ 2。
50% = 跟擲銅板一樣；股票長期大多在漲，所以「一直喊多」的方法單看命中率會虛高，平衡命中率可以排除這個偏差。
"""
from __future__ import annotations

import argparse
import sys
import warnings
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if sys.stdout is None:   # 排程用 pythonw 執行時沒有視窗
    (HERE / "報告").mkdir(exist_ok=True)
    sys.stdout = sys.stderr = open(HERE / "報告" / "回測紀錄.txt", "w", encoding="utf-8")
warnings.simplefilter("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import masters  # noqa: E402
import outlook  # noqa: E402
import patterns  # noqa: E402
import report  # noqa: E402
from data import SESSION_NIGHT, now_tpe, resolve_tw_symbol, taifex_tx  # noqa: E402
from data import load_daily as yahoo_daily  # noqa: E402  (Yahoo＋官方校正)
from report import esc  # noqa: E402

LEDGER = HERE / "紀錄"
HORIZONS = {1: "隔日", 5: "5日", 20: "20日"}
WINDOWS = [("近3個月", 63), ("近1年", 252), ("近5年", None)]
MAIN_H, MAIN_W = 5, "近1年"          # 排行榜主要依據
COMBOS = {"composite": ("綜合分數", "全部方法加權", None),
          "short": ("短線綜合", "教學＋動能", {"教學", "動能"}),
          "mid": ("中長線綜合", "趨勢方法", {"趨勢"})}


def read_watchlist() -> dict[str, str]:
    f = HERE / "自選股.txt"
    out = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            code, _, note = line.partition("#")
            if code.strip():
                out[code.split()[0]] = note.strip()
    return out


# ------------------------------------------------------------------ 評分
def evaluate(s: pd.Series, f: pd.Series, th: float) -> dict | None:
    m = s.notna() & f.notna()
    s, f = s[m], f[m]
    call = (s >= th) | (s <= -th)
    bull, bear = f[s >= th], f[s <= -th]
    if call.sum() < 20:
        return None
    bp = float((bull > 0).mean()) if len(bull) >= 5 else np.nan
    rp = float((bear < 0).mean()) if len(bear) >= 5 else np.nan
    bal = float(np.nanmean([bp, rp])) if not (np.isnan(bp) and np.isnan(rp)) else np.nan
    return {"n": int(call.sum()), "n_bull": len(bull), "n_bear": len(bear), "bull": bp, "bear": rp, "bal": bal,
            "one_side": np.isnan(bp) or np.isnan(rp),
            "hit": float((np.sign(s[call]) == np.sign(f[call])).mean()),
            "spread": float(bull.mean() - bear.mean()) if len(bull) and len(bear) else np.nan,
            "ic": float(s.rank().corr(f.rank()))}   # 等同 Spearman 相關（不需要 scipy）


def signed_combo(df: pd.DataFrame, groups: set | None) -> pd.Series:
    keys = [m.key for m in masters.METHODS if m.key in df.columns and (groups is None or m.group in groups)]
    w = np.array([masters.METHOD_BY_KEY[k].weight for k in keys])
    return (df[keys] * w).sum(axis=1) / w.sum()          # −2 ~ +2


def build_panel(names: dict[str, str]) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    taiex, _ = yahoo_daily("^TWII", "5y")
    todo = [("^TWII", "台股加權指數", taiex)]
    for s, nm in names.items():
        full = resolve_tw_symbol(s)
        try:
            df, meta = yahoo_daily(full, "5y")
            todo.append((full, nm or meta.get("shortName") or full, df))
        except RuntimeError as e:
            print(f"  ✘ {s}: {e}")
    frames, closes = [], {}
    for sym, nm, df in todo:
        if len(df) < 260:
            print(f"  － {nm}：資料不到一年，只用現有資料")
        d, _ = patterns.analyze(df)
        res = masters.compute(d, taiex["close"])
        x = pd.DataFrame({k: v[0] for k, v in res.items()})
        for key, (_, _, groups) in COMBOS.items():
            x[key] = signed_combo(x, groups)
        for h in HORIZONS:
            x[f"f{h}"] = d["close"].shift(-h) / d["close"] - 1
        x["sym"], x["name"] = sym, nm
        frames.append(x.iloc[200:])                         # 前 200 天指標還沒算滿
        closes[sym] = d["close"]
        print(f"  ✔ {nm}")
    return pd.concat(frames), closes, taiex


def method_table(panel: pd.DataFrame) -> list[dict]:
    last = panel.index.max()
    out = []
    keys = [(m.key, m.name, m.master, m.group, 0.5) for m in masters.METHODS if m.key in panel.columns]
    keys += [(k, v[0], v[1], "綜合", 0.28) for k, v in COMBOS.items()]   # 0.28 ≈ 綜合分數 57 / 43 分
    for key, name, master, group, th in keys:
        row = {"key": key, "name": name, "master": master, "group": group, "res": {}}
        for wname, days in WINDOWS:
            p = panel if days is None else panel[panel.index > last - pd.Timedelta(days=int(days * 1.45))]
            for h in HORIZONS:
                row["res"][(wname, h)] = evaluate(p[key], p[f"f{h}"], th)
        out.append(row)
    return out


def main_score(r: dict, w=MAIN_W, h=MAIN_H) -> float:
    e = r["res"].get((w, h))
    return -1 if e is None or np.isnan(e["bal"]) else e["bal"]


# ------------------------------------------------------------------ 大盤
def market_table(taiex: pd.DataFrame) -> tuple[list[dict], int]:
    world = outlook.load_world("2y")
    fut = taifex_tx(400)
    night = fut[fut["session"] == SESSION_NIGHT].set_index("date")["chg_pct"].astype(float)
    t = taiex.copy()
    t["prev"] = t["close"].shift()
    rows = []
    for day, npct in night.items():
        if day not in t.index or np.isnan(t.at[day, "prev"]) or np.isnan(npct):
            continue
        us, us_rows = outlook.us_score_before(world, day)
        rec = {"date": day, "夜盤": npct, "美股加權": us,
               "綜合（夜盤60%＋美股40%）": npct * outlook.NIGHT_W + (us if us is not None else npct) * outlook.US_W,
               "gap": t.at[day, "open"] / t.at[day, "prev"] - 1, "close": t.at[day, "close"] / t.at[day, "prev"] - 1}
        for r in us_rows:
            rec[r["name"]] = r["pct"]
        rows.append(rec)
    df = pd.DataFrame(rows).set_index("date").sort_index()
    preds = ["綜合（夜盤60%＋美股40%）", "夜盤", "美股加權"] + [v[0] for v in outlook.US.values()]
    last = df.index.max()
    out = []
    for p in preds:
        if p not in df.columns:
            continue
        r = {"name": p, "res": {}}
        for wname, days in (("近3個月", 63), ("近1年", 252)):
            x = df[df.index > last - pd.Timedelta(days=int(days * 1.45))]
            for tgt in ("gap", "close"):
                r["res"][(wname, tgt)] = evaluate(x[p], x[tgt], 0.1)   # 漲跌不到 0.1% 視為沒表態
        out.append(r)
    out.sort(key=lambda r: -(r["res"][("近1年", "close")] or {"bal": 0})["bal"])
    return out, len(df)


# ------------------------------------------------------------------ 實際紀錄（前瞻）
def live_table(closes: dict, taiex: pd.DataFrame) -> dict:
    out = {"stock": None, "stock_n": 0, "market": None, "market_n": 0, "days": 0}
    f = LEDGER / "個股預測.csv"
    if f.exists():
        lg = pd.read_csv(f, dtype={"sym": str}).drop_duplicates(["date", "sym"], keep="last")
        lg["date"] = pd.to_datetime(lg["date"])
        out["days"] = lg["date"].nunique()
        for h in (1, 5):
            vals = []
            for _, r in lg.iterrows():
                c = closes.get(r["sym"])
                if c is None or r["date"] not in c.index:
                    vals.append(np.nan)
                    continue
                i = c.index.get_loc(r["date"])
                vals.append(c.iat[i + h] / c.iat[i] - 1 if i + h < len(c) else np.nan)
            lg[f"f{h}"] = vals
        res = []
        for m in masters.METHODS:
            if m.key not in lg.columns:
                continue
            res.append({"name": m.name, "1": evaluate(lg[m.key], lg["f1"], 0.5), "5": evaluate(lg[m.key], lg["f5"], 0.5)})
        sc = (lg["total"] - 50) / 25
        res.append({"name": "綜合分數", "1": evaluate(sc, lg["f1"], 0.28), "5": evaluate(sc, lg["f5"], 0.28)})
        out["stock"], out["stock_n"] = res, int(lg["f1"].notna().sum())
    f = LEDGER / "大盤預測.csv"
    if f.exists():
        mk = pd.read_csv(f).drop_duplicates("target", keep="last")
        mk["target"] = pd.to_datetime(mk["target"])
        t = taiex.copy()
        t["prev"] = t["close"].shift()
        mk = mk[mk["target"].isin(t.index)]
        if len(mk):
            act = (t.loc[mk["target"], "close"].values / t.loc[mk["target"], "prev"].values - 1)
            hits = np.sign(mk["score"].values) == np.sign(act)
            out["market"] = {"n": len(mk), "hit": float(hits.mean()),
                             "rows": list(zip(mk["target"], mk["label"], act * 100, hits))[-10:][::-1]}
            out["market_n"] = len(mk)
    return out


def save_history(methods: list[dict], now) -> pd.DataFrame | None:
    """存每週排行，下週比較名次變化。"""
    LEDGER.mkdir(exist_ok=True)
    f = LEDGER / "每週回測排行.csv"
    old = pd.read_csv(f) if f.exists() else None
    week = f"{now:%Y-%m-%d}"
    rows = [{"week": week, "name": r["name"], "bal": main_score(r)} for r in methods]
    df = pd.DataFrame(rows)
    df["rank"] = df["bal"].rank(ascending=False, method="min").astype(int)
    allh = pd.concat([old[old["week"] != week], df]) if old is not None else df
    allh.to_csv(f, index=False, encoding="utf-8-sig")
    if old is None:
        return None
    prev = old[old["week"] != week]
    return prev[prev["week"] == prev["week"].max()] if len(prev) else None


# ------------------------------------------------------------------ HTML
EXTRA_CSS = """
.acc{display:grid;grid-template-columns:1fr 46px;align-items:center;gap:8px;margin:4px 0}
.acc .bar{height:8px}
.rank{display:grid;grid-template-columns:28px 1fr;gap:8px;padding:12px 0;border-bottom:1px solid var(--line)}
.rank:last-child{border:0}
.rank .no{font-family:var(--mono);font-size:18px;color:var(--muted);text-align:center}
.rank.top .no{color:var(--up);font-weight:700}
.rank .nm{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.sub{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;color:var(--muted)}
.podium{display:grid;gap:8px}
.podium>div{border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.podium span{display:block;font-size:12px;color:var(--muted)}
.podium b{font-size:16px}
.delta{font-size:12px;margin-left:6px}
@media (min-width:760px){.podium{grid-template-columns:repeat(3,1fr)}}
"""


def pct(v, d=0) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.{d}f}%"


def acc_bar(v, half: float = 0.2) -> str:
    """橫條中線 = 50%（擲銅板）；half = 中線到兩端代表幾 %（個股 ±20%、大盤 ±30%）。"""
    if v is None or np.isnan(v):
        return '<div class="acc"><span class="bar"></span><b class="num muted">—</b></div>'
    cls = "up" if v >= 0.55 else "dn" if v <= 0.45 else "mid"
    pos = min(max((v - 0.5 + half) / (2 * half) * 100, 2), 98)
    return f'<div class="acc"><span class="bar"><i class="{cls}" style="left:{pos:.0f}%"></i></span><b class="num {cls}">{v * 100:.0f}%</b></div>'


def rank_list(methods: list[dict], h: int, prev: pd.DataFrame | None) -> str:
    items = sorted(methods, key=lambda r: -main_score(r, h=h))
    out = []
    for i, r in enumerate(items, 1):
        e = r["res"].get((MAIN_W, h))
        e3, e5 = r["res"].get(("近3個月", h)), r["res"].get(("近5年", h))
        delta = ""
        if prev is not None and h == MAIN_H:
            pr = prev[prev["name"] == r["name"]]
            if len(pr):
                d = int(pr["rank"].iat[0]) - i
                delta = f'<span class="delta {"up" if d > 0 else "dn" if d < 0 else "muted"}">{"▲" if d > 0 else "▼" if d < 0 else "－"}{abs(d) if d else ""}</span>'
        sub = ""
        if e:
            sub = (f'<span>看多時準 {pct(e["bull"])}（{e["n_bull"]}次）</span><span>看空時準 {pct(e["bear"])}（{e["n_bear"]}次）</span>'
                   f'<span>近3個月 {pct(e3["bal"]) if e3 else "—"}</span><span>近5年 {pct(e5["bal"]) if e5 else "—"}</span>'
                   + ('<span>⚠ 只有單邊訊號</span>' if e["one_side"] else ""))
        out.append(f'<li class="rank{" top" if i <= 3 and e else ""}"><span class="no">{i}</span><div>'
                   f'<div class="nm"><b>{esc(r["name"])}{delta}</b><small>{esc(r["master"])}</small></div>'
                   f'{acc_bar(e["bal"] if e else None)}<div class="sub">{sub or "<span>樣本不足</span>"}</div></div></li>')
    return f'<ul class="list">{"".join(out)}</ul>'


def build_html(methods, market, mk_n, live, prev, now, n_syms) -> str:
    ranked = sorted([r for r in methods if main_score(r) >= 0], key=lambda r: -main_score(r))
    recent = sorted([r for r in methods if main_score(r, w="近3個月") >= 0], key=lambda r: -main_score(r, w="近3個月"))
    best_mk = market[0] if market else None
    pod = "".join(f'<div><span>近1年第 {i} 名</span><b>{esc(r["name"])}</b>{acc_bar(main_score(r))}</div>'
                  for i, r in enumerate(ranked[:3], 1))
    worst = ranked[-1] if ranked else None
    concl = f"""<section class="block" id="sum" data-nav="結論"><h2 class="sec-title">本週結論</h2>
<p class="muted">以「近 1 年、預測 5 日後漲跌」的平衡命中率排名。50% = 擲銅板，越高越準。</p>
<div class="podium">{pod}</div>
<ul class="list">
{report.row("近 3 個月最準", esc(recent[0]["name"]) if recent else "—", "最近的市場狀況下表現最好")}
{report.row("近 1 年最不準", esc(worst["name"]) if worst else "—", f"平衡命中率 {pct(main_score(worst))}" if worst else "")}
{report.row("大盤最準的預測方式", esc(best_mk["name"]) if best_mk else "—",
            f"近1年收盤方向平衡命中 {pct(best_mk['res'][('近1年', 'close')]['bal'])}" if best_mk and best_mk['res'][('近1年', 'close')] else "")}
</ul></section>"""

    rk = f"""<section class="block" id="rank" data-nav="方法排行"><h2 class="sec-title">個股方法排行（預測 5 日後）</h2>
<p class="muted">{n_syms} 檔（自選股＋加權指數）近 1 年每天的判讀，跟 5 個交易日後的實際漲跌比對。
分數 ≥ +0.5 算看多、≤ −0.5 算看空（綜合分數用 57／43 分）。▲▼ 是跟上週比的名次變化。</p>
{rank_list(methods, 5, prev)}
<details><summary>改看「預測隔日」排行</summary>{rank_list(methods, 1, None)}</details>
<details><summary>改看「預測 20 日後」排行</summary>{rank_list(methods, 20, None)}</details>
</section>"""

    mrows = []
    for i, r in enumerate(market, 1):
        c1, g1 = r["res"][("近1年", "close")], r["res"][("近1年", "gap")]
        c3 = r["res"][("近3個月", "close")]
        mrows.append(f'<li class="rank{" top" if i == 1 else ""}"><span class="no">{i}</span><div>'
                     f'<div class="nm"><b>{esc(r["name"])}</b></div>'
                     f'{acc_bar(c1["bal"] if c1 else None, half=0.4)}'
                     f'<div class="sub"><span>收盤方向（上方橫條）</span><span>開盤方向 {pct(g1["bal"]) if g1 else "—"}</span>'
                     f'<span>近3個月收盤 {pct(c3["bal"]) if c3 else "—"}</span><span>{c1["n"] if c1 else 0} 天</span></div></div></li>')
    mk = f"""<section class="block" id="market" data-nav="大盤預測"><h2 class="sec-title">大盤預測方式排行</h2>
<p class="muted">用前一晚的資料預測台股當天漲跌，近 1 年共 {mk_n} 個交易日。開盤方向通常比收盤準，因為盤中還會再變化。</p>
<ul class="list">{"".join(mrows)}</ul></section>"""

    if live["stock"] and live["stock_n"] >= 20:
        lr = "".join(report.row(esc(r["name"]), pct(r["5"]["bal"]) if r["5"] else "累積中",
                                f"隔日 {pct(r['1']['bal']) if r['1'] else '累積中'}") for r in live["stock"])
        stock_live = f'<ul class="list">{lr}</ul>'
    else:
        stock_live = (f'<p class="note">從 2026/11/11 開始記錄，目前 {live["days"]} 個交易日、{live["stock_n"]} 筆有結果。'
                      '至少要累積約 4 週，數字才有參考價值。</p>')
    if live["market"]:
        mm = live["market"]
        mr = "".join(report.row(f"{d:%m/%d}", f'{"✔" if h else "✘"}', f"評估 {esc(lab)}・實際 {a:+.2f}%")
                     for d, lab, a, h in mm["rows"])
        market_live = f'<p>大盤評估實際命中 <b class="num">{mm["hit"]:.0%}</b>（{mm["n"]} 天）</p><ul class="list">{mr}</ul>'
    else:
        market_live = '<p class="muted">還沒有已開獎的大盤評估紀錄。</p>'
    lv = f"""<section class="block" id="live" data-nav="實際紀錄"><h2 class="sec-title">實際紀錄（每天排程存下來的預測）</h2>
<p class="muted">上面的排行是拿歷史資料回頭算；這裡是每天當下真的做出的預測，事後比對結果，最能反映真實準度。</p>
<h3>大盤</h3>{market_live}<h3>個股方法（5 日／隔日平衡命中率）</h3>{stock_live}</section>"""

    note = """<section class="block" id="how" data-nav="怎麼看"><h2 class="sec-title">怎麼看這份週報</h2>
<ul class="list">
<li class="row"><span>平衡命中率<small>(看多時真的漲的比例 + 看空時真的跌的比例) ÷ 2。股票長期偏漲，一直喊多的方法單看命中率會虛高，這個算法可以排除。</small></span><span></span></li>
<li class="row"><span>50% 附近<small>跟擲銅板差不多，代表這個方法在這段期間沒有預測力。</small></span><span></span></li>
<li class="row"><span>55% 以上<small>在股市裡已經算有參考價值；超過 60% 很少見，也要小心只是運氣。</small></span><span></span></li>
<li class="row"><span>近 3 個月 vs 近 1 年<small>方法的準度會隨行情改變（趨勢盤、盤整盤適合的方法不同），兩個都高才比較可靠。</small></span><span></span></li>
<li class="row"><span>⚠ 只有單邊訊號<small>這段期間這個方法幾乎只喊多（或只喊空），平衡命中率只算了一邊，參考性較低。</small></span><span></span></li>
<li class="row"><span>三大法人籌碼<small>歷史資料只抓近期，不在歷史排行裡，會出現在「實際紀錄」。</small></span><span></span></li>
</ul></section>"""
    return report.shell("回測週報", f"{now:%Y/%m/%d %H:%M}", concl + rk + mk + lv + note, extra_css=EXTRA_CSS)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="每週回測：哪個方法最準")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    now = now_tpe()

    print("1/3 計算各方法歷史準度…")
    names = read_watchlist()
    panel, closes, taiex = build_panel(names)
    methods = method_table(panel)
    print("2/3 計算大盤預測方式準度（抓期交所一年夜盤）…")
    market, mk_n = market_table(taiex)
    print("3/3 比對每日實際紀錄…")
    live = live_table(closes, taiex)
    prev = save_history(methods, now)

    html = build_html(methods, market, mk_n, live, prev, now, panel["sym"].nunique())
    out = HERE / "報告"
    out.mkdir(exist_ok=True)
    path = out / "回測週報.html"
    path.write_text(html, encoding="utf-8")
    (out / f"回測週報_{now:%Y%m%d}.html").write_text(html, encoding="utf-8")

    print("\n▶ 近1年、預測5日：最準的方法")
    for i, r in enumerate(sorted(methods, key=lambda r: -main_score(r))[:5], 1):
        print(f"   {i}. {r['name']}　平衡命中率 {pct(main_score(r), 1)}")
    if market:
        e = market[0]["res"][("近1年", "close")]
        print(f"▶ 大盤最準：{market[0]['name']}（收盤方向 {pct(e['bal'] if e else None, 1)}）")
    print(f"\n報告：{path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
