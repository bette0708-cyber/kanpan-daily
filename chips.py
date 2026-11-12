"""三大法人買賣超（籌碼面）：上市＝證交所 T86、上櫃＝櫃買中心。

每天的資料抓一次就存到 快取/法人/，之後不重抓（證交所有流量限制）。
單位：張（1 張 = 1000 股）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from data import UA

CACHE = Path(__file__).resolve().parent / "快取" / "法人"


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return 0.0


def _fetch_twse(day: pd.Timestamp) -> dict | None:
    r = requests.get("https://www.twse.com.tw/rwd/zh/fund/T86",
                     params={"date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
                     headers=UA, timeout=30)
    j = r.json()
    if j.get("stat") != "OK" or not j.get("data") or j.get("date") != day.strftime("%Y%m%d"):
        return None
    # 4 外資(不含外資自營商) 7 外資自營商 10 投信 11 自營商合計 18 三大法人合計
    return {row[0].strip(): {"foreign": (_num(row[4]) + _num(row[7])) / 1000, "trust": _num(row[10]) / 1000,
                             "dealer": _num(row[11]) / 1000, "total": _num(row[18]) / 1000}
            for row in j["data"]}


def _fetch_tpex(day: pd.Timestamp) -> dict | None:
    r = requests.get("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
                     params={"type": "Daily", "sect": "EW", "date": day.strftime("%Y/%m/%d"), "id": "",
                             "response": "json"}, headers=UA, timeout=30)
    j = r.json()
    tables = j.get("tables") or []
    if not tables or not tables[0].get("data") or j.get("date") != day.strftime("%Y%m%d"):
        return None   # 假日或還沒公布
    # 10 外資及陸資合計 13 投信 22 自營商合計 23 三大法人合計
    return {row[0].strip(): {"foreign": _num(row[10]) / 1000, "trust": _num(row[13]) / 1000,
                             "dealer": _num(row[22]) / 1000, "total": _num(row[23]) / 1000}
            for row in tables[0]["data"]}


def load_day(day: pd.Timestamp, market: str) -> dict | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{day:%Y%m%d}_{market}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        data = _fetch_twse(day) if market == "twse" else _fetch_tpex(day)
    except (requests.RequestException, ValueError):
        data = None
    time.sleep(2.5)   # 證交所約每 5 秒 3 次以上會封鎖 IP
    if data:
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def history(code: str, market: str, days: list[pd.Timestamp]) -> pd.DataFrame:
    rows = []
    for d in days:
        data = load_day(d, market)
        if data and code in data:
            rows.append({"date": d, **data[code]})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def score(h: pd.DataFrame, avg_vol_lots: float) -> tuple[float, str] | None:
    """外資＋投信近 5 日買賣超，跟平均日成交量比較。"""
    if h.empty or len(h) < 3 or not avg_vol_lots:
        return None
    last5 = h.tail(5)
    fi = last5["foreign"].sum() + last5["trust"].sum()
    ratio = fi / avg_vol_lots
    trust_streak = 0
    for v in h["trust"].iloc[::-1]:
        if v > 0:
            trust_streak += 1
        else:
            break
    for_streak = 0
    for v in h["foreign"].iloc[::-1]:
        if (v > 0) == (h["foreign"].iat[-1] > 0) and v != 0:
            for_streak += 1
        else:
            break
    s = float(np.clip(ratio / 0.5, -2, 2))   # 5 日淨買超 = 半天成交量 → +1
    if trust_streak >= 3:
        s = min(2, s + 0.5)
    why = (f"近5日 外資 {last5['foreign'].sum():+,.0f} 張、投信 {last5['trust'].sum():+,.0f} 張、"
           f"自營 {last5['dealer'].sum():+,.0f} 張（合計約 {ratio:+.0%} 個日均量）")
    why += f"；外資連{'買' if h['foreign'].iat[-1] > 0 else '賣'} {for_streak} 天"
    if trust_streak >= 2:
        why += f"；投信連買 {trust_streak} 天"
    return round(s, 2), why
