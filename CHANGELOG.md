# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-11-09

### 新增

- 回测引擎：t 收盘决策 / t+1 开盘成交的事件顺序，逐日盯市，期末强制平仓
- 期货会计口径账本（开仓不占现金、平仓结转浮盈、保证金校验），手续费精确摊分到平仓交易
- 合约规格表：28 个品种的乘数 / 跳价 / 保证金率 / 手续费（近似值，带 verified 标记）
- 策略：唐奇安通道突破（止损反手）、横截面动量
- 仓位换算：ATR 波动率预算 + `sqrt(Σw²)` 归一 + 单品种风险上限 + 杠杆上限
- 风控：组合回撤熔断、ATR 止损（可选）、保证金不足拒单记录
- 数据层：天勤 TqSdk 主连日线（复权）/ CSV 缓存 / 确定性合成数据，来源在报告中显式标注
- 绩效：CAGR、年化波动、夏普、索提诺、最大回撤、卡玛、盈亏比、胜率、换手、月度矩阵、分策略与分品种归因
- 报告：Markdown + JSON + 手写 SVG 曲线
- 模拟盘：可断点的逐日推进账本 + 前瞻日志（幂等）
- DSH 插件：`qcta_doctor` / `qcta_data` / `qcta_backtest` / `qcta_report` / `qcta_paper` 五个工具
- Web 面板：`/dsh-quant-cta/panel`（净值曲线、指标卡、策略腿与品种归因、模拟盘持仓）
- 35 个单元测试，含「期末权益 − 初始资金 == Σ交易净盈亏」恒等式、分腿与总权益一致、以及「信号在 t+1 开盘成交」的反未来函数测试
- `tools/verify_invariants.py`：对任意一份报告跑三条账本恒等式核验
- `tools/smoke.mjs`：用假 ctx 装配插件、调真引擎的冒烟测试
- GitHub Actions CI（Python 单测 + 回测 + 恒等式 + 插件语法 + 冒烟）
