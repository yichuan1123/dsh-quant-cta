"""数据层：命令行/回测共用的 K 线来源。

三个来源，优先级从高到低：
1. 本地 CSV 缓存（data/cache/<code>.csv）—— 离线可复现
2. TqSdk 真实行情（需快期账号，见 credentials）
3. 确定性合成数据 —— 无凭据时的兜底，**结果里一定会标注 synthetic**

合成数据存在的理由：回测框架、撮合、成本模型必须先在没有网络/账号时
也能被验证。它绝不是"假行情代替真行情"，报告里会显著标明来源。
"""

import csv
import datetime as _dt
import hashlib
import math
import os
import random

from .specs import ContractSpec, get_spec
from .types import Bar

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data", "cache")
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".config")


def cache_dir() -> str:
    d = os.path.abspath(os.environ.get("QCTA_DATA_DIR") or CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def cache_path(code: str) -> str:
    return os.path.join(cache_dir(), code + ".csv")


# ───────────────────────────── 缓存读写 ─────────────────────────────

def save_cache(code: str, bars) -> str:
    path = cache_path(code)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume", "open_interest"])
        for b in bars:
            w.writerow([b.date, _fmt(b.open), _fmt(b.high), _fmt(b.low), _fmt(b.close), b.volume, b.open_interest])
    os.replace(tmp, path)
    return path


def _fmt(x: float) -> str:
    return ("%.6f" % x).rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def load_cache(code: str):
    path = cache_path(code)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(
                    Bar(
                        date=row["date"],
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume") or 0),
                        open_interest=float(row.get("open_interest") or 0),
                    )
                )
            except (KeyError, ValueError):
                continue
    out.sort(key=lambda b: b.date)
    return out


def slice_bars(bars, start: str = "", end: str = ""):
    lo = start or "0000-00-00"
    hi = end or "9999-99-99"
    return [b for b in bars if lo <= b.date <= hi]


# ───────────────────────────── 凭据 ─────────────────────────────

def credentials_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "credentials.json"))


def load_credentials():
    """返回 (user, password) 或 None。来源优先级：环境变量 > credentials.json。"""
    user = os.environ.get("QCTA_TQ_USER") or os.environ.get("TQ_USER")
    pwd = os.environ.get("QCTA_TQ_PASS") or os.environ.get("TQ_PASS")
    if user and pwd:
        return (user, pwd)
    path = credentials_path()
    if os.path.exists(path):
        try:
            import json

            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            u = obj.get("tq_user") or obj.get("user")
            p = obj.get("tq_pass") or obj.get("password")
            if u and p:
                return (str(u), str(p))
        except Exception:
            return None
    return None


# ───────────────────────────── 交易日历（合成数据用） ─────────────────────────────

_HOLIDAYS = set()


def _build_holidays(years):
    """粗略节假日：元旦、劳动节、国庆。用于合成数据的交易日序列。"""
    for y in years:
        for m, d in ((1, 1), (5, 1), (5, 2), (5, 3)):
            _HOLIDAYS.add("%04d-%02d-%02d" % (y, m, d))
        for d in range(1, 8):
            _HOLIDAYS.add("%04d-10-%02d" % (y, d))


def trading_days(start: str, end: str):
    y0 = int(start[:4])
    y1 = int(end[:4])
    if not _HOLIDAYS:
        _build_holidays(range(y0 - 1, y1 + 2))
    cur = _dt.date.fromisoformat(start)
    last = _dt.date.fromisoformat(end)
    out = []
    while cur <= last:
        if cur.weekday() < 5:
            iso = cur.isoformat()
            if iso not in _HOLIDAYS:
                out.append(iso)
        cur += _dt.timedelta(days=1)
    return out


# ───────────────────────────── 合成数据源 ─────────────────────────────

_SECTOR_VOL = {
    "黑色": 0.014,
    "有色": 0.012,
    "能化": 0.016,
    "农产品": 0.010,
    "贵金属": 0.011,
    "金融": 0.013,
}


def _round_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return x
    return round(round(x / tick) * tick, 8)


def synthetic_bars(code: str, start: str = "2018-01-01", end: str = "", spec: ContractSpec = None):
    """确定性合成日线：按品种代码哈希取种子，分段趋势 + 波动聚集。

    同一 code + 同一日期区间 → 永远同一组数据（便于测试与复现）。
    """
    spec = spec or get_spec(code)
    if not end:
        end = _dt.date.today().isoformat()
    seed = int(hashlib.sha256(("qcta:" + code).encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)

    days = trading_days(start, end)
    if not days:
        return []

    price = float(spec.base_price) or 1000.0
    vol_base = _SECTOR_VOL.get(spec.sector, 0.013)
    bars = []
    i = 0
    n = len(days)
    while i < n:
        seg_len = rng.randint(25, 110)
        drift = rng.gauss(0.0, 0.0022)
        vol = vol_base * rng.uniform(0.65, 1.7)
        for _ in range(seg_len):
            if i >= n:
                break
            o = price * (1.0 + rng.gauss(0.0, vol * 0.22))
            c = price * (1.0 + drift + rng.gauss(0.0, vol))
            hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol * 0.45)))
            lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol * 0.45)))
            o, hi, lo, c = (_round_tick(x, spec.tick) for x in (o, hi, lo, c))
            if lo <= 0 or c <= 0:
                c = max(spec.tick * 10, price)
                o = c
                hi = c
                lo = c
            bars.append(
                Bar(
                    date=days[i],
                    open=o,
                    high=max(hi, o, c),
                    low=min(lo, o, c),
                    close=c,
                    volume=float(int(rng.lognormvariate(11.0, 0.5))),
                    open_interest=float(int(rng.lognormvariate(11.5, 0.4))),
                )
            )
            price = c
            i += 1
    return bars


# ───────────────────────────── TqSdk 真实数据源 ─────────────────────────────

class TqSdkError(RuntimeError):
    pass


def fetch_tqsdk(code: str, data_length: int = 3000, spec: ContractSpec = None):
    """从天勤拉主连日线。需要快期账号（免费注册）。

    返回 (bars, adj_type_used)。adj_type 为 None 表示该版本不支持复权参数，
    此时主连拼接会有换月跳空 —— 报告里会提示。
    """
    spec = spec or get_spec(code)
    cred = load_credentials()
    if not cred:
        raise TqSdkError(
            "缺少快期账号凭据。请设置环境变量 QCTA_TQ_USER / QCTA_TQ_PASS，"
            "或在 " + credentials_path() + " 写入 {\"tq_user\":\"..\",\"tq_pass\":\"..\"}。"
        )
    try:
        from tqsdk import TqApi, TqAuth  # type: ignore
    except Exception as exc:  # pragma: no cover - 环境相关
        raise TqSdkError("未安装 tqsdk（pip install tqsdk）：%s" % exc)

    api = TqApi(auth=TqAuth(cred[0], cred[1]))
    try:
        sym = spec.tq_symbol()
        serial = None
        adj_used = None
        for adj in ("BFD", None):
            try:
                serial = api.get_kline_serial(sym, 86400, data_length=data_length, adj_type=adj)
                adj_used = adj
                break
            except TypeError:
                continue
        if serial is None:
            serial = api.get_kline_serial(sym, 86400, data_length=data_length)
        # 首次 wait_update 把历史 K 线填满；给足若干次机会。
        for _ in range(20):
            api.wait_update()
            if len(serial) > 0:
                break
        rows = []
        cols = list(serial.columns)
        for _, r in serial.iterrows():
            ns = r.get("datetime")
            if ns is None or (isinstance(ns, float) and math.isnan(ns)):
                continue
            ts = _dt.datetime.fromtimestamp(int(ns) / 1e9, tz=_dt.timezone(_dt.timedelta(hours=8)))
            o = float(r["open"]); h = float(r["high"]); l = float(r["low"]); c = float(r["close"])
            if any(math.isnan(x) for x in (o, h, l, c)) or o <= 0:
                continue
            rows.append(
                Bar(
                    date=ts.date().isoformat(),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=float(r.get("volume", 0) or 0),
                    open_interest=float(r.get("open_oi", r.get("close_oi", 0)) or 0),
                )
            )
        rows.sort(key=lambda b: b.date)
        if not rows:
            raise TqSdkError("天勤返回空数据（品种 %s）。请确认账号有该品种行情权限。" % code)
        return rows, adj_used
    finally:
        try:
            api.close()
        except Exception:
            pass


# ───────────────────────────── 统一入口 ─────────────────────────────

class DataBundle:
    """一次回测用到的全部行情 + 来源元信息。"""

    def __init__(self, bars_by_code, source: str, notes=None):
        self.bars_by_code = bars_by_code
        self.source = source
        self.notes = list(notes or [])

    @property
    def dates(self):
        s = set()
        for bars in self.bars_by_code.values():
            s.update(b.date for b in bars)
        return sorted(s)

    def coverage(self):
        out = {}
        for code, bars in self.bars_by_code.items():
            out[code] = {
                "bars": len(bars),
                "first": bars[0].date if bars else "",
                "last": bars[-1].date if bars else "",
            }
        return out


def load_bundle(codes, start: str, end: str, prefer_live: bool = False, refresh: bool = False) -> DataBundle:
    """按 缓存 → 天勤 → 合成 的顺序取数，并把实际来源记下来。"""
    bars_by_code = {}
    notes = []
    sources = set()

    for code in codes:
        spec = get_spec(code)
        cached = load_cache(code)
        if cached and not refresh:
            bars_by_code[code] = slice_bars(cached, start, end)
            sources.add("cache")
            continue

        bars = []
        src = None
        if prefer_live:
            try:
                bars, adj = fetch_tqsdk(code, spec=spec)
                save_cache(code, bars)
                notes.append("%s: 天勤实时拉取 %d 根，adj_type=%s" % (code, len(bars), adj))
                src = "tqsdk"
            except TqSdkError as exc:
                notes.append("%s: 天勤不可用（%s）" % (code, exc))
        # refresh 但没开 live / 或 live 失败：有缓存就必须用缓存，
        # 绝不能因为「刷新」把真实数据静默换成合成数据。
        if not bars and cached:
            bars = cached
            src = "cache"
            notes.append("%s: 使用本地缓存 %d 根" % (code, len(cached)))
        if not bars:
            bars = synthetic_bars(code, "2016-01-01", end or _dt.date.today().isoformat(), spec)
            src = "synthetic"
        bars_by_code[code] = slice_bars(bars, start, end)
        sources.add(src)

    if "synthetic" in sources:
        source = "synthetic" if sources == {"synthetic"} else "mixed"
        notes.append("警告：本次结果包含合成数据，只能用于验证流程，不可当作真实行情结论。")
    elif source_priority(sources) == "tqsdk":
        source = "tqsdk"
    else:
        source = "cache"
    return DataBundle(bars_by_code, source, notes)


def source_priority(sources):
    if "tqsdk" in sources:
        return "tqsdk"
    return "cache"
