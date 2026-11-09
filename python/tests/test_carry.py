"""期限结构（carry）的测试：斜率符号、年化、横截面去均值、缺数据不猜。"""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qcta import carry


class ExpiryTests(unittest.TestCase):
    def test_ordinal_is_monotonic(self):
        a = carry.expiry_ordinal(16, 5)
        b = carry.expiry_ordinal(16, 10)
        c = carry.expiry_ordinal(17, 1)
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_same_month_different_years(self):
        self.assertLess(carry.expiry_ordinal(16, 5), carry.expiry_ordinal(17, 5))


class CarrySeriesTests(unittest.TestCase):
    def test_contango_is_negative(self):
        # 近月 100、远月 105：远期更贵 = contango，多头换月要付钱
        contracts = {"SHFE.rb1605": {"2020-01-02": 100.0},
                     "SHFE.rb1610": {"2020-01-02": 105.0}}
        s = carry.carry_series(contracts)
        self.assertAlmostEqual(s["2020-01-02"],
                               math.log(100 / 105) * 365 / 155, places=10)
        self.assertLess(s["2020-01-02"], 0)

    def test_backwardation_is_positive(self):
        contracts = {"SHFE.rb1605": {"2020-01-02": 110.0},
                     "SHFE.rb1610": {"2020-01-02": 100.0}}
        s = carry.carry_series(contracts)
        self.assertGreater(s["2020-01-02"], 0)

    def test_single_contract_gives_nothing(self):
        self.assertEqual(carry.carry_series({"SHFE.rb1605": {"2020-01-02": 100.0}}), {})

    def test_picks_two_nearest_maturities(self):
        contracts = {
            "SHFE.rb1605": {"2020-01-02": 100.0},
            "SHFE.rb1610": {"2020-01-02": 104.0},
            "SHFE.rb1705": {"2020-01-02": 200.0},   # 远月价格离谱，若被选中结果会明显不同
        }
        s = carry.carry_series(contracts)
        self.assertAlmostEqual(s["2020-01-02"], math.log(100 / 104) * 365 / 155, places=10)

    def test_missing_day_skipped(self):
        contracts = {"SHFE.rb1605": {"2020-01-02": 100.0, "2020-01-03": 101.0},
                     "SHFE.rb1610": {"2020-01-02": 104.0}}
        s = carry.carry_series(contracts)
        self.assertIn("2020-01-02", s)
        self.assertNotIn("2020-01-03", s)      # 这天只有近月，算不出斜率就不算

    def test_czce_three_digit_codes(self):
        contracts = {"CZCE.SR601": {"2020-01-02": 5000.0},
                     "CZCE.SR605": {"2020-01-02": 5100.0}}
        s = carry.carry_series(contracts)
        self.assertLess(s["2020-01-02"], 0)


class CrossSectionTests(unittest.TestCase):
    def test_demeaned_by_median(self):
        frames = {"a": {"d": 0.10}, "b": {"d": 0.02}, "c": {"d": -0.06}}
        xs = carry.cross_section(frames, ["d"])
        self.assertAlmostEqual(sorted(xs["d"].values())[1], 0.0, places=12)   # 中位数被移到 0
        self.assertGreater(xs["d"]["a"], 0)
        self.assertLess(xs["d"]["c"], 0)

    def test_too_few_symbols_skipped(self):
        frames = {"a": {"d": 0.1}, "b": {"d": 0.0}}
        self.assertEqual(carry.cross_section(frames, ["d"]), {})


class StorageTests(unittest.TestCase):
    def test_save_load_roundtrip(self):
        d = tempfile.mkdtemp()
        carry.save_term("rb", {"SHFE.rb1605": {"2020-01-02": 1.0}}, term_dir=d)
        self.assertEqual(carry.load_term("rb", term_dir=d)["SHFE.rb1605"]["2020-01-02"], 1.0)

    def test_load_all_skips_missing(self):
        d = tempfile.mkdtemp()
        carry.save_term("rb", {"SHFE.rb1605": {"2020-01-02": 100.0},
                               "SHFE.rb1610": {"2020-01-02": 104.0}}, term_dir=d)
        got = carry.load_all(["rb", "i"], term_dir=d)
        self.assertIn("rb", got)
        self.assertNotIn("i", got)             # 没有数据就不参与，绝不猜

    def test_months_mapping(self):
        self.assertEqual(carry.months_for("rb"), [1, 5, 10])
        self.assertEqual(carry.months_for("au"), [6, 12])
        self.assertEqual(carry.months_for("unknown"), carry.DEFAULT_MONTHS)


class RollGapTests(unittest.TestCase):
    """换月缺口反推展期收益：这是不复权/前复权两条线的差值。"""

    def test_roll_gap_gives_carry(self):
        # 前复权连续；不复权在 2020-01-08 跳空 5%（远月比近月便宜 5% = backwardation）
        days = ["2020-01-0%d" % i for i in range(1, 10)]
        adj = {d: 100.0 for d in days}
        raw = {d: 100.0 for d in days}
        for d in days[7:]:
            raw[d] = 95.0
        s = carry.carry_from_rolls(raw, adj)
        # 第一次换月定基准，从这次起有值（这里只有一次换月 → 无输出）
        self.assertEqual(s, {})

    def test_rolls_produce_annualized_value(self):
        # 三次换月，每次都是「远月更便宜 5%」= backwardation，多头换月收钱
        days = ["2020-01-%02d" % i for i in range(1, 31)]
        adj = {d: 100.0 for d in days}
        raw = {}
        for i, d in enumerate(days):
            level = 100.0 if i < 9 else (95.0 if i < 19 else (90.25 if i < 29 else 85.7375))
            raw[d] = level
        s = carry.carry_from_rolls(raw, adj)
        keys = sorted(s)
        # 第一次换月只定基准，输出从第二次换月开始；段内取常数，所以每天都出现
        self.assertGreater(len(keys), 1)
        expected = -math.log(0.95) * 365 / 10
        for v in s.values():
            self.assertGreater(v, 0)
            self.assertAlmostEqual(v, expected, places=6)

    def test_contango_is_negative(self):
        # 每次换月新合约都更贵 5%：多头换月要付钱 → carry 为负
        days = ["2020-01-%02d" % i for i in range(1, 31)]
        adj = {d: 100.0 for d in days}
        raw = {}
        for i, d in enumerate(days):
            raw[d] = 100.0 if i < 9 else (105.0 if i < 19 else (110.25 if i < 29 else 115.7625))
        s = carry.carry_from_rolls(raw, adj)
        vals = list(s.values())
        self.assertTrue(vals)
        for v in vals:
            self.assertLess(v, 0)

    def test_identical_series_is_empty(self):
        days = ["2020-01-%02d" % i for i in range(1, 30)]
        same = {d: 100.0 for d in days}
        self.assertEqual(carry.carry_from_rolls(same, same), {})

    def test_short_overlap_is_empty(self):
        self.assertEqual(carry.carry_from_rolls({"2020-01-01": 1.0},
                                                {"2020-01-01": 1.0}), {})


class CarryStrategyTests(unittest.TestCase):
    """carry 策略腿：数据不够时必须空仓，而不是拿 3 个品种硬凑横截面。"""

    def _bundle(self, n_symbols):
        from qcta.data import DataBundle
        from qcta.types import Bar
        bars = {}
        for i in range(n_symbols):
            bars["s%d" % i] = [
                Bar(date="2020-01-0%d" % d, open=100, high=101, low=99, close=100,
                    volume=100.0, open_interest=0.0)
                for d in range(1, 6)
            ]
        return DataBundle(bars, "test", [])

    def test_insufficient_symbols_returns_empty(self):
        from qcta.strategies.carry import CarrySector
        self.assertEqual(CarrySector(min_symbols=6).generate(self._bundle(3), None), {})

    def test_disabled_returns_empty(self):
        from qcta.strategies.carry import CarrySector
        self.assertEqual(CarrySector(enabled=False).generate(self._bundle(10), None), {})


if __name__ == "__main__":
    unittest.main()
