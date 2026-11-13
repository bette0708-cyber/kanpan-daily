"""資料抓取：Yahoo Finance（各國指數、個股）＋ 期交所（台指期日盤/夜盤）。"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TPE = dt.timezone(dt.timedelta(hours=8))


def now_tpe() -> dt.datetime:
    return dt.datetime.now(TPE)


# ---------------------------------------------------------------- Yahoo
def yahoo_daily(symbol: str, range_: str = "1y") -> tuple[pd.DataFrame, dict]:
    """回傳日K DataFrame(index=日期, open/high/low/close/volume) 與 meta。"""
    last_err = None
    for host in ("query1", "query2", "query1"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}"
        try:
            r = requests.get(url, params={"range": range_, "interval": "1d"},
                             headers=UA, timeout=20)
            if r.status_code == 200:
                res = r.json()["chart"]["result"]
                if not res:
                    raise ValueError(f"{symbol} 查無資料")
                return _yahoo_to_df(res[0])
            last_err = RuntimeError(f"{symbol} HTTP {r.status_code}")
        except (requests.RequestException, KeyError, ValueError) as e:
            last_err = e
        time.sleep(1.2)
    raise RuntimeError(f"抓取 {symbol} 失敗：{last_err}")


def _yahoo_to_df(res: dict) -> tuple[pd.DataFrame, dict]:
    meta = res.get("meta", {})
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    off = meta.get("gmtoffset", 0)
    dates = (pd.to_datetime(ts, unit="s") + pd.Timedelta(seconds=off)).normalize()
    df = pd.DataFrame({k: q.get(k) for k in ("open", "high", "low", "close", "volume")},
                      index=dates)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[~df.index.duplicated(keep="last")]
    df["volume"] = df["volume"].fillna(0)
    return df.astype(float), meta


def resolve_tw_symbol(code: str) -> str:
    """'2330' → '2330.TW'（上市）或 '2330.TWO'（上櫃）；已含後綴或指數代號則原樣回傳。"""
    code = code.strip().upper()
    if "." in code or code.startswith("^") or not re.fullmatch(r"\d{4,6}[A-Z]?", code):
        return code
    for suf in (".TW", ".TWO"):
        try:
            df, _ = yahoo_daily(code + suf, "5d")
            if len(df):
                return code + suf
        except RuntimeError:
            pass
    return code + ".TW"


# ---------------------------------------------------------------- 官方日K（補 Yahoo 缺漏）
# 雲端（GitHub）用 快取/、紀錄/ 並存回 repo；電腦上執行改用 *_本機 資料夾，避免和雲端的檔案衝突
import os as _os
LOCAL_SUFFIX = "" if _os.environ.get("KANPAN_SITE") else "_本機"
CACHE_ROOT = Path(__file__).resolve().parent / f"快取{LOCAL_SUFFIX}"
OFFICIAL_CACHE = CACHE_ROOT / "行情"


def _roc_date(s: str) -> pd.Timestamp:
    y, m, d = s.strip().split("/")
    return pd.Timestamp(int(y) + 1911, int(m), int(d))


def _official_month(symbol: str, month: pd.Timestamp) -> pd.DataFrame:
    """證交所 / 櫃買中心 某個月的日K。已結束的月份存快取，當月每次重抓。"""
    code, _, suf = symbol.partition(".")
    done = month.to_period("M") < pd.Timestamp(now_tpe().date()).to_period("M")
    cache = OFFICIAL_CACHE / f"{symbol.replace('^', '')}_{month:%Y%m}.json"
    if done and cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        rows = []
        if symbol == "^TWII":
            r = requests.get("https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST",
                             params={"date": f"{month:%Y%m}01", "response": "json"}, headers=UA, timeout=20)
            j = r.json()
            for x in j.get("data") or []:
                rows.append([x[0], x[1], x[2], x[3], x[4], None])
        elif suf == "TW":
            r = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
                             params={"date": f"{month:%Y%m}01", "stockNo": code, "response": "json"},
                             headers=UA, timeout=20)
            j = r.json()
            for x in j.get("data") or []:   # 日期 成交股數 成交金額 開 高 低 收
                rows.append([x[0], x[3], x[4], x[5], x[6], x[1]])
        elif suf == "TWO":
            r = requests.get("https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock",
                             params={"code": code, "date": f"{month:%Y/%m}/01", "id": "", "response": "json"},
                             headers=UA, timeout=20)
            j = r.json()
            t = (j.get("tables") or [{}])[0]
            for x in t.get("data") or []:   # 日期 成交張數 成交仟元 開 高 低 收
                rows.append([x[0], x[3], x[4], x[5], x[6], str(_num(x[1]) * 1000)])
        time.sleep(2.5)   # 證交所流量限制
        if done and rows:
            OFFICIAL_CACHE.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    recs = []
    for d, o, h, l, c, v in rows:
        vals = [_num(o), _num(h), _num(l), _num(c)]
        if None in vals or 0 in vals:    # 當天沒成交（"--"）
            continue
        recs.append({"date": _roc_date(d), "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3],
                     "volume": _num(v) if v is not None else np.nan})
    return pd.DataFrame(recs).set_index("date") if recs else pd.DataFrame()


def patch_official(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """用官方最近兩個月的日K覆蓋 Yahoo（Yahoo 偶爾會漏掉某天或數字不準）。抓不到就維持原樣。"""
    if not (symbol == "^TWII" or symbol.endswith((".TW", ".TWO"))):
        return df
    this = pd.Timestamp(now_tpe().date()).replace(day=1)
    parts = []
    for m in (this - pd.DateOffset(months=1), this):
        try:
            parts.append(_official_month(symbol, m))
        except (requests.RequestException, ValueError, KeyError, IndexError):
            pass
    off = pd.concat([p for p in parts if not p.empty]) if any(not p.empty for p in parts) else pd.DataFrame()
    if off.empty:
        return df
    off = off[~off.index.duplicated(keep="last")]
    if off["volume"].isna().all():                       # 指數沒有成交量 → 沿用 Yahoo
        off["volume"] = df["volume"].reindex(off.index)
    out = df.reindex(df.index.union(off.index))          # 補上 Yahoo 漏掉的日期
    for col in ("open", "high", "low", "close", "volume"):
        ok = off[col].notna()
        out.loc[off.index[ok], col] = off.loc[ok, col]    # 官方數字優先
    out["volume"] = out["volume"].fillna(0)
    return out.sort_index().astype(float)


def load_daily(symbol: str, range_: str = "5y") -> tuple[pd.DataFrame, dict]:
    """Yahoo 歷史日K ＋ 官方近兩個月校正。看盤與回測都用這個。"""
    df, meta = yahoo_daily(symbol, range_)
    return patch_official(df, symbol), meta


# ---------------------------------------------------------------- 股票清單／自選股同步
STOCK_RE = re.compile(r"^(\d{4}|00\d{2,4}[A-Z]?)$")      # 一般股票 4 碼、ETF 00 開頭（排除權證、債券）
WATCHLIST_RAW = "https://raw.githubusercontent.com/bette0708-cyber/kanpan-daily/main/%E8%87%AA%E9%81%B8%E8%82%A1.txt"


def stock_list() -> list[list[str]]:
    """上市＋上櫃股票／ETF 清單 [[代號, 名稱, 'tse'|'otc'], ...]，給自選股頁面自動完成用。每天快取一次。"""
    cache = OFFICIAL_CACHE.parent / f"股票清單_{now_tpe():%Y%m%d}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    try:
        for x in requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=UA, timeout=30).json():
            if STOCK_RE.match(x.get("Code", "")):
                out[x["Code"]] = [x["Code"], x["Name"].strip(), "tse"]
        for x in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                              headers=UA, timeout=30).json():
            c = x.get("SecuritiesCompanyCode", "")
            if STOCK_RE.match(c) and c not in out:
                out[c] = [c, x["CompanyName"].strip(), "otc"]
    except (requests.RequestException, ValueError, KeyError):
        old = sorted(OFFICIAL_CACHE.parent.glob("股票清單_*.json"))
        return json.loads(old[-1].read_text(encoding="utf-8")) if old else []
    rows = sorted(out.values())
    if rows:
        cache.parent.mkdir(parents=True, exist_ok=True)
        for f in cache.parent.glob("股票清單_*.json"):
            f.unlink()
        cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def sync_watchlist(path: Path) -> None:
    """電腦上執行時，先從 GitHub 抓最新的自選股（在網頁上增刪的會存在那裡）。抓不到就用本機的。"""
    import os
    if os.environ.get("KANPAN_SITE"):        # 雲端執行時 repo 裡的就是最新的
        return
    try:
        r = requests.get(WATCHLIST_RAW, headers=UA, timeout=10, params={"t": int(time.time())})
        if r.status_code == 200 and "#" in r.text:
            text = r.content.decode("utf-8-sig")
            old = path.read_text(encoding="utf-8-sig") if path.exists() else ""
            if old.splitlines() != text.splitlines():   # 只有內容真的不同才寫（忽略換行符號差異）
                path.write_text(text, encoding="utf-8")
    except (requests.RequestException, OSError):
        pass


# ---------------------------------------------------------------- 期交所 台指期
SESSION_DAY, SESSION_NIGHT = "一般", "盤後"


def _taifex_csv(start: dt.date, end: dt.date) -> list[list[str]]:
    r = requests.post("https://www.taifex.com.tw/cht/3/futDataDown",
                      data={"down_type": "1", "commodity_id": "TX",
                            "queryStartDate": start.strftime("%Y/%m/%d"),
                            "queryEndDate": end.strftime("%Y/%m/%d")},
                      headers=UA, timeout=30)
    r.raise_for_status()
    text = r.content.decode("cp950", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return [row for row in rows[1:] if len(row) >= 18 and re.match(r"\d{4}/\d\d/\d\d", row[0])]


def _num(s: str) -> float | None:
    s = s.strip().replace(",", "").replace("%", "").replace("▲", "")
    if s.startswith("▼"):  # 網頁有時寫「▼124」、有時寫「▼-124」
        s = "-" + s[1:].lstrip("-")
    try:
        return float(s)
    except ValueError:
        return None


def taifex_tx(days: int = 120) -> pd.DataFrame:
    """台指期近月（每個交易日取當天有成交的最近月份合約）日盤＋夜盤。

    期交所把「夜盤」歸到下一個交易日：日期 D 的盤後行情 = D 前一個交易日 15:00 ~ D 當天 05:00。
    回傳欄位：date, session, contract, open, high, low, close, chg, chg_pct, volume
    """
    end = now_tpe().date()
    start = end - dt.timedelta(days=days)
    rows: list[list[str]] = []
    s = start
    while s <= end:  # 期交所單次查詢上限約一個月
        e = min(s + dt.timedelta(days=30), end)
        try:
            rows += _taifex_csv(s, e)
        except requests.RequestException:
            pass
        s = e + dt.timedelta(days=1)
    recs = []
    for row in rows:
        contract = row[2].strip()
        if not re.fullmatch(r"\d{6}", contract):  # 排除週選、價差
            continue
        vol = _num(row[9]) or 0
        if vol <= 0:
            continue
        recs.append({"date": pd.Timestamp(row[0].replace("/", "-")),
                     "session": row[17].strip(), "contract": contract,
                     "open": _num(row[3]), "high": _num(row[4]), "low": _num(row[5]),
                     "close": _num(row[6]), "chg": _num(row[7]), "chg_pct": _num(row[8]),
                     "volume": vol})
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df = df.sort_values(["date", "session", "contract"])
    df = df.groupby(["date", "session"], as_index=False).first()  # 近月
    return df.reset_index(drop=True)


def taifex_night_today() -> dict | None:
    """早上（日盤開盤前）CSV 可能還沒有今天的夜盤，改抓行情表網頁。"""
    today = now_tpe().date()
    try:
        r = requests.post("https://www.taifex.com.tw/cht/3/futDailyMarketReport",
                          data={"queryType": "2", "marketCode": "1", "MarketCode": "1",
                                "commodity_id": "TX", "commodity_idt": "TX",
                                "queryDate": today.strftime("%Y/%m/%d")},
                          headers=UA, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        return None
    text = r.content.decode("utf-8", errors="replace")  # 這個網頁是 UTF-8（CSV 下載才是 Big5）
    if today.strftime("%Y/%m/%d") not in text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|\s+", " ", text)
    m = re.search(r"TX (\d{6}) ([\d,.]+) ([\d,.]+) ([\d,.]+) ([\d,.]+) ([▲▼]?[\d,.-]+) ([▲▼]?[\d,.-]+)% ([\d,]+)", text)
    if not m:
        return None
    o, h, l, c = (_num(m.group(i)) for i in range(2, 6))
    return {"date": pd.Timestamp(today), "session": SESSION_NIGHT, "contract": m.group(1),
            "open": o, "high": h, "low": l, "close": c, "chg": _num(m.group(6)),
            "chg_pct": _num(m.group(7)), "volume": _num(m.group(8))}
