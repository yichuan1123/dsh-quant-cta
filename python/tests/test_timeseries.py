"""时间序列工具的测试（构造数据 + 已知解析值）。

核心要验证的是「能不能区分随机游走与均值回归」——这正是量化里最常见的误判来源。
"""

import math
import random
import unittest

from qcta import timeseries as T


def random_walk(n=600, seed=1):
    rng = random.Random(seed)
    out, x = [], 0.0
    for _ in range(n):
        x += rng.gauss(0.0, 1.0)
        out.append(x)
    return out


def ar1(phi, n=600, seed=2):
    rng = random.Random(seed)
    out, x = [], 0.0
    for _ in range(n):
        x = phi * x + rng.gauss(0.0, 1.0)
        out.append(x)
    return out


class TestLinearAlgebra(unittest.TestCase):
    def test_solve_2x2(self):
        x = T.solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])
        self.assertAlmostEqual(2.0 * x[0] + x[1], 5.0, places=10)
        self.assertAlmostEqual(x[0] + 3.0 * x[1], 10.0, places=10)

    def test_inverse_identity(self):
        A = [[4.0, 1.0], [2.0, 3.0]]
        inv = T.inverse(A)
        prod = [[sum(A[i][k] * inv[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
        self.assertAlmostEqual(prod[0][0], 1.0, places=10)
        self.assertAlmostEqual(prod[0][1], 0.0, places=10)

    def test_singular_raises(self):
        with self.assertRaises(ValueError):
            T.solve([[1.0, 1.0], [1.0, 1.0]], [1.0, 2.0])


class TestOLS(unittest.TestCase):
    def test_exact_fit(self):
        xs = [float(i) for i in range(20)]
        y = [2.0 + 3.0 * x for x in xs]
        res = T.ols(y, [[1.0, x] for x in xs])
        self.assertAlmostEqual(res["params"][0], 2.0, places=8)
        self.assertAlmostEqual(res["params"][1], 3.0, places=8)
        self.assertAlmostEqual(res["r2"], 1.0, places=10)

    def test_coefficient_recovers_true_beta(self):
        rng = random.Random(7)
        xs = [rng.gauss(0, 1) for _ in range(500)]
        y = [1.5 + 0.8 * x + rng.gauss(0, 1) for x in xs]
        res = T.ols(y, [[1.0, x] for x in xs])
        self.assertAlmostEqual(res["params"][1], 0.8, delta=0.15)

    def test_hac_inflates_se_for_autocorrelated_residuals(self):
        # 残差强正自相关时，HAC 标准误必须大于普通标准误（否则 t 值虚高）
        rng = random.Random(11)
        xs = [float(i) for i in range(400)]
        errs, e = [], 0.0
        for _ in xs:
            e = 0.9 * e + rng.gauss(0, 1)
            errs.append(e)
        y = [0.5 * x + err for x, err in zip(xs, errs)]
        X = [[1.0, x] for x in xs]
        plain = T.ols(y, X)
        hac = T.ols(y, X, hac_lags=10)
        self.assertGreater(hac["se"][1], plain["se"][1] * 1.5)
        self.assertLess(abs(hac["tstats"][1]), abs(plain["tstats"][1]))


class TestAutocorrelation(unittest.TestCase):
    def test_acf_known_value(self):
        # [1,2,3,4,5]：均值 3，分母 Σ(x-3)² = 10，lag1 分子 = 2+0+0+2 = 4 → 0.4
        self.assertAlmostEqual(T.acf([1, 2, 3, 4, 5], 1)[0], 0.4, places=12)

    def test_pacf_first_equals_acf_first(self):
        xs = ar1(0.6, 300)
        self.assertAlmostEqual(T.pacf(xs, 3)[0], T.acf(xs, 1)[0], places=9)

    def test_ljung_box_white_noise(self):
        rng = random.Random(3)
        xs = [rng.gauss(0, 1) for _ in range(500)]
        self.assertGreater(T.ljung_box(xs, 10)["p"], 0.05, "白噪声不应被拒绝")

    def test_ljung_box_ar1_rejected(self):
        xs = ar1(0.7, 500)
        self.assertLess(T.ljung_box(xs, 10)["p"], 0.001, "强自相关必须被拒绝")

    def test_autocorr_se_factor(self):
        rng = random.Random(5)
        iid = [rng.gauss(0, 1) for _ in range(800)]
        self.assertLess(T.autocorr_se_factor(iid), 1.4, "独立样本放大系数应接近 1")
        self.assertGreater(T.autocorr_se_factor(ar1(0.8, 800)), 1.8, "强正自相关应显著放大")


class TestUnitRoot(unittest.TestCase):
    def test_adf_does_not_reject_random_walk(self):
        res = T.adf(random_walk(600, seed=1))
        self.assertGreater(res["stat"], res["crit"]["5%"], "随机游走不该被拒绝")
        self.assertFalse(res["reject"])

    def test_adf_rejects_stationary(self):
        res = T.adf(ar1(0.4, 600, seed=4))
        self.assertLess(res["stat"], res["crit"]["5%"], "平稳序列应被拒绝单位根")
        self.assertTrue(res["reject"])

    def test_adf_short_series_safe(self):
        res = T.adf([1.0, 2.0, 3.0])
        self.assertFalse(res["reject"])


class TestVarianceRatio(unittest.TestCase):
    def test_random_walk_vr_near_one(self):
        returns = [random_walk(600, seed=9)[i] - random_walk(600, seed=9)[i - 1] for i in range(1, 600)]
        res = T.variance_ratio(returns, 5)
        self.assertAlmostEqual(res["vr"], 1.0, delta=0.6)

    def test_positive_autocorr_gives_vr_above_one(self):
        rng = random.Random(13)
        r, prev = [], 0.0
        for _ in range(600):
            prev = 0.5 * prev + rng.gauss(0, 0.01)
            r.append(prev)
        res = T.variance_ratio(r, 5)
        self.assertGreater(res["vr"], 1.0)


class TestHurst(unittest.TestCase):
    def test_iid_increments_near_half(self):
        """输入是增量（收益）：独立同分布 → H ≈ 0.5。"""
        rng = random.Random(21)
        increments = [rng.gauss(0, 1) for _ in range(2000)]
        self.assertAlmostEqual(T.hurst_dfa(increments), 0.5, delta=0.12)

    def test_persistent_increments_above_half(self):
        """增量本身正自相关（动量）→ H > 0.5。"""
        h = T.hurst_dfa(ar1(0.6, 2000, seed=23))
        self.assertGreater(h, 0.6, "正自相关增量应给出 H > 0.6（实际 %.3f）" % h)

    def test_mean_reverting_increments_below_half(self):
        """增量负自相关（均值回归）→ H < 0.5。"""
        rng = random.Random(25)
        xs, prev = [], 0.0
        for _ in range(2000):
            prev = -0.6 * prev + rng.gauss(0, 1)
            xs.append(prev)
        self.assertLess(T.hurst_dfa(xs), 0.45)

    def test_short_series_default(self):
        self.assertEqual(T.hurst_dfa([1.0, 2.0, 3.0]), 0.5)


class TestBootstrap(unittest.TestCase):
    def test_block_bootstrap_shape(self):
        xs = [float(i) for i in range(100)]
        samples = T.block_bootstrap(xs, 20, block=5, seed=1)
        self.assertEqual(len(samples), 20)
        for s in samples:
            self.assertEqual(len(s), 100)
            # 块均值本身方差很大（块长 5、样本 20 块），这里只验证量级没跑偏
            self.assertGreater(sum(s) / len(s), 20.0)
            self.assertLess(sum(s) / len(s), 80.0)

    def test_block_bootstrap_preserves_mean_on_random_data(self):
        rng = random.Random(31)
        xs = [rng.gauss(0, 1) for _ in range(400)]
        samples = T.block_bootstrap(xs, 200, block=7, seed=3)
        means = [sum(s) / len(s) for s in samples]
        self.assertAlmostEqual(sum(means) / len(means), 0.0, delta=0.35)

    def test_block_bootstrap_reproducible(self):
        xs = list(range(50))
        a = T.block_bootstrap(xs, 3, seed=7)
        b = T.block_bootstrap(xs, 3, seed=7)
        self.assertEqual(a, b)


class AdfGuardTests(unittest.TestCase):
    """短样本：ADF 的增广项会把自由度吃光导致回归矩阵奇异，必须降级而不是崩。"""

    def test_short_series_flagged_not_crashed(self):
        y = [1.0 + 0.01 * i for i in range(22)]
        r = T.adf(y, max_lag=5)
        self.assertTrue(r.get("insufficient"))
        self.assertFalse(r["reject"])

    def test_flat_then_jump_does_not_crash(self):
        y = [100.0] * 15 + [100.0 + i for i in range(1, 40)]
        r = T.adf(y, max_lag=5)
        self.assertIn("stat", r)


if __name__ == "__main__":
    unittest.main()
