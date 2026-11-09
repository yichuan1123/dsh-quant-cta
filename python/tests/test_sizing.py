import unittest

from qcta.sizing import cap_by_leverage, lots_for_risk, normalize_weights
from qcta.specs import get_spec


class TestSizing(unittest.TestCase):
    def test_equal_weights_normalize_by_l2(self):
        w = normalize_weights([1.0, 1.0, 1.0])
        self.assertAlmostEqual(sum(x * x for x in w), 1.0, places=9)
        # 零相关近似下，等权 n 个品种的组合风险 = 预算
        for x in w:
            self.assertAlmostEqual(x, 1.0 / 3 ** 0.5, places=9)

    def test_lots_for_risk_is_floor(self):
        rb = get_spec("rb")           # 10 吨/手
        # ATR=80 元/吨 → 每手日风险 800 元；预算 2000 → 2 手（向下取整）
        self.assertEqual(lots_for_risk(rb, 1_000_000, 80.0, 2000.0, 0.0), 2)
        self.assertEqual(lots_for_risk(rb, 1_000_000, 80.0, 799.0, 0.0), 0)

    def test_symbol_risk_cap_applies(self):
        rb = get_spec("rb")
        # 权益 10 万，单品种风险上限 2% = 2000 元；ATR80×10=800 → 最多 2 手
        self.assertEqual(lots_for_risk(rb, 100_000, 80.0, 99_999.0, 0.02), 2)

    def test_zero_atr_gives_zero_lots(self):
        self.assertEqual(lots_for_risk(get_spec("rb"), 1e6, 0.0, 1000.0, 0.0), 0)

    def test_leverage_cap_scales_down(self):
        rb = get_spec("rb")
        desired = {"rb": (1, 100)}
        prices = {"rb": 3600.0}
        out = cap_by_leverage(desired, prices, {"rb": rb}, 100_000, 1.0)
        # 100 手 × 3600 × 10 = 360 万，权益 10 万，上限 10 万 → 缩到 2 手
        self.assertEqual(out["rb"][1], 2)

    def test_leverage_cap_noop_when_under_limit(self):
        rb = get_spec("rb")
        out = cap_by_leverage({"rb": (1, 1)}, {"rb": 3600.0}, {"rb": rb}, 1_000_000, 2.0)
        self.assertEqual(out["rb"][1], 1)


if __name__ == "__main__":
    unittest.main()
