# 数学基底：概率论与统计学在这里各管什么

这份文档解释 `python/qcta/` 里那层"不产出交易信号、只判断结果可不可信"的模块。
它回答一个问题：**一次回测给出的夏普 1.5，凭什么说它是边缘而不是噪声？**

答案不是"多跑几年"，而是四条互相独立的数学检查：

| 问题 | 工具 | 模块 |
|---|---|---|
| 样本够不够？ | MinTRL（最小样本长度） | `significance.py` |
| 夏普是不是噪声？ | PSR + 块自助区间 | `significance.py` / `timeseries.py` |
| 我试了多少次？ | DSR（通缩夏普）+ BH 校正 | `significance.py` |
| 风险长什么样？ | VaR/CVaR + GARCH(1,1) | `risk.py` |

支撑它们的是三个基础设施：分布函数（`probability.py`，纯标准库实现的 Φ、t、χ²、Beta 及分位数）、
时间序列诊断（`timeseries.py`）、贝叶斯更新（`bayes.py`）。

**为什么不用 numpy/scipy**：引擎要在插件里跑、要能在没有科学计算栈的机器上装起来。
所有分布函数都是自己写的，精度用测试钉住（`tests/test_probability.py`，24 个用例对比已知临界值）。

---

## 1. 分布基底（`probability.py`）

| 函数 | 用途 | 精度口径 |
|---|---|---|
| `norm_cdf/pdf/ppf` | 正态概率与分位数 | ppf 用 Acklam 有理逼近 + 一步 Halley 精修，绝对误差 < 1e-15 |
| `t_cdf/ppf` | 小样本均值检验 | 由不完全 Beta 函数给出，与 t 表临界值对齐 |
| `chi2_cdf/ppf` | 方差比、Ljung-Box、GARCH 似然 | 正则化下不完全 Gamma |
| `beta_inc/beta_ppf` | 胜率的共轭后验 | 连分式展开（Lentz 算法） |
| `skewness/kurtosis` | 分布形状 | 样本矩（偏度未做无偏修正，与 PSR 公式口径一致） |

关键点：**这些不是装饰**。PSR 的分母、DSR 的基准夏普、VaR 的分位数全部依赖它们，
错一位小数就会把"不显著"判成"显著"。

---

## 2. 收益序列的统计模型（`timeseries.py`）

大部分回测指标默认**收益是独立同分布（iid）**。这个假设值得单独检验，因为它是夏普置信区间的地基。

### 2.1 三条独立性检验

1. **Ljung-Box**：`Q = n(n+2)Σ_{k=1}^{m} ρ̂_k²/(n−k)`，在 iid 下近似 `χ²(m)`。
   大样本下它对极弱自相关也会显著（实测：1415 天、一阶自相关 +0.008，p 值仍是 0.000），
   所以要**同时看一阶自相关的量级**再下结论。体检报告里就是这么写的。
2. **方差比（Lo-MacKinlay）**：`VR(q) = Var(q 期收益)/(q·Var(1 期收益))`。iid 时 = 1；
   `VR>1` 说明正自相关（动量），`<1` 说明均值回归。z 统计量做了异方差稳健修正。
3. **ADF 单位根**：检验权益序列是不是随机游走。拒绝单位根 → 有均值回归结构；
   不能拒绝 → 与"纯随机游走"不可区分（多数真实策略落在这里）。

### 2.2 Hurst 指数（DFA）

用去趋势波动分析而不是 R/S：对非平稳更稳健。

`F(s) ∝ s^H`，取 log-log 斜率。`H≈0.5` 随机游走；`H>0.55` 有持续性；`H<0.45` 均值回归。

**输入必须是收益的增量序列**（价格序列本身做 DFA 会得到接近 1 的假象，这是实现时踩过的坑）。

### 2.3 自相关对标准误的修正

如果收益有自相关，夏普的标准误会被低估。修正因子：

`SE_factor = sqrt(1 + 2Σ_{k=1}^{m}(1 - k/(m+1))ρ̂_k)`

同时 `ols()` 默认支持 **Newey-West HAC** 标准误（`hac_lags=L`），用于任何"残差可能自相关"的回归。

---

## 3. 夏普的抽样分布：PSR / DSR / MinTRL

### 3.1 PSR（概率性夏普，Bailey & López de Prado 2012）

单期夏普 `SR` 的估计误差不是对称的，且被偏度和肥尾放大：

```
PSR(SR*) = Φ( (SR − SR*)·√(n−1) / √(1 − γ₃·SR + ((γ₄−1)/4)·SR²) )
```

- `γ₃` 偏度、`γ₄` 超额峰度
- 分母 > 1 时（负偏、肥尾）同样的夏普置信度更低 —— 这正是它优于裸 t 检验的地方
- 期货策略普遍负偏 + 肥尾（实测 `γ₃≈0`、`γ₄≈4.5`），所以这个修正不是小数点级别的

实测（真实数据 40/20）：年化夏普 0.11 → **PSR 59.9%**，几乎等于抛硬币。

### 3.2 DSR（通缩夏普）

参数扫描挑出的最好那个，天然带选择偏差。N 次独立试验下最大夏普的期望：

```
SR* ≈ √V · [ (1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)) ]      γ = 欧拉常数 0.5772
```

然后 `DSR = PSR(SR*)`。`N=1` 时退化为 PSR。V 拿不到时用 `1/(n−1)`（单次试验夏普的近似方差）。

实测：**N=12 → DSR 7.8%**。也就是说，前面那轮参数扫描里最好的结果，
扣掉"我试了 12 次"这个事实后，几乎完全不可信。

### 3.3 MinTRL（最小样本长度）

由 PSR 反解：

```
n ≥ 1 + (1 − γ₃SR + ((γ₄−1)/4)SR²)·(z_α/SR)²
```

实测：夏普 0.11 要 **> 100 年**样本才能以 95% 置信声称 > 0。
这不是程序错误，这是"这个夏普本来就太小、永远证明不了"的正确表达。

---

## 4. 自助法（`block_bootstrap`）

百分位区间，但**必须用块自助**：按块重采样保留自相关结构，块长 `≈ n^(1/3)`（`block=None` 时自动取）。

- `bootstrap_sharpe_ci`：重采样收益 → 夏普区间
- `bootstrap_cagr_ci`：重采样收益 → 复利路径 → 年化区间

iid 自助在自相关序列上会给出**过窄**的区间（假精确），所以这里不用。

实测夏普区间 `[−0.77, +0.98]` —— 跨过 0，结论只能是"与噪声不可区分"。

---

## 5. 风险度量（`risk.py`）

### 5.1 VaR 三档对照

| 方法 | 公式 | 用途 |
|---|---|---|
| 历史 | `−quantile(r, 1−level)` | 无分布假设，但样本外尾部看不见 |
| 正态 | `−(μ + z_{1−level}·σ)` | 基准，通常低估 |
| Cornish-Fisher | `z_cf = z + (z²−1)γ₃/6 + (z³−3z)γ₄/24 − (2z³−5z)γ₃²/36` | 用偏度/峰度修正分位数 |

三档并列给出，是因为**它们不一致时本身就是信息**：CF 明显大于正态 → 尾部比正态厚。

`CVaR`（条件 VaR / 期望尾部损失）= 超过 VaR 那部分的均值，是比 VaR 更"可加"的风险度量。

### 5.2 GARCH(1,1)

```
σ²_t = ω + α·r²_{t−1} + β·σ²_{t−1}
```

极大似然 + 方差目标化（`ω` 用样本方差约束），`(α, β)` 在网格上搜索后用似然精修。
输出：当前波动、长期波动 `√(ω/(1−α−β))`、持续性 `α+β`。

**波动聚集是期货的常态**：`α+β ≈ 0.99` 意味着冲击衰减极慢，
所以"用一段固定历史估波动率"在结构上就是滞后的 —— 这也是仓位经常偏小的数学原因之一。

### 5.3 回撤结构

`drawdown_episodes` 给出每次回撤的峰、谷、深度、恢复天数、是否已恢复。
体检里报告：次数、最深、平均深度、最长水下天数。这比单一"最大回撤"更能说明**痛苦持续多久**。

---

## 6. 贝叶斯层（`bayes.py`）

### 6.1 Beta-Binomial：胜率

胜率 `p` 的先验 `Beta(a₀,b₀)`（默认 `Beta(1,1)` 均匀），观测到 `w` 胜 `l` 负后：

```
p | data ~ Beta(a₀+w, b₀+l)
P(p > 0.5 | data) = 1 − I_{0.5}(a₀+w, b₀+l)
```

为什么需要它：**40 笔交易里 22 胜（55%）根本不能说明有优势** ——
后验区间 `[43%, 57%]` 跨过 50%，`P(p>0.5)≈0.6`。用点估计做决策是这类错误的典型来源。

### 6.2 正态-逆伽马：均值与方差的联合后验

用于"样本均值/方差都不可靠"的场景（小样本、参数不稳定）。给出后验均值、自由度与尺度，
可直接反推均值的 t 分布预测区间。

---

## 7. 组合层：从协方差到权重（`optimizer.py`）

### 7.1 Ledoit-Wolf 恒等目标收缩

28 个品种、250 天窗口时，样本协方差矩阵接近奇异（N/T ≈ 0.11，但相关性高时有效秩更低），
直接求逆会让最小方差解爆掉。收缩：

```
Σ̃ = (1−δ)·S + δ·m·I,   m = tr(S)/N
δ* = π/γ  （截断到 [0,1]）
π = (1/T²)·Σ_t ‖x_t x_tᵀ − S‖²_F   （估计误差）
γ = ‖S − m·I‖²_F                    （偏离目标）
```

### 7.2 ERC（等风险贡献）

求解：

```
min_w  ½·wᵀΣw − Σ_i b_i·ln(w_i)
```

一阶条件 `w_i·(Σw)_i = b_i` 给出**不动点迭代**（坐标下降）：

```
w_i ← ( −c_i + √(c_i² + 4·Σ_ii·b_i) ) / (2·Σ_ii),   c_i = Σ_{j≠i} Σ_ij·w_j
```

实现里有一条容易踩的坑：**迭代内部不要每轮归一化**。固定点自带尺度，
归一化会把尺度一起压小、收敛变慢（实测残差停在 1e-2 量级）。收敛后最后归一一次。

**为什么默认用 ERC 而不是均值方差**：均值方差对预期收益的估计误差极其敏感
（误差放大 `Σ⁻¹` 倍），而期货的预期收益本来就估不准。ERC 只需要协方差，稳健得多。
`mean_variance` 仍然提供，但要自己给 `expected_returns` 和风险厌恶系数。

### 7.3 约束投影

单品种上限 `cap`、板块上限 `sector_cap` 用**迭代水位投影**（把超出部分按比例分给未触顶的品种），
而不是简单截断后归一 —— 截断会破坏风险贡献比例，投影不会。

---

## 8. 波动率目标化与"颗粒度陷阱"

每日期望波动预算：

```
daily_budget = equity · target_vol / √252
per_symbol  = daily_budget · w_i / √(Σ w_j²)
lots        = floor( per_symbol / (ATR_i · multiplier_i) )
```

**这里有一个数学上无法回避的问题**：`ATR·multiplier` 是单手的日风险，
它是离散的。当 `per_symbol < ATR·multiplier` 时，`floor` 得到 0 —— 这个品种直接不参与。

实测（100 万本金、10% 目标波动、28 品种）：只有 **6 个**品种曾真正建仓，
其余 22 个的每手风险（如 IF 约 14,150 元/日）远超它们的预算（约 1,192 元/日）。

两条路：加钱（让预算追上颗粒度），或放宽预算（`--recycle-budget` 把 0 手品种的预算分给能下单的）。
后者把资金利用率从 23% 抬到 88–100%，但**实测所有参数在样本外都亏** ——
结论是**不要把风险预算填满**，因为 2021–2026 上这份 alpha 本来就没有边缘。
详见 [SWEEP.md](SWEEP.md)。

---

## 9. 模块与接口

| 模块 | 主要入口 | 被谁用 |
|---|---|---|
| `probability.py` | `norm_*`, `t_*`, `chi2_*`, `beta_*`, `skewness`, `kurtosis` | 所有统计模块 |
| `timeseries.py` | `ols`, `acf`, `ljung_box`, `adf`, `variance_ratio`, `hurst_dfa`, `block_bootstrap` | `significance`, `verdict` |
| `significance.py` | `psr`, `deflated_sharpe`, `min_track_record_length`, `bootstrap_*_ci`, `t_test_mean`, `wilcoxon_signed_rank`, `benjamini_hochberg` | `verdict` |
| `risk.py` | `var_report`, `underwater_stats`, `garch11_fit` | `verdict` |
| `bayes.py` | `beta_binomial`, `prob_win_rate_above`, `normal_inverse_gamma` | `verdict` |
| `optimizer.py` | `optimize`, `risk_parity`, `shrink_cov` | `engine` |
| `verdict.py` | `analyze`, `render_markdown` | `report`, `cli stats` |

**产品接口**：

- 每次回测的 `report.md` 末尾自动带「## 统计体检」一节（`write_reports` 内部调用 `verdict.analyze`）
- `report.json` 的 `meta.verdict` 是同一份结构化数据
- 命令行：`python -m qcta stats --run <id> --trials 12`（`--write` 写入该 run 目录的 `stats.md`，`--json` 出结构化）

---

## 10. 这层数学不做什么

- **不产生交易信号**。它是裁判，不是选手。
- **不预测收益**。均值方差的 `expected_returns` 必须外部给，且默认不用。
- **不解决非稳态**。GARCH 能描述波动聚集，但结构突变（交易所改规则、品种生命周期结束）它看不见。
- **不替代样本外检验**。所有检验都是"在给定样本内能否被噪声解释"，
  真正的验证永远是样本外 + 时间推进（模拟盘 `qcta paper` 就是干这个的）。
- **不保证精度到小数点后很多位**。分布函数用测试钉住了合理误差，但这不是科研级数值库。

---

## 参考

- Bailey, D. & López de Prado, M. (2012). *The Sharpe Ratio Efficient Frontier*. Journal of Risk.
- Bailey, D. & López de Prado, M. (2014). *The Deflated Sharpe Ratio*. Journal of Portfolio Management.
- Ledoit, O. & Wolf, M. (2004). *A Well-Conditioned Estimator for Large-Dimensional Covariance Matrices*.
- Lo, A. (2002). *The Statistics of Sharpe Ratios*. Financial Analysts Journal.
- Lo, A. & MacKinlay, A. (1988). *Stock Market Prices Do Not Follow Random Walks*.
- Maillard, S., Roncalli, T., Teïletche, J. (2010). *The Properties of Equally Weighted Risk Contribution Portfolios*.
- Bollerslev, T. (1986). *Generalized Autoregressive Conditional Heteroskedasticity*.
- Peng, C.-K. et al. (1994). *Mosaic Organization of DNA Nucleotides*（DFA 原始文献）.
