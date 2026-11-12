"""大盤漲跌評估：前一晚台指期夜盤 ＋ 各國主要指數 → 評估目標交易日台股方向。

權重說明（可在下方 WEIGHTS / NIGHT_W 調整）：
  - 台指期夜盤：交易到清晨 05:00，已反映整晚美股，對隔天台股最直接 → 權重最高
  - 美股：台積電ADR、費半、那斯達克、標普、道瓊（台股權值股以半導體為主，所以 TSM/SOX 較重）
  - 亞洲、歐洲：只當參考，不進分數（與台股同時段或時差短）
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from data import SESSION_DAY, SESSION_NIGHT, now_tpe, taifex_night_today, taifex_tx, yahoo_daily

US = {  # 代號: (名稱, 權重)
    "TSM": ("台積電ADR", 0.25),
    "^SOX": ("費城半導體", 0.25),
    "^IXIC": ("那斯達克", 0.20),
    "^GSPC": ("標普500", 0.20),
    "^DJI": ("道瓊工業", 0.10),
}
OTHERS = {
    "^N225": ("日經225", "亞洲"), "^KS11": ("韓國KOSPI", "亞洲"), "^HSI": ("香港恆生", "亞洲"),
    "000001.SS": ("上證指數", "亞洲"), "^GDAXI": ("德國DAX", "歐洲"), "^FTSE": ("英國FTSE", "歐洲"),
    "ES=F": ("S&P期貨(即時)", "美期"), "NQ=F": ("那斯達克期貨(即時)", "美期"),
    "TWD=X": ("美元/台幣", "匯率"),
}
NIGHT_W, US_W = 0.6, 0.4


def _pct(df: pd.DataFrame) -> pd.Series:
    return df["close"].pct_change() * 100


def load_world(range_: str = "6mo") -> dict[str, dict]:
    out = {}
    for sym, info in {**{k: (v[0], "美國") for k, v in US.items()}, **OTHERS}.items():
        try:
            df, _ = yahoo_daily(sym, range_)
        except RuntimeError as e:
            out[sym] = {"name": info[0], "region": info[1], "error": str(e)}
            continue
        out[sym] = {"name": info[0], "region": info[1], "df": df, "pct": _pct(df)}
    return out


def us_score_before(world: dict, day: pd.Timestamp) -> tuple[float | None, list[dict]]:
    """取「台股 day 之前最後一個美股交易日」的加權漲跌%。"""
    total, wsum, rows = 0.0, 0.0, []
    for sym, (name, w) in US.items():
        info = world.get(sym, {})
        pct = info.get("pct")
        if pct is None:
            continue
        pct = pct[pct.index < day].dropna()
        if pct.empty:
            continue
        v = float(pct.iloc[-1])
        rows.append({"sym": sym, "name": name, "date": pct.index[-1], "pct": v, "w": w})
        total += v * w
        wsum += w
    return (total / wsum if wsum else None), rows


def label(score: float) -> tuple[str, str]:
    if score >= 1.0:
        return "強勢偏多", "up2"
    if score >= 0.3:
        return "偏多", "up1"
    if score > -0.3:
        return "持平震盪", "flat"
    if score > -1.0:
        return "偏空", "dn1"
    return "強勢偏空", "dn2"


def build(taiex: pd.DataFrame, world: dict) -> dict:
    fut = taifex_tx(120)
    night = fut[fut["session"] == SESSION_NIGHT].set_index("date") if not fut.empty else pd.DataFrame()
    dayf = fut[fut["session"] == SESSION_DAY].set_index("date") if not fut.empty else pd.DataFrame()

    now = now_tpe()
    today = pd.Timestamp(now.date())
    # 早上 CSV 可能還沒放今天的夜盤 → 抓行情網頁補
    if now.weekday() < 5 and (night.empty or today not in night.index) and now.hour >= 5:
        extra = taifex_night_today()
        if extra:
            row = pd.DataFrame([extra]).set_index("date")
            night = pd.concat([night, row[night.columns] if not night.empty else row]).sort_index()

    last_taiex_day = taiex.index[-1]
    taiex_closed = not (last_taiex_day == today and now.time() < dt.time(13, 35))

    # ------------------------------------------------ 目標交易日
    target, mode = None, ""
    if not night.empty:
        n_last = night.index.max()
        if n_last > last_taiex_day or (n_last == last_taiex_day and not taiex_closed):
            target = n_last
            mode = "當日" if n_last == today else "隔日"
    pending_note = None
    if target is None:
        # 今天已收盤，今晚夜盤要到明早 05:00 才結束
        pending_note = ("今晚夜盤（15:00～明早05:00）尚未結束，下方先以「美股期貨即時」＋ 歐股暫估；"
                        "明早 05:00 以後再跑一次，就會用完整夜盤評估明天的走勢。")

    res: dict = {"now": now, "target": target, "mode": mode, "pending_note": pending_note,
                 "taiex_last": last_taiex_day, "night_table": night, "day_table": dayf}

    if target is not None:
        nrow = night.loc[target]
        npct = float(nrow["chg_pct"]) if pd.notna(nrow["chg_pct"]) else None
        us, us_rows = us_score_before(world, target)
        parts = [(npct, NIGHT_W), (us, US_W)]
        parts = [(v, w) for v, w in parts if v is not None]
        score = sum(v * w for v, w in parts) / sum(w for _, w in parts)
        prev_close = float(taiex["close"][taiex.index < target].iloc[-1])
        res.update({"night": nrow, "night_pct": npct, "us": us, "us_rows": us_rows, "score": score,
                    "label": label(score), "prev_close": prev_close,
                    "est_open": prev_close * (1 + (npct or 0) / 100)})
    else:
        live = []
        for sym in ("ES=F", "NQ=F", "^GDAXI"):
            info = world.get(sym, {})
            if "df" in info and info["df"].index[-1] >= last_taiex_day:
                live.append((info["name"], float(info["pct"].iloc[-1])))
        if live:
            sc = float(np.mean([v for _, v in live]))
            res.update({"provisional": live, "score": sc, "label": label(sc)})

    res["backtest"] = backtest(taiex, night, world)
    return res


def backtest(taiex: pd.DataFrame, night: pd.DataFrame, world: dict) -> dict:
    """用過去資料檢驗：夜盤＋美股分數的方向，與台股當天『開盤跳空』『收盤漲跌』是否一致。"""
    if night.empty:
        return {"n": 0}
    t = taiex.copy()
    t["prev_close"] = t["close"].shift()
    rows = []
    for day in night.index:
        if day not in t.index or np.isnan(t.at[day, "prev_close"]):
            continue
        npct = night.at[day, "chg_pct"]
        if npct is None or np.isnan(npct):
            continue
        us, _ = us_score_before(world, day)
        score = npct * NIGHT_W + (us if us is not None else npct) * US_W
        if abs(score) < 0.3:   # 持平不算
            continue
        gap = t.at[day, "open"] / t.at[day, "prev_close"] - 1
        close = t.at[day, "close"] / t.at[day, "prev_close"] - 1
        rows.append({"date": day, "score": score, "gap": gap * 100, "close": close * 100,
                     "hit_gap": np.sign(gap) == np.sign(score), "hit_close": np.sign(close) == np.sign(score)})
    if not rows:
        return {"n": 0}
    df = pd.DataFrame(rows)
    return {"n": len(df), "hit_gap": df["hit_gap"].mean(), "hit_close": df["hit_close"].mean(),
            "rows": df.tail(15).iloc[::-1]}
