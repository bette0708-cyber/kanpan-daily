"""大師方法綜合評估 —— 把國際公認、出書可查的經典技術分析方法量化成分數。

每個方法每天給一個分數：-2（明顯偏空）～ +2（明顯偏多），附判讀理由。
全部用「當天以前」的資料計算（不偷看未來），所以可以拿來做歷史回測。

方法與出處：
  趨勢（中長線）
    葛蘭碧八大法則   Joseph Granville《Granville's New Key to Stock Market Profits》（季線版）
    階段分析         Stan Weinstein《Secrets for Profiting in Bull and Bear Markets》
    趨勢模板         Mark Minervini《Trade Like a Stock Market Wizard》
    道氏理論         Charles Dow：高點/低點是否越墊越高
    CAN SLIM 量價突破 William O'Neil《How to Make Money in Stocks》
  動能（短線）
    MACD             Gerald Appel
    KD 隨機指標      George Lane（台股常用 9,3,3）
    RSI              J. Welles Wilder《New Concepts in Technical Trading Systems》
    布林通道         John Bollinger《Bollinger on Bollinger Bands》
    OBV 能量潮       Joseph Granville
  你的教學
    K 線型態         《看盤學習》12 張教學圖（patterns.py）
  籌碼（台股特有，只有近期資料，不進回測）
    三大法人         外資＋投信近 5 日買賣超
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from patterns import BUY, SELL


@dataclass
class Method:
    key: str
    name: str
    master: str
    group: str      # 趨勢 / 動能 / 教學 / 籌碼
    weight: float


METHODS = [
    Method("kline", "K線型態（你的教學）", "看盤學習 12 張圖", "教學", 1.5),
    Method("granville", "葛蘭碧八大法則", "Joseph Granville", "趨勢", 1.0),
    Method("weinstein", "階段分析", "Stan Weinstein", "趨勢", 1.0),
    Method("minervini", "趨勢模板", "Mark Minervini", "趨勢", 1.0),
    Method("dow", "道氏理論", "Charles Dow", "趨勢", 1.0),
    Method("oneil", "CAN SLIM 量價突破", "William O'Neil", "趨勢", 1.0),
    Method("macd", "MACD", "Gerald Appel", "動能", 0.75),
    Method("kd", "KD 隨機指標", "George Lane", "動能", 0.75),
    Method("rsi", "RSI", "J. Welles Wilder", "動能", 0.75),
    Method("boll", "布林通道", "John Bollinger", "動能", 0.75),
    Method("obv", "OBV 能量潮", "Joseph Granville", "動能", 0.75),
    Method("chips", "三大法人籌碼", "外資＋投信動向", "籌碼", 1.5),
]
METHOD_BY_KEY = {m.key: m for m in METHODS}


def _sel(conds, scores, texts, default_score, default_text, index):
    s = pd.Series(np.select(conds, scores, default=np.nan), index=index)
    t = pd.Series(np.select(conds, texts, default=""), index=index)
    s = s.fillna(default_score)   # 沒有符合任何條件的日子 → 用預設分數/理由
    t = t.where(t != "", default_text)
    return s, t


# ------------------------------------------------------------------ 趨勢
def granville(d):
    c, ma = d["close"], d["close"].rolling(60).mean()
    sl = ma.pct_change(10)
    rising, falling = sl > 0.005, sl < -0.005
    above = c > ma
    cu, cd = above & ~above.shift(1, fill_value=False), ~above & above.shift(1, fill_value=False)
    bias = c / ma - 1
    conds = [cu & ~falling, cd & ~rising,
             rising & above & (d["low"].rolling(5).min() < ma),
             falling & ~above & (d["high"].rolling(5).max() > ma),
             rising & above & (d["low"] <= ma * 1.02),
             falling & ~above & (d["high"] >= ma * 0.98),
             falling & (bias < -0.15), rising & (bias > 0.15)]
    sc = [2, -2, 1.5, -1.5, 1, -1, 1, -1]
    tx = ["買進法則1：季線止跌走平，股價由下往上突破",
          "賣出法則1：季線走平/下彎，股價由上往下跌破",
          "買進法則2：季線上揚，股價短暫跌破後站回",
          "賣出法則2：季線下彎，股價短暫站上後跌回",
          "買進法則3：季線上揚，回檔到季線附近守住",
          "賣出法則3：季線下彎，反彈到季線附近受壓",
          "買進法則4：季線下彎但股價離季線太遠（負乖離>15%），易跌深反彈",
          "賣出法則4：股價離季線太遠（正乖離>15%），易漲多拉回"]
    dflt_s = pd.Series(np.where(above & rising, 0.5, np.where(~above & falling, -0.5, 0)), index=d.index)
    dflt_t = pd.Series(np.where(above & rising, "站在上揚季線之上", np.where(~above & falling, "在下彎季線之下", "季線附近盤整")),
                       index=d.index)
    s, t = _sel(conds, sc, tx, dflt_s, dflt_t, d.index)
    t = t + "（季線 " + ma.round(1).astype(str) + "，乖離 " + (bias * 100).round(1).astype(str) + "%）"
    return s.where(ma.notna(), 0), t.where(ma.notna(), "資料不足")


def weinstein(d):
    c, ma = d["close"], d["close"].rolling(150).mean()   # 30 週均線
    sl = ma.pct_change(20)
    prev_fall = ma < ma.shift(60)
    conds = [(c > ma) & (sl > 0.01), (c < ma) & (sl < -0.01),
             (c > ma) & (sl < -0.01), (c < ma) & (sl > 0.01),
             (sl.abs() <= 0.01) & prev_fall, (sl.abs() <= 0.01) & ~prev_fall]
    sc = [2, -2, -0.5, 0.5, 0.5, -0.5]
    tx = ["第2階段（上升期）：股價在上揚的30週線之上 → 長線趨勢仍向上",
          "第4階段（下跌期）：股價在下彎的30週線之下 → 長線趨勢向下",
          "第4階段中的反彈：30週線仍下彎，反彈站上但趨勢未翻",
          "第2階段中的回檔：30週線仍上揚，暫時跌破",
          "第1階段（打底期）：跌勢後30週線走平",
          "第3階段（做頭期）：漲勢後30週線走平"]
    s, t = _sel(conds, sc, tx, 0, "階段不明", d.index)
    return s.where(ma.notna(), 0), t.where(ma.notna(), "資料不足（需150天）")


def minervini(d, bench):
    c = d["close"]
    m50, m150, m200 = (c.rolling(n).mean() for n in (50, 150, 200))
    lo52, hi52 = d["low"].rolling(250, min_periods=120).min(), d["high"].rolling(250, min_periods=120).max()
    rs = (c / c.shift(126)) / (bench.reindex(d.index).ffill() / bench.reindex(d.index).ffill().shift(126))
    checks = [c > m150, c > m200, m150 > m200, m200 > m200.shift(20), (m50 > m150) & (m50 > m200),
              c > m50, c >= lo52 * 1.3, c >= hi52 * 0.75, rs > 1]
    n = sum(x.fillna(False).astype(int) for x in checks)
    s = pd.Series(np.select([n >= 8, n >= 6, n >= 3, n >= 1], [2, 1, 0, -1], -2), index=d.index).astype(float)
    t = "符合 " + n.astype(str) + "/9 項（站上50/150/200日線、均線多頭排列、離52週高點25%內、強於大盤…）"
    return s.where(m200.notna(), 0), t.where(m200.notna(), "資料不足（需200天）")


def dow(d, k=3):
    h, l, c = d["high"].values, d["low"].values, d["close"].values
    n = len(d)
    s, t = np.zeros(n), np.array(["轉折點不足"] * n, dtype=object)
    sh, sl_ = [], []
    for i in range(n):
        j = i - k   # 第 j 根是否為轉折點，要到第 i 根才能確認（不偷看未來）
        if j - k >= 0:
            if h[j] == h[j - k:i + 1].max():
                sh.append(h[j])
            if l[j] == l[j - k:i + 1].min():
                sl_.append(l[j])
        if len(sh) >= 2 and len(sl_) >= 2:
            hh, hl = sh[-1] > sh[-2], sl_[-1] > sl_[-2]
            if hh and hl:
                v, txt = 1.5, "高點、低點都越墊越高 → 主要趨勢向上"
            elif not hh and not hl:
                v, txt = -1.5, "高點、低點都越來越低 → 主要趨勢向下"
            else:
                v, txt = 0, "高低點方向不一致 → 趨勢轉換/盤整"
            if c[i] > sh[-1]:
                v, txt = v + 0.5, txt + "；收盤突破前波高點"
            elif c[i] < sl_[-1]:
                v, txt = v - 0.5, txt + "；收盤跌破前波低點"
            s[i], t[i] = max(-2, min(2, v)), txt
    return pd.Series(s, index=d.index), pd.Series(t, index=d.index)


def oneil(d):
    c, v = d["close"], d["volume"]
    hh = d["high"].rolling(50).max().shift(1)
    v50 = v.rolling(50).mean().shift(1)
    m50 = c.rolling(50).mean()
    has_vol = v50 > 0
    brk = (c > hh) & (v >= 1.4 * v50) & has_vol
    recent = brk.rolling(5).max().fillna(0).astype(bool) & (c > m50)
    dist = (c < c.shift()) & (v >= 1.4 * v50) & (c < m50) & has_vol
    weak = (c < m50) & (m50 < m50.shift(10))
    conds = [brk, recent, dist, weak, (c > m50) & (c >= hh * 0.95)]
    sc = [2, 1, -1.5, -1, 0.5]
    tx = ["帶量突破50日新高（量 ≥ 1.4 倍均量）→ 經典買點",
          "近 5 日內帶量突破，仍站穩50日線",
          "放量下跌且跌破50日線 → 出貨跡象",
          "在下彎的50日線之下 → O'Neil 不買弱勢股（也提醒：虧損 7~8% 一定停損）",
          "接近50日高點、等待帶量突破"]
    return _sel(conds, sc, tx, 0, "沒有突破/出貨訊號", d.index)


# ------------------------------------------------------------------ 動能
def macd(d):
    c = d["close"]
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    g = (dif > dea) & (dif.shift() <= dea.shift())
    x = (dif < dea) & (dif.shift() >= dea.shift())
    g3, x3 = g.rolling(3).max().fillna(0).astype(bool), x.rolling(3).max().fillna(0).astype(bool)
    conds = [g3 & (dif < 0), g3, x3 & (dif > 0), x3, dif > dea]
    sc = [1.5, 1, -1.5, -1, 0.5]
    tx = ["零軸下黃金交叉（低檔轉強）", "黃金交叉", "零軸上死亡交叉（高檔轉弱）", "死亡交叉", "DIF 在訊號線之上"]
    s, t = _sel(conds, sc, tx, -0.5, "DIF 在訊號線之下", d.index)
    return s, t + "（DIF " + dif.round(2).astype(str) + "）"


def kd(d):
    lo, hi = d["low"].rolling(9).min(), d["high"].rolling(9).max()
    rsv = ((d["close"] - lo) / (hi - lo).replace(0, np.nan) * 100).fillna(50)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    dd = k.ewm(alpha=1 / 3, adjust=False).mean()
    g = (k > dd) & (k.shift() <= dd.shift())
    x = (k < dd) & (k.shift() >= dd.shift())
    conds = [g & (k < 30), x & (k > 70), g, x, k < 20, k > 80, k > dd]
    sc = [2, -2, 0.5, -0.5, 0.5, -0.5, 0.25]
    tx = ["低檔黃金交叉（K<30）", "高檔死亡交叉（K>70）", "黃金交叉", "死亡交叉", "超賣區（K<20），留意低檔鈍化",
          "超買區（K>80），留意高檔鈍化", "K 在 D 之上"]
    s, t = _sel(conds, sc, tx, -0.25, "K 在 D 之下", d.index)
    return s, t + "（K " + k.round(0).astype(int).astype(str) + " / D " + dd.round(0).astype(int).astype(str) + "）"


def rsi(d, n=14):
    c = d["close"]
    ch = c.diff()
    up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    r = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    r = r.fillna(50)
    bull = (c <= c.rolling(20).min() * 1.01) & (r > r.rolling(20).min() + 5)
    bear = (c >= c.rolling(20).max() * 0.99) & (r < r.rolling(20).max() - 5)
    conds = [bull, bear, r < 30, r > 70]
    sc = [1.5, -1.5, 1, -1]
    tx = ["底背離：股價破底但 RSI 沒破底 → 跌勢減弱", "頂背離：股價創高但 RSI 沒創高 → 漲勢減弱",
          "超賣（<30）", "超買（>70）"]
    s, t = _sel(conds, sc, tx, ((r - 50) / 50).clip(-0.5, 0.5), "中性區", d.index)
    return s, t + "（RSI " + r.round(0).astype(int).astype(str) + "）"


def boll(d):
    c = d["close"]
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    up, lo = mid + 2 * sd, mid - 2 * sd
    bw = (up - lo) / mid
    squeeze = bw <= bw.rolling(120, min_periods=40).min() * 1.15
    conds = [squeeze & (c > up), squeeze & (c < lo),
             (c.shift() < lo.shift()) & (c > lo), (c.shift() > up.shift()) & (c < up),
             c > up, c < lo, c > mid]
    sc = [1.5, -1.5, 1, -1, 0.5, -0.5, 0.25]
    tx = ["通道壓縮後向上突破 → 新行情", "通道壓縮後向下跌破", "跌出下軌後收回通道內 → 止跌",
          "衝出上軌後跌回通道內 → 漲勢降溫", "沿上軌走（強勢）", "沿下軌走（弱勢）", "在中軌（月線）之上"]
    s, t = _sel(conds, sc, tx, -0.25, "在中軌（月線）之下", d.index)
    pb = ((c - lo) / (up - lo)).round(2)
    return s.where(mid.notna(), 0), t + "（%b " + pb.astype(str) + "）"


def obv(d):
    c, v = d["close"], d["volume"]
    o = (np.sign(c.diff()).fillna(0) * v).cumsum()
    on = (o - o.shift(20)) / v.rolling(20).sum().replace(0, np.nan)
    pc = c / c.shift(20) - 1
    conds = [(pc < 0) & (on > 0.05), (pc > 0) & (on < -0.05), on > 0.05, on < -0.05]
    sc = [1, -1, 0.5, -0.5]
    tx = ["價跌量能卻增加 → 有人默默吸籌（背離）", "價漲量能卻減少 → 上漲沒量（背離）",
          "20 日量能流入", "20 日量能流出"]
    s, t = _sel(conds, sc, tx, 0, "量能持平", d.index)
    return s.where(on.notna(), 0), t.where(on.notna(), "無成交量資料")


# ------------------------------------------------------------------ 你的教學
def kline(d):
    net = pd.Series(0.0, index=d.index)
    for i, pats in enumerate(d["patterns"]):
        net.iat[i] = sum(p.strength for p in pats if p.side == BUY) - sum(p.strength for p in pats if p.side == SELL)
    s = (net + 0.6 * net.shift(1).fillna(0) + 0.3 * net.shift(2).fillna(0)) / 1.5
    s = s.clip(-2, 2)
    t = pd.Series("近 3 天沒有關鍵型態", index=d.index)
    for i in range(len(d)):
        names = []
        for k in range(3):
            if i - k >= 0:
                names += [p.name for p in d["patterns"].iat[i - k] if p.side in (BUY, SELL)]
        if names:
            t.iat[i] = "近 3 天：" + "、".join(dict.fromkeys(names))
    return s, t


# ------------------------------------------------------------------ 綜合
def compute(d: pd.DataFrame, bench: pd.Series) -> dict[str, tuple[pd.Series, pd.Series]]:
    """d 必須是 patterns.analyze() 的輸出（含 patterns 欄）。回傳 {key: (分數序列, 理由序列)}。"""
    return {"kline": kline(d), "granville": granville(d), "weinstein": weinstein(d),
            "minervini": minervini(d, bench), "dow": dow(d), "oneil": oneil(d),
            "macd": macd(d), "kd": kd(d), "rsi": rsi(d), "boll": boll(d), "obv": obv(d)}


def composite(scores: dict[str, pd.Series], keys=None) -> pd.Series:
    keys = keys or list(scores)
    num = sum(scores[k] * METHOD_BY_KEY[k].weight for k in keys)
    den = sum(METHOD_BY_KEY[k].weight for k in keys)
    return ((num / den) + 2) / 4 * 100   # 換成 0~100 分


def verdict(score: float) -> tuple[str, str]:
    if score >= 70:
        return "多數方法偏多", "up2"
    if score >= 57:
        return "略偏多", "up1"
    if score > 43:
        return "多空分歧／中性", "flat"
    if score > 30:
        return "略偏空", "dn1"
    return "多數方法偏空", "dn2"


def latest_table(res: dict, chips: tuple[float, str] | None) -> dict:
    """整理最後一天的各方法分數，算出綜合分、短線分、中長線分。"""
    rows = []
    for m in METHODS:
        if m.key == "chips":
            if chips is None:
                continue
            sc, why = chips
        else:
            sc, why = float(res[m.key][0].iat[-1]), str(res[m.key][1].iat[-1])
        rows.append({"key": m.key, "name": m.name, "master": m.master, "group": m.group,
                     "weight": m.weight, "score": round(sc, 2), "why": why})

    def agg(groups):
        rs = [r for r in rows if r["group"] in groups]
        if not rs:
            return None
        return (sum(r["score"] * r["weight"] for r in rs) / sum(r["weight"] for r in rs) + 2) / 4 * 100

    total = agg({"教學", "趨勢", "動能", "籌碼"})
    return {"rows": rows, "total": total, "short": agg({"教學", "動能", "籌碼"}), "mid": agg({"趨勢"}),
            "verdict": verdict(total)}


def backtest_composite(pool: list[pd.DataFrame]) -> pd.DataFrame:
    """pool：每檔一個 DataFrame（欄位 score, close）。統計分數區間 vs 之後 5/20 日報酬。"""
    rows = []
    for df in pool:
        f5 = df["close"].shift(-5) / df["close"] - 1
        f20 = df["close"].shift(-20) / df["close"] - 1
        x = pd.DataFrame({"score": df["score"], "f5": f5, "f20": f20}).iloc[200:]   # 前 200 天指標還沒算滿
        rows.append(x)
    all_ = pd.concat(rows).dropna(subset=["score"])
    bins = [0, 30, 43, 57, 70, 101]
    labels = ["≤30 偏空", "30~43 略偏空", "43~57 中性", "57~70 略偏多", "≥70 偏多"]
    all_["bucket"] = pd.cut(all_["score"], bins=bins, labels=labels, right=False)
    out = all_.groupby("bucket", observed=False).agg(
        n=("f5", lambda s: int(s.notna().sum())),
        avg5=("f5", "mean"), win5=("f5", lambda s: (s.dropna() > 0).mean() if s.notna().any() else np.nan),
        avg20=("f20", "mean"), win20=("f20", lambda s: (s.dropna() > 0).mean() if s.notna().any() else np.nan))
    return out
