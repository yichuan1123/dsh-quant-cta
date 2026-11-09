import unittest

from qcta.specs import DEFAULT_POOL, SPECS, get_spec, resolve_pool


class TestSpecs(unittest.TestCase):
    def test_tick_value_is_multiplier_times_tick(self):
        for code, spec in SPECS.items():
            with self.subTest(code=code):
                self.assertAlmostEqual(spec.tick_value, spec.multiplier * spec.tick, places=9)

    def test_every_pool_symbol_exists(self):
        for code in DEFAULT_POOL:
            self.assertIn(code, SPECS)

    def test_commission_uses_max_of_rate_and_per_lot(self):
        rb = get_spec("rb")
        # rb: 万1 按额 + 0 固定费
        self.assertAlmostEqual(rb.commission(1, 3600.0), 0.0001 * 3600 * 10, places=6)
        m = get_spec("m")
        # 豆粕：按额算出来接近 0，固定费 1.5/手 生效
        self.assertAlmostEqual(m.commission(2, 3000.0), 3.0, places=6)

    def test_margin_scales_with_lots_and_buffer(self):
        rb = get_spec("rb")
        one = rb.margin(1, 3600.0)
        two = rb.margin(2, 3600.0)
        self.assertAlmostEqual(two, one * 2, places=6)
        self.assertGreater(one, 3600 * 10 * 0.09)

    def test_resolve_pool(self):
        self.assertEqual(resolve_pool("starter"), ["rb", "i", "cu", "m", "y", "TA"])
        self.assertEqual(resolve_pool("rb,cu"), ["rb", "cu"])
        self.assertIn("rb", resolve_pool("default"))

    def test_tq_symbol_format(self):
        self.assertEqual(get_spec("rb").tq_symbol(), "KQ.m@SHFE.rb")
        self.assertEqual(get_spec("m").tq_symbol(), "KQ.m@DCE.m")


if __name__ == "__main__":
    unittest.main()
