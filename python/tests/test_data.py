import unittest

from qcta.data import load_bundle, synthetic_bars, trading_days
from qcta.specs import get_spec


class TestSyntheticData(unittest.TestCase):
    def test_bars_are_valid_ohlc(self):
        bars = synthetic_bars("rb", "2019-01-01", "2019-12-31", get_spec("rb"))
        self.assertGreater(len(bars), 200)
        for b in bars:
            self.assertTrue(b.is_valid(), b)

    def test_deterministic(self):
        a = synthetic_bars("cu", "2020-01-01", "2020-06-30", get_spec("cu"))
        b = synthetic_bars("cu", "2020-01-01", "2020-06-30", get_spec("cu"))
        self.assertEqual([x.close for x in a], [x.close for x in b])

    def test_different_symbols_differ(self):
        a = synthetic_bars("rb", "2020-01-01", "2020-03-31")
        b = synthetic_bars("cu", "2020-01-01", "2020-03-31")
        self.assertNotEqual([x.close for x in a], [x.close for x in b])

    def test_prices_are_on_tick_grid(self):
        spec = get_spec("rb")
        for b in synthetic_bars("rb", "2020-01-01", "2020-02-29", spec):
            self.assertAlmostEqual(b.close / spec.tick, round(b.close / spec.tick), places=6)

    def test_trading_days_skip_weekends(self):
        days = trading_days("2020-01-01", "2020-01-15")
        for d in days:
            import datetime as dt
            self.assertLess(dt.date.fromisoformat(d).weekday(), 5)

    def test_bundle_without_credentials_falls_back_to_synthetic(self):
        b = load_bundle(["rb"], "2020-01-01", "2020-06-30", prefer_live=False)
        self.assertEqual(b.source, "synthetic")
        self.assertTrue(any("合成" in n for n in b.notes))


if __name__ == "__main__":
    unittest.main()
