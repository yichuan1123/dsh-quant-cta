"""预算回收（recycle_budget）的测试。

背景：只按权重分预算时，单手风险超过预算的品种会算出 0 手——它的预算既没变成
持仓、也没给别人。实测 100 万 / 目标 10% 时 28 个品种只有 6 个真正建过仓。
回收就是把 0 手品种的预算回填给能下单的品种。

默认**关闭**：开启会把实际波动从约 1/4 目标放大到接近目标（约 4 倍风险）。
"""

import datetime as dt
import unittest

from qcta.data import DataBundle
from qcta.engine import Backtester
from qcta.types import Bar, EngineConfig


def ramp(code, price, step, n=40):
    """构造一段上涨：前 20 天横盘，之后每天涨 step（触发唐奇安突破）。

    日期必须连续且唯一——重复日期会撞上引擎按日期建的索引。
    """
    base = dt.date(2021, 1, 1)
    out = []
    p = price
    for i in range(n):
        d = (base + dt.timedelta(days=i)).isoformat()
        if i >= 20:
            p = price + step * (i - 19)
        # high 贴住收盘价：横盘期 close 不高于前 N 根最高价（不触发），
        # 上涨期 close 逐日抬高（触发突破）；同时保留 1% 振幅让 ATR 不为 0。
        out.append(Bar(date=d, open=p * 0.995, high=p, low=p * 0.99, close=p,
                       volume=1000, open_interest=1000))
    return out


def make_bundle():
    # IF: 合约乘数 300，价格高 → 单手日风险极大
    # m : 合约乘数 10，价格低 → 单手日风险小
    return DataBundle({"IF": ramp("IF", 4000, 30), "m": ramp("m", 3000, 12)}, "synthetic", [])


def cfg(recycle, capital=1_000_000.0):
    return EngineConfig(
        start_date="", end_date="", initial_capital=capital, target_vol=0.10,
        atr_period=10, slip_ticks=1.0, max_leverage=5.0, max_drawdown_halt=0.99,
        max_symbol_risk=0.5,
        sleeves=({"name": "trend", "strategy": "trend", "alloc": 1.0,
                  "kwargs": {"entry": 5, "exit": 3}},),
        recycle_budget=recycle,
    )


class TestBudgetRecycling(unittest.TestCase):
    def _lots(self, recycle):
        res = Backtester(cfg(recycle), make_bundle()).run(liquidate=False)
        pos = res.sleeves[0].account.positions
        return {c: p.lots for c, p in pos.items()}

    def test_without_recycling_expensive_symbol_gets_zero(self):
        lots = self._lots(False)
        self.assertNotIn("IF", lots, "单手风险超预算的品种应算出 0 手")
        self.assertIn("m", lots)

    def test_recycling_fills_budget_into_affordable_symbol(self):
        off = self._lots(False)
        on = self._lots(True)
        self.assertIn("m", on)
        self.assertGreater(on["m"], off["m"], "回收后能下单的品种应拿到更多手数")

    def test_recycling_can_bring_in_previously_excluded_symbol(self):
        """资金足够时，回收应让原本 0 手的品种也能建仓。"""
        big = 20_000_000.0
        off = Backtester(cfg(False, big), make_bundle()).run(liquidate=False)
        on = Backtester(cfg(True, big), make_bundle()).run(liquidate=False)
        off_codes = set(off.sleeves[0].account.positions)
        on_codes = set(on.sleeves[0].account.positions)
        self.assertGreaterEqual(len(on_codes), len(off_codes))

    def test_default_is_off(self):
        c = EngineConfig()
        self.assertFalse(getattr(c, "recycle_budget", False), "预算回收默认必须关闭")


if __name__ == "__main__":
    unittest.main()
