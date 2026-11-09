"""概率分布与稳健统计的已知值测试。

自己实现分布函数的风险是「看起来对、其实偏了」。所以这里全部对照权威数值：
正态 1.96→0.975、t(10) 0.975→2.228139、χ²(3) 0.95→7.814728、
Beta(2,3) 在 0.5 处 CDF 恰好 0.6875（可手算）。
"""

import math
import unittest

from qcta import probability as P


class TestNormal(unittest.TestCase):
    def test_cdf_known_points(self):
        self.assertAlmostEqual(P.norm_cdf(0.0), 0.5, places=12)
        self.assertAlmostEqual(P.norm_cdf(1.96), 0.9750021049, places=8)
        self.assertAlmostEqual(P.norm_cdf(-1.96), 0.0249978951, places=8)
        self.assertAlmostEqual(P.norm_cdf(1.0), 0.8413447461, places=8)

    def test_ppf_known_values(self):
        self.assertAlmostEqual(P.norm_ppf(0.975), 1.959963985, places=8)
        self.assertAlmostEqual(P.norm_ppf(0.5), 0.0, places=12)
        self.assertAlmostEqual(P.norm_ppf(0.99), 2.326347874, places=8)
        self.assertAlmostEqual(P.norm_ppf(0.025), -1.959963985, places=8)

    def test_ppf_cdf_roundtrip(self):
        for p in (0.001, 0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99, 0.999):
            self.assertAlmostEqual(P.norm_cdf(P.norm_ppf(p)), p, places=7)

    def test_pdf_integrates_to_one(self):
        # 粗略数值积分（±8σ，梯形法）
        n = 2000
        lo, hi = -8.0, 8.0
        h = (hi - lo) / n
        area = 0.5 * (P.norm_pdf(lo) + P.norm_pdf(hi))
        for i in range(1, n):
            area += P.norm_pdf(lo + i * h)
        area *= h
        self.assertAlmostEqual(area, 1.0, places=6)

    def test_extreme_p_returns_inf(self):
        # 分位数的定义域端点：p=0/1 → ∓inf（VaR 在极端置信度下不该抛异常）
        self.assertEqual(P.norm_ppf(0.0), float("-inf"))
        self.assertEqual(P.norm_ppf(1.0), float("inf"))

    def test_out_of_range_p_raises(self):
        with self.assertRaises(ValueError):
            P.norm_ppf(-0.1)
        with self.assertRaises(ValueError):
            P.norm_ppf(1.1)


class TestChi2(unittest.TestCase):
    def test_ppf_known_values(self):
        self.assertAlmostEqual(P.chi2_ppf(0.95, 3), 7.814728, places=4)
        self.assertAlmostEqual(P.chi2_ppf(0.95, 1), 3.841459, places=4)
        self.assertAlmostEqual(P.chi2_ppf(0.99, 5), 15.086272, places=3)

    def test_cdf_roundtrip(self):
        for df in (1, 3, 5, 10):
            x = P.chi2_ppf(0.9, df)
            self.assertAlmostEqual(P.chi2_cdf(x, df), 0.9, places=6)

    def test_chi2_1_is_normal_square(self):
        # χ²(1) 的 95 分位 = 1.96²
        self.assertAlmostEqual(P.chi2_ppf(0.95, 1), P.norm_ppf(0.975) ** 2, places=4)


class TestStudentT(unittest.TestCase):
    def test_ppf_known_values(self):
        self.assertAlmostEqual(P.t_ppf(0.975, 10), 2.228139, places=5)
        self.assertAlmostEqual(P.t_ppf(0.975, 1), 12.706205, places=4)
        self.assertAlmostEqual(P.t_ppf(0.95, 30), 1.697261, places=4)

    def test_converges_to_normal(self):
        self.assertAlmostEqual(P.t_ppf(0.975, 1_000_000), 1.959964, places=4)

    def test_symmetry(self):
        self.assertAlmostEqual(P.t_ppf(0.1, 7), -P.t_ppf(0.9, 7), places=9)

    def test_two_sided_p(self):
        self.assertAlmostEqual(P.t_two_sided_p(2.228139, 10), 0.05, places=4)
        self.assertAlmostEqual(P.t_two_sided_p(0.0, 10), 1.0, places=9)


class TestBeta(unittest.TestCase):
    def test_beta_inc_exact_value(self):
        # Beta(2,3) 的 CDF 在 0.5 处 = 12∫₀^0.5 x(1-x)² dx = 0.6875（可手算）
        self.assertAlmostEqual(P.beta_inc(2, 3, 0.5), 0.6875, places=10)

    def test_beta_inc_bounds(self):
        self.assertEqual(P.beta_inc(2, 3, 0.0), 0.0)
        self.assertEqual(P.beta_inc(2, 3, 1.0), 1.0)

    def test_beta_ppf_roundtrip(self):
        for a, b in ((2, 3), (0.5, 0.5), (5, 2)):
            x = P.beta_ppf(0.3, a, b)
            self.assertAlmostEqual(P.beta_inc(a, b, x), 0.3, places=6)


class TestRobustStats(unittest.TestCase):
    def test_median(self):
        self.assertAlmostEqual(P.median([3, 1, 2]), 2.0)
        self.assertAlmostEqual(P.median([4, 1, 3, 2]), 2.5)
        self.assertEqual(P.median([]), 0.0)

    def test_quantile_linear(self):
        xs = [1, 2, 3, 4]
        self.assertAlmostEqual(P.quantile(xs, 0.5), 2.5)
        self.assertAlmostEqual(P.quantile(xs, 0.0), 1.0)
        self.assertAlmostEqual(P.quantile(xs, 1.0), 4.0)
        self.assertAlmostEqual(P.quantile(xs, 0.25), 1.75)

    def test_mad_ignores_outlier(self):
        base = [1, 2, 3, 4, 5]
        with_outlier = base + [1000]
        self.assertLess(P.mad(with_outlier), 3.0, "MAD 不应被单个极端值带飞")
        self.assertGreater(P.std(with_outlier), 300.0, "标准差会被带飞（这就是为什么要 MAD）")

    def test_trimmed_mean_resists_outlier(self):
        xs = [1, 2, 3, 4, 5, 1000]
        self.assertLess(P.trimmed_mean(xs, 0.2), 10.0)
        self.assertGreater(P.mean(xs), 160.0)

    def test_winsorize_bounds(self):
        xs = list(range(100)) + [10_000]
        w = P.winsorize(xs, 0.05)
        self.assertEqual(len(w), len(xs))
        self.assertLess(max(w), 10_000)
        self.assertGreaterEqual(min(w), P.quantile(xs, 0.05))

    def test_skewness_zero_for_symmetric(self):
        self.assertAlmostEqual(P.skewness([-2, -1, 0, 1, 2]), 0.0, places=9)
        self.assertGreater(P.skewness([1, 1, 1, 1, 10]), 1.0)

    def test_kurtosis_normal_like(self):
        import random
        rng = random.Random(42)
        sample = [rng.gauss(0, 1) for _ in range(20000)]
        self.assertAlmostEqual(P.kurtosis(sample), 0.0, delta=0.15)

    def test_variance_ddof(self):
        self.assertAlmostEqual(P.variance([1, 2, 3], ddof=1), 1.0)
        self.assertAlmostEqual(P.variance([1, 2, 3], ddof=0), 2.0 / 3.0)


if __name__ == "__main__":
    unittest.main()
