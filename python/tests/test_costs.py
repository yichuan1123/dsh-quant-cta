"""成本（滑点）估计的测试：价差代理、封顶逻辑、成交额口径。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qcta import costs
from qcta.specs import get_spec
from qcta.types import Bar


def bar(date, o, h, l, c, vol=1000.0):
    return Bar(date=date, open=o, high=h, low=l, close=c, volume=vol, open_interest=0.0)


class QuantileTests(unittest.TestCase):
    def test_median_even_and_odd(self):
        self.assertEqual(costs.median([1, 2, 3]), 2)
        self.assertEqual(costs.median([1, 2, 3, 4]), 2.5)
        self.assertEqual(costs.median([]), 0.0)

    def test_quantile_interpolates(self):
        self.assertAlmostEqual(costs.quantile([0, 10], 0.5), 5.0)
        self.assertAlmostEqual(costs.quantile([0, 10, 20], 0.25), 5.0)
        self.assertEqual(costs.quantile([7], 0.9), 7)


class SlipRuleTests(unittest.TestCase):
    def test_half_spread_plus_half_tick(self):
        # 价差 2 跳 → 0.5×2 + 0.5 = 1.5 → round(1.5)=2（银行家舍入进到 2）
        ticks, method = costs.slip_ticks_from(spread_ticks=2.0)
        self.assertEqual(ticks, 2.0)
        self.assertEqual(method, "minute_spread")

    def test_spread_is_capped(self):
        ticks, method = costs.slip_ticks_from(spread_ticks=40.0)
        self.assertEqual(method, "minute_spread_capped")
        self.assertLessEqual(ticks, 2.0)      # 封顶 3 跳 → 0.5*3+0.5=2

    def test_floor_is_one_tick(self):
        ticks, _ = costs.slip_ticks_from(spread_ticks=0.2)
        self.assertEqual(ticks, 1.0)

    def test_daily_range_fallback(self):
        ticks, method = costs.slip_ticks_from(range_ticks=8.0)
        self.assertEqual(method, "daily_range_proxy")
        self.assertEqual(ticks, 1.0)          # 8/20=0.4 → 0.5*0.4+0.5=0.7 → 1

    def test_no_data_defaults_to_one(self):
        self.assertEqual(costs.slip_ticks_from(), (1.0, "default"))


class TableTests(unittest.TestCase):
    def setUp(self):
        self.specs = {"rb": get_spec("rb")}
        # 100 天，每天成交 1000 手 × 3000 元 × 10 吨 = 3000 万/日
        self.bars = {"rb": [bar("2024-01-%02d" % (i % 28 + 1), 3000, 3010, 2990, 3000) for i in range(100)]}

    def test_daily_stats(self):
        st = costs.daily_stats(self.bars["rb"], self.specs["rb"])
        self.assertAlmostEqual(st["median_amount"], 1000 * 3000 * 10)
        self.assertAlmostEqual(st["median_range_ticks"], 20.0)   # (3010-2990)/1
        self.assertEqual(st["days"], 100)

    def test_build_table_marks_illiquid(self):
        table = costs.build_table(self.bars, self.specs, min_amount=1e8)
        self.assertIn("rb", table)
        self.assertTrue(table["rb"]["illiquid"])       # 3000 万 < 1 亿
        self.assertEqual(table["rb"]["method"], "daily_range_proxy")

    def test_slip_map(self):
        table = {"rb": {"slip_ticks": 2.0}, "i": {}}
        m = costs.slip_map(table, fallback=1.0)
        self.assertEqual(m["rb"], 2.0)
        self.assertEqual(m["i"], 1.0)


class SaveLoadTests(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "costs.json")
        costs.save_costs({"rb": {"slip_ticks": 1.0}}, path=p)
        self.assertEqual(costs.load_costs(p)["rb"]["slip_ticks"], 1.0)

    def test_missing_file_returns_empty(self):
        self.assertEqual(costs.load_costs(os.path.join(os.path.dirname(__file__), "nope.json")), {})


if __name__ == "__main__":
    unittest.main()
