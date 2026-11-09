import unittest

import os
import tempfile
import unittest

from qcta import data as data_mod
from qcta.data import load_bundle, save_cache, synthetic_bars, trading_days
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
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("QCTA_DATA_DIR")
            os.environ["QCTA_DATA_DIR"] = tmp
            try:
                b = load_bundle(["rb"], "2020-01-01", "2020-06-30", prefer_live=False)
                self.assertEqual(b.source, "synthetic")
                self.assertTrue(any("合成" in n for n in b.notes))
            finally:
                if old is None:
                    os.environ.pop("QCTA_DATA_DIR", None)
                else:
                    os.environ["QCTA_DATA_DIR"] = old

    def test_refresh_keeps_cache_instead_of_synthetic(self):
        """--refresh 但没开 live 时，必须继续用缓存，不能静默换成合成数据。"""
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("QCTA_DATA_DIR")
            os.environ["QCTA_DATA_DIR"] = tmp
            try:
                bars = synthetic_bars("rb", "2020-01-01", "2020-06-30", get_spec("rb"))
                save_cache("rb", bars)
                b = load_bundle(["rb"], "2020-01-01", "2020-06-30", prefer_live=False, refresh=True)
                self.assertEqual(b.source, "cache", "refresh 不该把缓存换成 synthetic")
                self.assertEqual(len(b.bars_by_code["rb"]), len(bars))
            finally:
                if old is None:
                    os.environ.pop("QCTA_DATA_DIR", None)
                else:
                    os.environ["QCTA_DATA_DIR"] = old


if __name__ == "__main__":
    unittest.main()
