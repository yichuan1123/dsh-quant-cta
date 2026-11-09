import unittest

from qcta.metrics import compute_metrics, daily_returns
from qcta.types import DailyRecord


def curve_from(equities, start_day=1):
    out = []
    for i, e in enumerate(equities):
        out.append(DailyRecord(
            date="2020-01-%02d" % (start_day + i),
            equity=e, cash=e, margin=0.0, gross_exposure=0.0, net_exposure=0.0, drawdown=0.0,
        ))
    return out


class TestMetrics(unittest.TestCase):
    def test_total_return_and_max_drawdown(self):
        m = compute_metrics(curve_from([1000, 1100, 990, 1200]), [], 1000)
        self.assertAlmostEqual(m["total_return_pct"], 20.0, places=6)
        # 峰值 1100 跌到 990 → 10%
        self.assertAlmostEqual(m["max_drawdown_pct"], 10.0, places=6)

    def test_daily_returns(self):
        r = daily_returns([100, 110, 99])
        self.assertAlmostEqual(r[0], 0.1, places=9)
        self.assertAlmostEqual(r[1], -0.1, places=9)

    def test_sharpe_zero_for_flat_curve(self):
        m = compute_metrics(curve_from([1000] * 50), [], 1000)
        self.assertEqual(m["sharpe"], 0.0)

    def test_monthly_buckets(self):
        c = curve_from([1000, 1010])
        c[-1].date = "2020-02-03"
        m = compute_metrics(c, [], 1000)
        self.assertAlmostEqual(m["monthly_returns_pct"]["2020-01"], 0.0, places=6)
        self.assertAlmostEqual(m["monthly_returns_pct"]["2020-02"], 1.0, places=6)

    def test_empty_curve_is_reported(self):
        self.assertIn("error", compute_metrics([], [], 1000))


if __name__ == "__main__":
    unittest.main()
