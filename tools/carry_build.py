"""抓**不复权**主连日线，用来反推换月缺口（展期收益）。

为什么不用合约级数据：那需要 500+ 个合约（每个品种 12 年 × 3-4 个月份），
天勤的登录本身极不稳定（建连随机挂起），逐个抓 40 分钟还抓不完。
而不复权主连 + 前复权主连只差**换月缺口**这一件事：

    前复权序列 a_t = 不复权序列 p_t × k_t，k_t 在每个换月日跳一次
    换月缺口   e_d = (p_d/p_{d-1}) / (a_d/a_{d-1}) = k_{d-1}/k_d
    段内年化展期收益 carry = −ln(e_d) × 365 / 距上次换月的天数

这正是「多头持仓换月时收/付的钱」，比任何代理都直接。代价是它只在换月日更新
（一年 2-4 次），段内取常数——对横截面排序够用，对日内择时不够。

    python tools/carry_build.py --pool all
"""

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

from qcta import carry  # noqa: E402
from qcta.data import _serial_to_bars, load_cache, load_credentials, open_api  # noqa: E402
from qcta.specs import get_spec, resolve_pool  # noqa: E402


def raw_path(code):
    return os.path.join(carry.DATA_DIR, "raw", code + ".csv")


def save_raw(code, bars):
    p = raw_path(code)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write("date,close\n")
        for b in bars:
            fh.write("%s,%s\n" % (b.date, b.close))
    return p


def load_raw(code):
    p = raw_path(code)
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, "r", encoding="utf-8") as fh:
        next(fh, None)
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) != 2:
                continue
            try:
                out[parts[0]] = float(parts[1])
            except ValueError:
                continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="all")
    ap.add_argument("--bars", type=int, default=4200)
    ap.add_argument("--wait", type=float, default=4.0)
    ap.add_argument("--chunk", type=int, default=6, help="每次订阅几个品种就落盘")
    args = ap.parse_args(argv)

    codes = resolve_pool(args.pool)
    codes = [c for c in codes if not os.path.exists(raw_path(c))]   # 断点续跑
    if not codes:
        sys.stderr.write("所有品种都已抓过\n")
        return 0
    import time
    api = open_api(load_credentials())
    log = sys.stderr.write
    done = 0
    try:
        for i in range(0, len(codes), args.chunk):
            chunk = codes[i:i + args.chunk]
            serials = {}
            for code in chunk:
                sym = "KQ.m@" + get_spec(code).tq_symbol().split("@")[-1]
                try:
                    serials[code] = api.get_kline_serial(sym, 86400,
                                                         data_length=args.bars, adj_type=None)
                except Exception as exc:
                    log("  %-4s 订阅失败 %s\n" % (code, str(exc)[:60]))
            budget = time.time() + args.wait
            while time.time() < budget:
                try:
                    api.wait_update(deadline=time.time() + 0.5)
                except TypeError:
                    break
                except Exception:
                    break
                if serials and all(len(s) > 0 for s in serials.values()):
                    break
            for code, serial in serials.items():
                bars = _serial_to_bars(serial)
                if len(bars) < 200:
                    log("  %-4s 只有 %d 根，跳过\n" % (code, len(bars)))
                    continue
                save_raw(code, bars)
                adj_bars = load_cache(code)
                series = carry.carry_from_rolls({b.date: b.close for b in bars},
                                                {b.date: b.close for b in adj_bars})
                done += 1
                log("  %-4s 不复权 %d 根，换月段 %d\n" % (code, len(bars), len(series)))
    finally:
        try:
            api.close()
        except Exception:
            pass
    log("完成 %d 个品种\n" % done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
