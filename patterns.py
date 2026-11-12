"""K 線型態辨識與買賣點 —— 規則全部依照《看盤學習》教學圖：

  IMG_4060  K線怎麼看：上影線=沖高受阻、下影線=下跌反彈
  IMG_4071  K線16種型態（陽線6、陰線6、十字星4）
  IMG_4065~4070  錘子紅K / 小陽線 / 倒錘紅K / 光頭紅K / 大陽線 / 光腳紅K
  IMG_4061  必背2根K線：看漲吞沒、看跌吞沒、穿刺線、烏雲蓋頂、錘子底、鑷子頂
  IMG_4062  必背3根K線：晨星、黃昏星、三白兵、三烏鴉
            ＋避坑：單一K線不算數，要等確認信號（後續K線＋成交量）
  IMG_4063  日線圖看形態：十字星平局、下影線超長=強力支撐、3連陽後續看漲
  IMG_4064  持續形態：上升三法、下降三法、看漲旗形、看跌旗形 ＋ 風控永遠第一

台股慣例：紅K = 收盤 > 開盤（上漲），綠/黑K = 收盤 < 開盤（下跌）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BUY, SELL, INFO = "buy", "sell", "info"


@dataclass
class Pattern:
    name: str
    side: str          # buy / sell / info
    strength: float    # 1 弱 ~ 3 強
    note: str          # 教學依據
    source: str        # 教學圖檔


@dataclass
class Signal:
    date: pd.Timestamp
    side: str
    score: float
    patterns: list[Pattern] = field(default_factory=list)
    status: str = "待確認"     # 待確認 / 已確認 / 未確認(失敗)
    stop: float = 0.0          # 風控停損價
    ret5: float | None = None  # 確認後 5 日報酬（回測用）


# ------------------------------------------------------------------ 基本量
def add_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    d["body"] = (c - o).abs()
    d["range"] = (h - l).replace(0, np.nan)
    d["upper"] = h - np.maximum(o, c)
    d["lower"] = np.minimum(o, c) - l
    d["red"] = c > o
    d["green"] = c < o
    d["mid"] = (o + c) / 2
    d["avg_body"] = d["body"].rolling(10, min_periods=3).mean().shift(1)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14, min_periods=5).mean()
    d["vol_ma5"] = d["volume"].rolling(5, min_periods=3).mean().shift(1)
    for n in (5, 10, 20, 60):
        d[f"ma{n}"] = c.rolling(n, min_periods=n).mean()
    return d


def trend_before(d: pd.DataFrame, i: int, look: int = 5) -> str:
    """看第 i 根之前的走勢：以 ATR 標準化 look 根的漲跌，判斷「上漲後 / 下跌後」。"""
    if i - look - 1 < 0:
        return "flat"
    chg = d["close"].iat[i - 1] - d["close"].iat[i - 1 - look]
    atr = d["atr"].iat[i - 1]
    if not atr or np.isnan(atr):
        return "flat"
    z = chg / atr
    if z <= -1.0:
        return "down"
    if z >= 1.0:
        return "up"
    return "flat"


# ------------------------------------------------------------------ 單根 16 型態
def classify(d: pd.DataFrame, i: int) -> str:
    """回傳 IMG_4071 的 16 種型態名稱之一。"""
    r = d.iloc[i]
    rng = r["high"] - r["low"]
    if rng <= 0:
        return "一字線"
    body, up, lo = r["body"], r["upper"], r["lower"]
    small = 0.08 * rng          # 視為「沒有影線」
    if body <= 0.1 * rng:       # 十字星家族
        if up <= small and lo > small:
            return "T字線"
        if lo <= small and up > small:
            return "倒T字線"
        return "十字星"
    color = "紅" if r["red"] else "黑"
    avg = r["avg_body"] if not np.isnan(r["avg_body"]) else body
    if up <= small and lo <= small:
        return "大陽線" if r["red"] else "大陰線"
    if lo >= 2 * body and up <= max(small, 0.5 * body):
        return f"錘子{color}K線"
    if up >= 2 * body and lo <= max(small, 0.5 * body):
        return f"倒錘{color}K線"
    if up <= small:
        return f"光頭{color}K線"      # 沒上影、有下影
    if lo <= small:
        return f"光腳{color}K線"      # 沒下影、有上影
    if body >= 1.5 * avg and up + lo <= 0.3 * rng:
        return "大陽線" if r["red"] else "大陰線"
    return "小陽線" if r["red"] else "小陰線"


def single_patterns(d: pd.DataFrame, i: int, kind: str, tr: str) -> list[Pattern]:
    r = d.iloc[i]
    rng = r["high"] - r["low"]
    avg = r["avg_body"] if not np.isnan(r["avg_body"]) else r["body"]
    big = r["body"] >= 1.2 * avg
    out: list[Pattern] = []
    if kind == "大陽線" and big:
        out.append(Pattern("大陽線", BUY, 2, "買方從始至終主導，強烈多頭信號、後續上漲機率高", "IMG_4069"))
    elif kind == "大陰線" and big:
        out.append(Pattern("大陰線", SELL, 2, "賣方從始至終主導，強烈空頭信號", "IMG_4071"))
    elif kind.startswith("錘子") and tr == "down":
        ratio = r["lower"] / max(r["body"], 1e-9)
        s = 1.5 + min(ratio / 4, 1.0) + (0.3 if kind == "錘子紅K線" else 0)
        out.append(Pattern(kind, BUY, round(s, 1),
                           "下跌後出現長下影：賣壓被多方拉回，下影線越長反彈信號越強", "IMG_4065"))
    elif kind.startswith("倒錘") and tr == "up":
        s = 1.5 + min(r["upper"] / max(r["body"], 1e-9) / 4, 1.0)
        out.append(Pattern(kind, SELL, round(s, 1),
                           "上漲後出現長上影：開盤後買盤推高但遭賣壓，上影線越長賣壓越強", "IMG_4067"))
    elif kind == "光頭紅K線" and r["lower"] >= 0.3 * rng:
        out.append(Pattern("光頭紅K線", BUY, 1, "跌破開盤後買方強勢介入、收在日高，低位積極吸納", "IMG_4068"))
    elif kind == "光腳紅K線" and tr == "up" and r["upper"] >= 0.3 * rng:
        out.append(Pattern("光腳紅K線", SELL, 1, "上漲後遭賣壓回落，長上影線表示高位積極賣出", "IMG_4070"))
    elif kind == "小陽線":
        out.append(Pattern("小陽線", INFO, 0.5, "多空勢均力敵、仍由買方主導，後市有機會續漲", "IMG_4066"))
    elif kind in ("十字星", "T字線", "倒T字線"):
        out.append(Pattern(kind, INFO, 0, "多空博弈平局，留意是否為轉折", "IMG_4063"))
    # 下影線超長 = 強力支撐（任一顏色）
    if rng > 0 and r["lower"] >= 0.6 * rng and tr != "up" and not kind.startswith("錘子"):
        out.append(Pattern("下影線超長", BUY, 1.5, "下影線超長代表有強力支撐", "IMG_4063"))
    return out


# ------------------------------------------------------------------ 2 根
def two_bar(d: pd.DataFrame, i: int, tr: str) -> list[Pattern]:
    if i < 1:
        return []
    p, c = d.iloc[i - 1], d.iloc[i]
    out: list[Pattern] = []
    tol = 0.001 * c["close"]  # 「同最高/同最低」容許 0.1%
    # 看漲吞沒：下跌後，陰線之後出現實體完全吃掉前者的大陽線
    if tr == "down" and p["green"] and c["red"] and c["close"] >= p["open"] and c["open"] <= p["close"] \
            and c["body"] > p["body"]:
        out.append(Pattern("看漲吞沒", BUY, 2.5, "下跌後大陽線吃掉前一根 = 可能要反轉", "IMG_4061"))
    # 看跌吞沒：上漲後，陽線之後出現吃掉前者的大陰線
    if tr == "up" and p["red"] and c["green"] and c["open"] >= p["close"] and c["close"] <= p["open"] \
            and c["body"] > p["body"]:
        out.append(Pattern("看跌吞沒", SELL, 2.5, "上漲後大陰線吃掉前一根 = 看跌信號", "IMG_4061"))
    # 穿刺線：下跌後，陽線開盤低於前低，但收在前一根陰線中點之上
    if tr == "down" and p["green"] and c["red"] and c["open"] < p["low"] and p["mid"] < c["close"] < p["open"]:
        out.append(Pattern("穿刺線", BUY, 2, "下跌後陽線開低卻收在前K中點之上 = 多頭反擊", "IMG_4061"))
    # 烏雲蓋頂：上漲後，陰線開盤高於前高，但收在前一根陽線中點之下
    if tr == "up" and p["red"] and c["green"] and c["open"] > p["high"] and p["open"] < c["close"] < p["mid"]:
        out.append(Pattern("烏雲蓋頂", SELL, 2, "上漲後陰線開高卻收在前K中點之下 = 趨勢要拐頭", "IMG_4061"))
    # 錘子底（平頭底）：兩根同最低
    if tr == "down" and abs(p["low"] - c["low"]) <= tol and c["close"] > c["low"] + 0.3 * (c["high"] - c["low"]):
        out.append(Pattern("錘子底", BUY, 1.5, "兩根K線同最低，低點守住 = 看漲信號", "IMG_4061"))
    # 鎚子頂（平頭頂）：兩根同最高
    if tr == "up" and abs(p["high"] - c["high"]) <= tol and c["close"] < c["high"] - 0.3 * (c["high"] - c["low"]):
        out.append(Pattern("鎚子頂", SELL, 1.5, "兩根K線同最高，高點過不去 = 看跌信號", "IMG_4061"))
    return out


# ------------------------------------------------------------------ 3 根
def three_bar(d: pd.DataFrame, i: int) -> list[Pattern]:
    if i < 2:
        return []
    a, b, c = d.iloc[i - 2], d.iloc[i - 1], d.iloc[i]
    avg = a["avg_body"] if not np.isnan(a["avg_body"]) else a["body"]
    long_ = lambda r: r["body"] >= 1.0 * avg
    smallb = lambda r: r["body"] <= 0.5 * avg
    tr = trend_before(d, i - 2)
    out: list[Pattern] = []
    if tr == "down" and a["green"] and long_(a) and smallb(b) and c["red"] and long_(c) and c["close"] > a["mid"]:
        out.append(Pattern("晨星", BUY, 3, "長陰→小實體→長陽，下跌末端黎明要到 = 看漲信號", "IMG_4062"))
    if tr == "up" and a["red"] and long_(a) and smallb(b) and c["green"] and long_(c) and c["close"] < a["mid"]:
        out.append(Pattern("黃昏星", SELL, 3, "長陽→小實體→長陰，上漲末端黃昏來臨 = 看跌信號", "IMG_4062"))
    three_red = all(r["red"] for r in (a, b, c)) and a["close"] < b["close"] < c["close"]
    three_grn = all(r["green"] for r in (a, b, c)) and a["close"] > b["close"] > c["close"]
    if three_red and b["open"] >= a["open"] and c["open"] >= b["open"]:
        if tr == "down":
            out.append(Pattern("三白兵", BUY, 2.5, "下跌後三根連漲陽線，下跌轉上漲 = 看漲信號", "IMG_4062"))
        else:
            out.append(Pattern("3連陽", BUY, 1.5, "日線3連陽，後續看漲", "IMG_4063"))
    if three_grn and b["open"] <= a["open"] and c["open"] <= b["open"]:
        if tr == "up":
            out.append(Pattern("三烏鴉", SELL, 2.5, "上漲後三根連跌陰線，上漲轉下跌 = 看跌信號", "IMG_4062"))
        else:
            out.append(Pattern("3連陰", SELL, 1.5, "三根連跌陰線，空方持續", "IMG_4062"))
    return out


# ------------------------------------------------------------------ 持續形態
def continuation(d: pd.DataFrame, i: int) -> list[Pattern]:
    out: list[Pattern] = []
    if i >= 4:
        w = d.iloc[i - 4:i + 1]
        first, mids, last = w.iloc[0], w.iloc[1:4], w.iloc[4]
        avg = first["avg_body"] if not np.isnan(first["avg_body"]) else first["body"]
        if first["red"] and first["body"] >= 1.2 * avg and last["red"] and last["body"] >= 1.2 * avg \
                and mids["high"].max() <= first["high"] and mids["low"].min() >= first["low"] \
                and (mids["body"] <= 0.6 * first["body"]).all() and last["close"] > first["close"]:
            out.append(Pattern("上升三法", BUY, 2.5, "長陽＋3根小K不破長陽範圍＋長陽 = 繼續上漲（別中途下車）", "IMG_4064"))
        if first["green"] and first["body"] >= 1.2 * avg and last["green"] and last["body"] >= 1.2 * avg \
                and mids["high"].max() <= first["high"] and mids["low"].min() >= first["low"] \
                and (mids["body"] <= 0.6 * first["body"]).all() and last["close"] < first["close"]:
            out.append(Pattern("下降三法", SELL, 2.5, "長陰＋3根小K不破長陰範圍＋長陰 = 繼續下跌", "IMG_4064"))
    out += flags(d, i)
    return out


def flags(d: pd.DataFrame, i: int) -> list[Pattern]:
    """旗形：急漲(跌)的旗桿 → 3~10 根窄幅橫盤 → 今天收盤突破旗面上沿(下沿)。"""
    out: list[Pattern] = []
    c = d["close"]
    for n in range(3, 11):                      # 旗面長度
        pole_end = i - n                        # 旗桿最後一根
        pole_start = pole_end - 5
        if pole_start < 0:
            break
        atr = d["atr"].iat[pole_end]
        if not atr or np.isnan(atr):
            continue
        pole = c.iat[pole_end] - c.iat[pole_start]
        flag = d.iloc[pole_end + 1:i]
        if len(flag) < 3:
            continue
        f_hi, f_lo = flag["high"].max(), flag["low"].min()
        if f_hi - f_lo > 0.6 * abs(pole):       # 橫盤幅度要明顯小於旗桿
            continue
        if pole >= 3 * atr and c.iat[i] > f_hi and c.iat[i - 1] <= f_hi:
            out.append(Pattern("看漲旗形", BUY, 2.5, f"急漲後橫盤{len(flag)}天，突破旗形上沿 = 上漲信號", "IMG_4064"))
            break
        if pole <= -3 * atr and c.iat[i] < f_lo and c.iat[i - 1] >= f_lo:
            out.append(Pattern("看跌旗形", SELL, 2.5, f"急跌後橫盤{len(flag)}天，跌破旗形下沿 = 下跌信號", "IMG_4064"))
            break
    return out


# ------------------------------------------------------------------ 主流程
BUY_TH = SELL_TH = 2.0   # 同一天同方向型態加總分數達門檻才算買/賣點


def analyze(df: pd.DataFrame) -> tuple[pd.DataFrame, list[Signal]]:
    d = add_features(df)
    kinds, pats_by_day = [], []
    for i in range(len(d)):
        kind = classify(d, i)
        tr = trend_before(d, i)
        pats = single_patterns(d, i, kind, tr) + two_bar(d, i, tr) + three_bar(d, i) + continuation(d, i)
        kinds.append(kind)
        pats_by_day.append(pats)
    d["kind"] = kinds
    d["patterns"] = pats_by_day

    signals: list[Signal] = []
    for i, pats in enumerate(pats_by_day):
        b = sum(p.strength for p in pats if p.side == BUY)
        s = sum(p.strength for p in pats if p.side == SELL)
        side = BUY if b - s >= BUY_TH else SELL if s - b >= SELL_TH else None
        if not side:
            continue
        r = d.iloc[i]
        sig = Signal(d.index[i], side, round(b - s if side == BUY else s - b, 1),
                     [p for p in pats if p.side == side],
                     stop=float(r["low"] if side == BUY else r["high"]))
        _confirm(d, i, sig)
        signals.append(sig)
    return d, signals


def _confirm(d: pd.DataFrame, i: int, sig: Signal) -> None:
    """避坑規則：單一K線不算數，隔天(或後天)要收盤延續方向＋量增才算確認。"""
    if i + 1 >= len(d):
        sig.status = "待確認"
        return
    base = d["close"].iat[i]
    for k in (1, 2):
        if i + k >= len(d):
            sig.status = "待確認"
            return
        nxt = d.iloc[i + k]
        vm = d["vol_ma5"].iat[i + k]
        vol_ok = (not vm) or np.isnan(vm) or nxt["volume"] == 0 or nxt["volume"] >= 0.9 * vm
        moved = nxt["close"] > base if sig.side == BUY else nxt["close"] < base
        broke = nxt["close"] < sig.stop if sig.side == BUY else nxt["close"] > sig.stop
        if broke:
            sig.status = "失敗(破停損)"
            return
        if moved and vol_ok:
            sig.status = "已確認"
            j = i + k
            if j + 5 < len(d):
                ret = d["close"].iat[j + 5] / d["close"].iat[j] - 1
                sig.ret5 = ret if sig.side == BUY else -ret
            return
    sig.status = "未確認"


def summarize_backtest(signals: list[Signal]) -> dict:
    out = {}
    for side in (BUY, SELL):
        rets = [s.ret5 for s in signals if s.side == side and s.ret5 is not None]
        out[side] = {"n": len(rets),
                     "win": (sum(r > 0 for r in rets) / len(rets)) if rets else None,
                     "avg": (float(np.mean(rets)) if rets else None)}
    return out


def trend_state(d: pd.DataFrame) -> dict:
    """均線排列，補充判斷目前處於上漲/下跌/盤整（教學裡「上漲後/下跌後」的依據）。"""
    r = d.iloc[-1]
    c = r["close"]
    ma = {n: r.get(f"ma{n}") for n in (5, 10, 20, 60)}
    above = [n for n, v in ma.items() if v is not None and not np.isnan(v) and c > v]
    if len(above) == 4 and ma[5] > ma[20]:
        label = "多頭排列（偏多）"
    elif not above and ma[5] < ma[20]:
        label = "空頭排列（偏空）"
    else:
        label = "盤整／轉折中"
    return {"label": label, "ma": ma, "above": above}
