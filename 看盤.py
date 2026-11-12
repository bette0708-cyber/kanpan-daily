"""每日看盤系統 —— 主程式

用法：
  python 看盤.py                 # 加權指數 + 自選股.txt 裡的股票
  python 看盤.py 2330 2317 0050  # 指定股票（上市/上櫃代號皆可）
  python 看盤.py --no-open       # 只產生報告，不自動開瀏覽器
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 工作排程器用 pythonw 執行時沒有視窗 → 輸出（含錯誤）寫到 報告/排程紀錄.txt
if sys.stdout is None:
    (HERE / "報告").mkdir(exist_ok=True)
    sys.stdout = sys.stderr = open(HERE / "報告" / "排程紀錄.txt", "w", encoding="utf-8")

import pandas as pd  # noqa: E402

from data import load_daily as yahoo_daily, now_tpe, resolve_tw_symbol, sync_watchlist  # noqa: E402  (Yahoo＋官方校正)
import chips  # noqa: E402
import manage  # noqa: E402
import masters  # noqa: E402
import monitor  # noqa: E402
import outlook  # noqa: E402
import patterns  # noqa: E402
import report  # noqa: E402


def read_watchlist() -> dict[str, str]:
    """{代號: 中文名}，中文名取自 # 後面的備註。電腦上執行時先同步 GitHub 上的最新清單。"""
    f = HERE / "自選股.txt"
    sync_watchlist(f)
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        code, _, note = line.partition("#")
        code = code.strip()
        if code:
            out[code.split()[0]] = note.strip()
    return out


LEDGER_DIR = HERE / "紀錄"


def drop_partial(df: pd.DataFrame) -> pd.DataFrame:
    """盤中還沒收盤的今天那根K線先拿掉：教學說單一K線要收盤才算數，盤中變化交給即時監看頁。"""
    now = now_tpe()
    if len(df) and df.index[-1].date() == now.date() and now.strftime("%H:%M") < "13:35":
        return df.iloc[:-1]
    return df


def save_ledger(rows: list[dict], o: dict, now) -> None:
    """把每次的預測存下來，每週回測時拿來跟實際結果比對（不偷看未來的真實紀錄）。"""
    LEDGER_DIR.mkdir(exist_ok=True)
    if rows:
        f = LEDGER_DIR / "個股預測.csv"
        df = pd.DataFrame(rows)
        if f.exists():
            df = pd.concat([pd.read_csv(f, dtype={"sym": str}), df], ignore_index=True)
        df.to_csv(f, index=False, encoding="utf-8-sig")
    if o.get("target") is not None:
        f = LEDGER_DIR / "大盤預測.csv"
        row = pd.DataFrame([{"run": f"{now:%Y-%m-%d %H:%M}", "target": f"{o['target']:%Y-%m-%d}",
                             "night": o["night_pct"], "us": o["us"], "score": round(o["score"], 4),
                             "label": o["label"][0]}])
        if f.exists():
            row = pd.concat([pd.read_csv(f), row], ignore_index=True)
        row.to_csv(f, index=False, encoding="utf-8-sig")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="每日看盤系統")
    ap.add_argument("symbols", nargs="*", help="股票代號，例如 2330 0050 6488")
    ap.add_argument("--no-open", action="store_true", help="不要自動開啟瀏覽器")
    ap.add_argument("--out", default=str(HERE / "報告"), help="報告輸出資料夾")
    args = ap.parse_args()

    names = read_watchlist()
    syms = args.symbols or list(names)
    print("抓取加權指數與各國指數…")
    taiex = drop_partial(yahoo_daily("^TWII", "5y")[0])
    world = outlook.load_world()
    print("抓取期交所台指期日盤/夜盤…")
    o = outlook.build(taiex, world)

    blocks, charts, summary, pool, ledger, watch = [], {}, [], [], [], []
    todo = [("^TWII", "台股加權指數", taiex)]
    for s in syms:
        full = resolve_tw_symbol(s)
        try:
            df, meta = yahoo_daily(full, "5y")
        except RuntimeError as e:
            print(f"  ✘ {s}: {e}")
            continue
        name = names.get(s) or meta.get("longName") or meta.get("shortName") or full
        todo.append((full, name, drop_partial(df)))

    chip_days = list(taiex.index[-10:])
    print("分析中（三大法人資料第一次抓會比較久，之後有快取）…")
    for k, (sym, name, df) in enumerate(todo):
        if len(df) < 30:
            print(f"  ✘ {sym}: 資料太少")
            continue
        d, sigs = patterns.analyze(df)
        res = masters.compute(d, taiex["close"])
        pool.append(pd.DataFrame({"score": masters.composite({k2: v[0] for k2, v in res.items()}),
                                  "close": d["close"]}))
        chip = None
        if sym.endswith((".TW", ".TWO")):
            market = "tpex" if sym.endswith(".TWO") else "twse"
            h = chips.history(sym.split(".")[0], market, chip_days)
            chip = chips.score(h, d["volume"].tail(20).mean() / 1000)
        m = masters.latest_table(res, chip)
        ledger.append({"run": f"{now_tpe():%Y-%m-%d %H:%M}", "date": f"{d.index[-1]:%Y-%m-%d}", "sym": sym,
                       "name": name, "close": round(float(d["close"].iat[-1]), 4),
                       **{r["key"]: r["score"] for r in m["rows"]},
                       "total": round(m["total"], 2), "short": round(m["short"], 2), "mid": round(m["mid"], 2)})
        cid = f"c{k}"
        blocks.append(report.symbol_html(sym, name, d, sigs, patterns.trend_state(d),
                                         patterns.summarize_backtest(sigs), cid, m))
        charts[cid] = report.chart_data(d, sigs)
        last = sigs[-1] if sigs else None
        watch.append(monitor.watch_item(sym, name, d, sigs, m))
        summary.append({"cid": cid, "name": name, "sym": sym, "close": d["close"].iat[-1],
                        "chg": (d["close"].iat[-1] / d["close"].iat[-2] - 1) * 100, "m": m, "last": last})
        print(f"  ✔ {name}（{sym}）綜合 {m['total']:.0f} 分 {m['verdict'][0]}｜短線 {m['short']:.0f}／中長線 {m['mid']:.0f}"
              + (f"｜K線訊號 {last.date:%m/%d} {'買' if last.side == 'buy' else '賣'}點 {last.status}" if last else ""))

    bt_master = masters.backtest_composite(pool)
    now = now_tpe()
    html = report.page(f"{now:%Y/%m/%d %H:%M}", report.outlook_html(o, world), blocks, charts,
                       report.summary_html(summary), report.masters_html(bt_master))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"看盤報告_{now:%Y%m%d_%H%M}.html"
    path.write_text(html, encoding="utf-8")
    (out_dir / "最新報告.html").write_text(html, encoding="utf-8")
    (out_dir / "即時監看.html").write_text(monitor.build_html(watch, o, now), encoding="utf-8")
    (out_dir / "自選股.html").write_text(manage.build_html(now), encoding="utf-8")
    save_ledger(ledger, o, now)

    if o.get("target") is not None:
        print(f"\n▶ {o['target']:%m/%d} {o['mode']}評估：{o['label'][0]}（分數 {o['score']:+.2f}，"
              f"夜盤 {o['night_pct']:+.2f}%，美股加權 {o['us'] if o['us'] is not None else 0:+.2f}%）")
    elif o.get("pending_note"):
        print("\n▶ " + o["pending_note"])
    bt = o.get("backtest", {})
    if bt.get("n"):
        print(f"  過去 {bt['n']} 天命中率：開盤 {bt['hit_gap']:.0%}、收盤 {bt['hit_close']:.0%}")
    print(f"\n報告：{path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
