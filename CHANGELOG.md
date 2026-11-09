# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-11-09

### 新增

- **客户端半身** `client/client.js`：在右侧栏「开始」列表注册「量化 CTA 工作台」入口，
  点开是一个 tab（同源 iframe 指向 `/dsh-quant-cta/panel`）。之前只有宿主端，
  所以在「开始」菜单里看不到这个插件
- `tools/smoke-client.mjs`：在 vm 沙箱里跑 client bundle，校验注册面（tab 类型 / guide 入口 /
  slot 内容）与渲染结果（iframe 指向面板），不需要浏览器与真 react

### 变更

- manifest 增加 `dsh.client.platform: web` 与 `exports["./client"]`
- 面板入口两条：开始菜单里的「量化 CTA 工作台」与 `/dsh-quant-cta/panel` 直连

## [0.2.1] - 2026-11-09

### 安全加固

- 凭据端点（GET/POST/DELETE `/dsh-quant-cta/api/credentials` 与 `/test`）增加来源校验：
  只接受本机回环 Host，带 Origin 时必须是本机同源。防 DNS rebinding 与跨站表单把账号密码写进本机文件。
  冒烟测试补了一条「跨源写请求必须被拒」的断言
- 连通性探测的 `wait_update` 改为带 1 秒 deadline 轮询：原先可能长时间阻塞，把 UI 请求拖死

## [0.2.0] - 2026-11-09

### 新增

- **面板「数据源账号」卡片**：填快期账号密码 → 保存并真连一次天勤验证（回显 KQ.m@SHFE.rb 最新价）。
  密码只写本机 credentials.json（gitignore 覆盖），页面永不回显，保存后输入框清空
- qcta auth 子命令：status / login / test / logout。**密码只从 stdin 读**，不进 argv、不进进程列表
- HTTP 路由：GET/POST/DELETE /dsh-quant-cta/api/credentials、POST /dsh-quant-cta/api/credentials/test（只测不存）
- 凭据文件位置支持 QCTA_CRED_PATH 覆盖（测试与多账户场景）
- 冒烟测试升级为**真 HTTP 服务**：把注册的路由逐个打一遍，并断言「响应体里既没有明文密码、也没有完整账号」

### 变更

- 账号配置的推荐路径从「环境变量/手写 json」改为「面板表单」，命令行方式保留
- 单测 35 → 43（新增 8 条凭据存储与脱敏测试）

## [0.1.1] - 2026-11-09

### 修复

- `--refresh` 不带 `--live` 时，即使已有真实行情缓存也会静默降级成合成数据。现在有缓存必用缓存，并加了回归测试
- 分腿归因的期末权益与总权益口径不一致（差的就是强平成本）。`run()` 新增 `liquidate` 开关，强平后同步各腿曲线
- paper 的指标与日志权益口径不一致：改为 `liquidate=False` 的持仓库口径，与模拟盘账户的真实状态一致
- `qcta_report action="get"` 描述写「打印完整 markdown」，实现只给摘要。现在读回 `report.md` 全文（超 12k 字符截断并提示路径）
- CI 里回测产物路径写错（`python/runs` → `runs`），会让流水线恒红
- 清理未使用的导入

### 新增

- `tools/verify_invariants.py`：对任意报告核验三条账本恒等式
- `tools/smoke.mjs`：用假 ctx 装配插件、打真引擎的冒烟测试
- GitHub Actions CI（单测 + 端到端回测 + 恒等式 + 插件语法 + 冒烟）
- 单测 32 → 35（新增分腿一致性、未平仓保留、refresh 不降级三条）

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
