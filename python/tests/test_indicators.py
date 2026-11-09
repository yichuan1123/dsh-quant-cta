"""指标测试：ADX 能不能区分「趋势」与「震荡」，以及退化输入的降级行为。"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qcta.indicators import adx, atr, rolling_max, rolling_min
from qcta.types import Bar


def bars_from_closes(closes, spread=1.0):
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(date="d%03d" % i, open=c, high=c + spread, low=c - spread,
                       close=c, volume=1000.0, open_interest=0.0))
    return out


class AdxTests(unittest.TestCase):
    def test_strong_uptrend_is_high(self):
        closes = [100 + i * 2 for i in range(120)]
        series = adx(bars_from_closes(closes), 14)
        last = series[-1]
        self.assertIsNotNone(last)
        self.assertGreater(last, 40)

    def test_chop_is_low(self):
        rnd = random.Random(4)
        closes = [100 + rnd.uniform(-1, 1) for _ in range(200)]
        series = adx(bars_from_closes(closes), 14)
        last = series[-1]
        self.assertIsNotNone(last)
        self.assertLess(last, 25)

    def test_warmup_is_none(self):
        series = adx(bars_from_closes([100 + i for i in range(60)]), 14)
        self.assertIsNone(series[0])
        self.assertTrue(any(v is not None for v in series))

    def test_short_input_returns_all_none(self):
        self.assertEqual(adx(bars_from_closes([100, 101, 102]), 14), [None, None, None])

    def test_flat_market_gives_zero_ish(self):
        series = adx(bars_from_closes([100.0] * 80), 14)
        last = series[-1]
        self.assertTrue(last is None or last == 0.0)


class ClassicIndicatorTests(unittest.TestCase):
    def test_atr_constant_range(self):
        bars = bars_from_closes([100.0] * 40, spread=1.0)
        series = atr(bars, 20)
        self.assertAlmostEqual(series[-1], 2.0, places=6)

    def test_rolling_max_min(self):
        xs = [1, 3, 2, 5, 4]
        # 预热 = window-1 个额外 None（索引 2 处还凑不满 3 个点）
        self.assertEqual(rolling_max(xs, 3), [None, None, None, 3, 5])
        self.assertEqual(rolling_min(xs, 3), [None, None, None, 1, 2])


if __name__ == "__main__":
    unittest.main()
