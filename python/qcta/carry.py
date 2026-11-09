"""期限结构 / 展期收益（carry）。

**为什么需要它**：趋势跟踪赚的是「价格往一个方向走」，carry 赚的是「曲线形状」——
近月比远月贵（backwardation）时，多头换月是收钱；远月贵（contango）时，多头换月在付钱。
这是商品期货里最古老、也最有经济含义的一类信号（仓储/便利收益）。

**数据口径**：需要同一品种不同到期月的价格，所以必须有合约级数据（主连不够）。
`tools/term_build.py` 把各品种的挂牌合约日线抓到 `data/term/<code>.json`，
这里负责拼成逐日 carry 序列。

定义（年化对数斜率）：
    carry = ln(P_near / P_far) × 365 / (T_far − T_near)
正值 = backwardation（多头收展期收益），负值 = contango。

单一品种的 carry 是**绝对**量，跨品种比较时对它做横截面标准化（减去当日中位数），
这样信号只表达「谁比谁更陡」，不受整体商品曲线水平影响。
"""

import json
import math
import os

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))
TERM_DIR = os.path.join(DATA_DIR, "term")

# 各品种的主要挂牌月份（取主力可能轮到的月份，不必穷举全部 12 个月）
MONTHS = {
    "rb": [1, 5, 10], "hc": [1, 5, 10], "i": [1, 5, 9], "j": [1, 5, 9], "jm": [1, 5, 9],
    "m": [1, 5, 9], "y": [1, 5, 9], "p": [1, 5, 9], "c": [1, 5, 9],
    "SR": [1, 5, 9], "CF": [1, 5, 9], "FG": [1, 5, 9], "TA": [1, 5, 9], "MA": [1, 5, 9],
    "ru": [1, 5, 9], "cu": [1, 5, 9], "al": [1, 5, 9], "zn": [1, 5, 9], "ni": [1, 5, 9],
    "au": [6, 12], "ag": [6, 12], "sc": [1, 5, 9],
    "IF": [3, 6, 9, 12], "IH": [3, 6, 9, 12], "IC": [3, 6, 9, 12], "IM": [3, 6, 9, 12],
    "T": [3, 6, 9, 12],
}
DEFAULT_MONTHS = [1, 5, 9]


def months_for(code):
    return MONTHS.get(code, DEFAULT_MONTHS)


def expiry_ordinal(yy, mm):
    """交割月的近似到期日（第 15 天）。只用于算两个到期月之间的时间差，误差几天不影响斜率。"""
    year = 2000 + int(yy) if int(yy) < 100 else int(yy)
    return year * 372 + int(mm) * 31 + 15


def term_path(code, term_dir=None):
    return os.path.join(term_dir or TERM_DIR, code + ".json")


def save_term(code, contracts, term_dir=None, meta=None):
    os.makedirs(term_dir or TERM_DIR, exist_ok=True)
    payload = {"code": code, "meta": meta or {}, "contracts": contracts}
    with open(term_path(code, term_dir), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return term_path(code, term_dir)


def load_term(code, term_dir=None):
    p = term_path(code, term_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("contracts") or {}
    except Exception:
        return {}


def carry_series(contracts):
    """把 {合约代码: {date: close}} 拼成 {date: 年化 carry}。

    每个交易日取「最近到期且当日有价」的两个合约，算年化对数斜率。
    只有一个合约有价时跳过（算不出斜率，绝不猜）。
    """
    if not contracts:
        return {}
    # 每个合约的（年, 月）用于排序：合约代码尾部 4 位（CZCE 3 位）
    meta = {}
    for sym in contracts:
        tail = "".join(ch for ch in sym.split(".")[-1] if ch.isdigit())
        if len(tail) >= 4:
            mm = int(tail[-2:])
            yy = int(tail[-4:-2])
        elif len(tail) == 3:
            mm = int(tail[-2:])
            yy = int(tail[-3])
        else:
            continue
        if not (1 <= mm <= 12):
            continue
        meta[sym] = expiry_ordinal(yy, mm)

    dates = set()
    for series in contracts.values():
        dates.update(series.keys())
    out = {}
    for d in sorted(dates):
        live = [(meta[s], s) for s in contracts if s in meta and d in contracts[s]]
        if len(live) < 2:
            continue
        live.sort()
        (t1, s1), (t2, s2) = live[0], live[1]
        p1 = contracts[s1][d]
        p2 = contracts[s2][d]
        if not (p1 > 0 and p2 > 0) or t2 <= t1:
            continue
        days = float(t2 - t1)
        try:
            out[d] = math.log(p1 / p2) * 365.0 / days
        except ValueError:
            continue
    return out


ROLL_TOL = 1e-4     # 换月缺口阈值（1bp）：低于它视为「同一条曲线」，是数值噪声


def carry_from_rolls(raw, adj):
    """⚠️ 实测在天勤上**不可用**：KQ.m@ 主连在 `adj_type=None` 与 `adj_type='F'` 下返回
    完全相同的序列（2633 天逐点相同），说明主连本身已经是处理过换月的合成序列，
    复权参数对主连是空操作 —— 拿不到「不复权的原始跳空」。

    保留这个函数是因为它对**别的数据源**（自己拼的合约连续、交易所原始行情）仍然有效，
    并且有单测钉住数学口径。当前可用的路径只有合约级：`tools/term_build.py`。
    """

    """不复权 + 前复权两条主连 → 逐日年化展期收益。

    前复权 a = 不复权 p × k，k 只在换月日跳一次：
        e_d = (p_d/p_{d−1}) / (a_d/a_{d−1}) = k_{d−1}/k_d
        carry_d = −ln(e_d) × 365 / (距上次换月的自然日数)
    换月日之间取常数（一年只有 2-4 次更新），这是**段的年化**，不是逐日新信息。
    第一次换月只用来定基准（没有「上一段」可言），从第二次起才产出数值。
    """
    import datetime as _dt

    dates = sorted(set(raw) & set(adj))
    out = {}
    if len(dates) < 3:
        return out
    last_roll = None
    carry = None
    for i in range(1, len(dates)):
        d, d0 = dates[i], dates[i - 1]
        p0, p1 = raw.get(d0), raw.get(d)
        a0, a1 = adj.get(d0), adj.get(d)
        if not (p0 and p1 and a0 and a1) or p0 <= 0 or a0 <= 0:
            continue
        gap = math.log(p1 / p0) - math.log(a1 / a0)
        if abs(gap) < ROLL_TOL:
            if carry is not None:
                out[d] = carry
            continue
        if last_roll is None:
            last_roll = d
            continue
        try:
            days = (_dt.date.fromisoformat(d) - _dt.date.fromisoformat(last_roll)).days
        except ValueError:
            continue
        if days <= 0:
            last_roll = d
            continue
        carry = -gap * 365.0 / days
        out[d] = carry
        last_roll = d
    return out


def load_roll_frames(codes, raw_dir=None):
    """从 data/raw/*.csv（不复权）+ data/cache/*.csv（前复权）构建 carry 序列。"""
    import csv as _csv
    import os

    raw_dir = raw_dir or os.path.join(DATA_DIR, "raw")
    cache_dir = os.path.join(DATA_DIR, "cache")
    out = {}
    for code in codes:
        rp = os.path.join(raw_dir, code + ".csv")
        cp = os.path.join(cache_dir, code + ".csv")
        if not (os.path.exists(rp) and os.path.exists(cp)):
            continue
        raw, adj = {}, {}
        with open(rp, "r", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                try:
                    raw[row["date"]] = float(row["close"])
                except (KeyError, ValueError):
                    continue
        with open(cp, "r", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                try:
                    adj[row["date"]] = float(row["close"])
                except (KeyError, ValueError):
                    continue
        s = carry_from_rolls(raw, adj)
        if s:
            out[code] = s
    return out


def load_all(codes, term_dir=None):
    """返回 {code: {date: carry}}，只含真的抓到合约数据的品种。"""
    out = {}
    for code in codes:
        contracts = load_term(code, term_dir)
        if not contracts:
            continue
        s = carry_series(contracts)
        if s:
            out[code] = s
    return out


def cross_section(frames, dates):
    """按日做横截面去均值，返回 {date: {code: demeaned_carry}}。"""
    out = {}
    for d in dates:
        row = {c: f[d] for c, f in frames.items() if d in f}
        if len(row) < 3:
            continue
        mid = sorted(row.values())[len(row) // 2]
        out[d] = {c: v - mid for c, v in row.items()}
    return out
