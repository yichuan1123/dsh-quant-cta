# dsh-quant-cta

商品期货 CTA 的**研究 → 回测 → 模拟盘**全链路工作台，以 DSH 插件形式提供工具与面板，引擎用 Python 实现，行情来自天勤 TqSdk（真实）或确定性合成数据（无凭据时兜底并显式标注）。

**本程序不发任何真实委托**：没有 CTP 下单路径，paper 模式只写内部账本。

## 它做了什么

一条完整的交易流程，每一环都在代码里，不是示意：

| 环节 | 实现位置 | 说明 |
|---|---|---|
| 行情获取 | [python/qcta/data.py](python/qcta/data.py) | 天勤主连日线（复权）/ 本地 CSV 缓存 / 确定性合成数据 |
| 合约规格 | [python/qcta/specs.py](python/qcta/specs.py) | 28 个品种的乘数、跳价、保证金率、手续费 |
| 策略信号 | [python/qcta/strategies/](python/qcta/strategies) | 唐奇安突破、横截面动量 |
| 仓位换算 | [python/qcta/sizing.py](python/qcta/sizing.py) | ATR 波动率预算 → 手数，按 sqrt(Σw²) 归一 |
| 撮合与账本 | [python/qcta/account.py](python/qcta/account.py) | 期货会计口径、手续费精确摊分、滑点、保证金校验 |
| 事件循环 | [python/qcta/engine.py](python/qcta/engine.py) | t 收盘决策 → t+1 开盘成交，逐日盯市、熔断、止损 |
| 绩效归因 | [python/qcta/metrics.py](python/qcta/metrics.py) | CAGR/夏普/回撤/卡玛/PF/月度矩阵/分策略/分品种 |
| 报告输出 | [python/qcta/report.py](python/qcta/report.py) | Markdown + JSON + 手写 SVG 曲线 |
| 模拟盘 | [python/qcta/paper.py](python/qcta/paper.py) | 逐日推进的可断点账本 + 前瞻日志 |
| DSH 工具 | [lib/index.js](lib/index.js) | qcta_doctor / qcta_data / qcta_backtest / qcta_report / qcta_paper / qcta_sim |
| 最优配比 | [optimizer.py](python/qcta/optimizer.py) | 等权 / 波动率倒数 / ERC 风险平价 / 最小方差 / 均值方差（带 Ledoit-Wolf 收缩） |
| 滚动模型 | [model.py](python/qcta/model.py) | 调仓日重估权重（walk-forward），状态落盘可追溯 |
| 模拟调度 | [lib/index.js](lib/index.js) | DSH 按间隔定时推进 paper 账本，状态回流工具与面板 |
| Web 面板 | [panel.html](panel.html) | /dsh-quant-cta/panel |
| 客户端半身 | [client/client.js](client/client.js) | 在「开始」列表注册入口 + 右侧栏 tab（同源 iframe 套面板） |

## 安装

### 1. 装成本地 DSH 插件

**本地目录安装（已验证可用，不动 pnpm 锁文件）**：

```bash
# 1) 把插件目录联接进 profile 的 node_modules
powershell -NoProfile -Command "New-Item -ItemType Junction -Path '<profile>\node_modules\dsh-quant-cta' -Target '<repo>'"

# 2) 在 profile 的 cordis.patch.yml 末尾追加（config 里写本机 Python 路径）
#    - insert:
#        - id: dsh-quant-cta
#          name: dsh-quant-cta
#          config:
#            pythonPath: <repo>/.venv/Scripts/python.exe
#            engineRoot: <repo>

# 3) 重启 DSH
```

其中 `<profile>` 一般是 `%USERPROFILE%\.dsh\profiles\desktop`。

**发布到 npm 后**也可以用 `dsh plugin add dsh-quant-cta`（插件自带 `cordis.patch.yml`，
会把自己插进插件名单；本机 Python 路径仍需在 profile 的 patch 层给出）。

### 2. 装 Python 依赖

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pandas tqsdk
```

插件按以下顺序找 Python：`config.pythonPath` → `QCTA_PYTHON` → `<插件目录>/.venv` → `PATH` 里的 `python`。

### 3. 配天勤账号（可选，但强烈建议）

**推荐：在面板里填** —— 打开 `/dsh-quant-cta/panel`，找到「数据源账号」卡片，
填快期账号（手机号）与密码，点「保存并测试」，它会立刻真连一次天勤并回显
`KQ.m@SHFE.rb` 的最新价。密码只写到本机 `credentials.json`（已 gitignore），
页面永不回显，保存后输入框自动清空。

命令行等价物（密码走 stdin，不进 argv）：

```bash
echo '{"user":"手机号","password":"密码"}' | python -m qcta auth login --stdin --verify
python -m qcta auth status     # 只看脱敏账号
python -m qcta auth logout     # 删除本机凭据
```

也支持环境变量 `QCTA_TQ_USER` / `QCTA_TQ_PASS`（优先级高于文件），
或直接写 `credentials.json`。凭据文件位置可用 `QCTA_CRED_PATH` 覆盖。

没有凭据时引擎仍能跑，但只用合成数据，报告顶部有醒目标注。

**凭据怎么存**：Windows 上用 **DPAPI** 加密（`CryptProtectData`），密文只能被
**同一台机器上的同一个用户**解开——文件被拷到别处也用不了。面板会显示当前是
「DPAPI 加密」还是「明文存储」；老版本写下的明文文件仍能读，重存一次即可升级。
账号会明文返回给面板用于回填输入框（仅本机回环 + 同源），**密码永远不出现在任何返回值里**。

## 用法

### 在对话里（最省事）

```
qcta_doctor                                   # 先自检
qcta_sim    action="status"                    # 模拟调度 + 最新最优配比
qcta_sim    action="advance"                   # 推进一天并重算配比
qcta_data   action="sync" live=true           # 拉真实行情
qcta_backtest pool="default" start="2019-01-01" live=true
qcta_report action="get"
qcta_paper  action="init"; qcta_paper action="step"
```

面板里三张图（纯 SVG 手写，无图表库）：

| 图 | 内容 | 动态与约束 |
|---|---|---|
| 动态折线图 | 权益曲线 + 回撤曲线（双图） | 描边动效、悬停十字线读数、极值/末值标记、**期望回报路径**叠加、回撤图的**你的风险阈值**参考线、全部/近3年/近1年 区间切换 |
| 环形饼图 | 当前最优配比的权重分布 | 每品种固定配色（会话内缓存 + 黄金角）、顶格品种黄边告警、环下标注上限 |
| 分时图 | 逐日收益柱（正绿负红）+ 累计线 | 累计线走**独立右轴刻度**、**20 日滚动年化波动率**副轴、最差单日定位，切换「逐日收益 / 权益走势」两个视图 |

**资金与预期**：面板顶部可以填「模拟投入金额 / 期望年化回报 / 风险阈值（最大可接受回撤）」，
存在本机 localStorage。它不改历史回测，只做确定性换算与告警：期望期末权益、同金额实际期末、
年化差、回撤是否越过阈值；阈值比引擎熔断线更严时会提示把 `dd_halt` 调下来。
长序列用 LTTB 降采样（保峰谷形状）后再画，2000 个交易日也不会糊成一团。

约束是可视化出来的，不只是文字：单品种上限画成环形图下的标注与顶格描边、板块上限画成条形图的虚线、
熔断阈值画成回撤图的红色虚线。

面板有两条入口，指向同一个页面：

- **右侧栏「开始」列表**里的「量化 CTA 工作台」（客户端插件注册的入口，点开就是 tab）
- 图表全部可交互：**滚轮缩放 · 拖拽平移 · 双击复位**，悬停看当日数值
  （策略腿/品种归因是累计净盈亏折线，不是静态表）
- 直接访问 `http://127.0.0.1:58080/dsh-quant-cta/panel`

面板背后的只读/管理路由（全部同源，无鉴权，只在本机回环地址上可用）：

| 路由 | 作用 |
|---|---|
| `GET /dsh-quant-cta/api/overview` | 回测清单 + 最近一次报告 + 模拟盘状态 |
| `GET /dsh-quant-cta/api/report?run=<id>` | 单次回测的完整 JSON |
| `GET /dsh-quant-cta/api/credentials` | 凭据状态（只回脱敏账号） |
| `POST /dsh-quant-cta/api/credentials` | 保存账号密码（并可选验证连通） |
| `DELETE /dsh-quant-cta/api/credentials` | 删除本机凭据 |
| `POST /dsh-quant-cta/api/credentials/test` | 只测连通、不保存 |
| `GET /dsh-quant-cta/api/sim` | 模拟调度状态 + 最新模型权重 |
| `POST /dsh-quant-cta/api/sim` | 推进一次 / 启动定时 / 停止 |
| `GET /dsh-quant-cta/panel` | 面板页面 |

### 命令行

```bash
cd python
python -m qcta doctor
python -m qcta data sync --live --pool default
python -m qcta backtest --pool default --start 2019-01-01 --json
python -m qcta backtest --pool starter --sleeves trend:0.6,xsec:0.4 --vol 0.10
python -m qcta runs
python -m qcta report --run 20260101_120000_trend06-xsec04
python -m qcta stats --run 20260101_120000_trend06-xsec04 --trials 12   # 统计体检
python -m qcta backtest --pool all --slip auto --adx 18 --start 2016-01-05   # 逐品种滑点 + 强度过滤
python -m qcta paper init --pool starter --capital 300000
python -m qcta paper step --live
```

## 回测口径（写死的，不是可选项）

1. **信号 t 日收盘产生，t+1 日开盘成交** —— 防未来函数的核心。用当日收盘价成交会系统性高估收益。
2. **手数按波动率预算换算**，等权时按 `sqrt(Σw²)` 归一，使零相关近似下组合风险 ≈ 目标波动。
3. **成本**：手续费按合约规格（按额/按手取大，平今倍率），滑点默认 1 跳/笔。
4. **保证金校验**：开仓前检查可用资金，不足则按可负担手数缩减并记拒单。
5. **风控**：单品种风险上限、名义杠杆上限、组合回撤熔断、ATR 止损（默认关闭）。
6. **期末强制平仓**，保证 `期末权益 − 初始资金 == Σ 交易净盈亏`（有单测守着这条恒等式）。

细节见 [docs/DESIGN.md](docs/DESIGN.md)。

## 真实数据上的结论（2021–2026，28 个品种）

28 个品种、100 万本金、目标波动 10%、逐品种滑点。
**天勤主连的日线最早到 2016-01-05**（28 个品种同一天），2015 及更早拿不到连续序列。

| 窗口 | 配置 | 年化 | 波动 | 夏普 | 最大回撤 | 交易 | 成本 | PSR |
|---|---|---|---|---|---|---|---|---|
| 2016→今（2653 天） | 40/20 基线 | **−1.13%** | 2.34% | −0.47 | 14.81% | 660 | 2.71% | 6.2% |
| 2016→今 | 40/20 + ADX≥18 | −0.81% | 2.50% | −0.31 | 11.29% | 676 | 2.72% | 15.5% |
| 2021→今（1416 天） | 40/20 基线 | +0.17% | 2.38% | 0.09 | 4.81% | 304 | 1.16% | 58.0% |
| 2021→今 | 40/20 + ADX≥18 | +0.57% | 2.53% | 0.24 | 4.35% | 332 | 1.27% | 71.4% |
| 合成数据（缓存建立前的基线） | — | +9.46% | 2.50% | 3.64 | 3.27% | — | — | — |

**把窗口拉到 11 年，策略是亏的。** 之前 2021–2026 那段"不亏"是因为窗口恰好避开了 2016–2020。
合成数据把年化高估了约 10 个百分点。

其它两轮实验的结论（详见 [docs/SWEEP.md](docs/SWEEP.md)）：

- **ADX 强度过滤有方向性但不显著**：18 这个阈值两段都改善、回撤也降，但 22/26/30 反而更差——
  非单调说明这是噪声里的一个点（DSR 13.6%）。可以留着（降回撤、不改逻辑），别当边缘。
- **"FG/CF 流动性差"这个前提是错的**：按日均成交额，两者在 28 个品种里排 19–20；
  真正垫底的是 c、jm、al、j、hc、v。按 ≥130 亿剔掉这 6 个后 OOS 反而更差（−0.54%）。
  别按感觉剔品种，按数据剔也不会让没有边缘的信号产生边缘。
- **滑点已按品种重估**（1 分钟线低分位振幅当价差代理，分辨率不够时 3 跳封顶），
  OOS 成本从 0.97% → 1.16%。

## 统计体检：这个结果能信吗

回测给出「年化 20%、夏普 1.5」不代表有边缘。每次回测的 `report.md` 末尾现在都带一节
**统计体检**，用数学回答四个问题：样本够不够（MinTRL）、夏普是不是噪声（PSR + 块自助区间）、
试过多少次参数（DSR 通缩夏普）、风险长什么样（VaR/CVaR/GARCH）。

上面那次真实数据回测（40/20，2021-01→2026-11，28 品种）的体检结论：

| 检验 | 值（11 年窗口） | 判读 |
|---|---|---|
| 结论 | — | **与噪声不可区分** |
| 年化夏普 | −0.47 | 95% 区间 [−1.14, +0.09]，跨过 0 |
| PSR（真实夏普>0 的概率） | 6.2% | 几乎没有 |
| DSR（试过 12 组参数） | 0.1% | 扣掉挑选偏差后完全不显著 |
| 日收益 t 检验 | p = 0.13 | 均值与 0 无显著差异（但方向是负的） |
| 胜率后验 | 41.3%，区间 [37.6%, 45.1%] | 明显低于 50% |
| 交易笔数 | 660 | 样本量足够，不是「样本太小看不出来」 |
| Hurst（收益） | 0.544 | 接近随机游走 |

**这比夏普好看得多也重要得多**：它把「参数扫描挑出来的最好那个」和真实边缘分开。
数学推导与公式出处见 [docs/MATH.md](docs/MATH.md)。

## 它做不到什么

- **没有实盘下单**。要接 CTP 得自己写，且先想清楚风控。
- **拿不到真实盘中滑点**。日频撮合用开盘价 ± 固定跳数，真实冲击成本只有实盘有。
- **合约规格是近似值**。手续费/保证金随交易所公告调整，下单前必须核对。
- **合成数据不是行情**。它只用来验证流程，报告里永远标注 `synthetic`。
- **不构成投资建议**。历史回测不代表未来收益。
- **跨策略腿不做净额对冲**：两条腿在同一个品种上方向相反时，账上会同时持有两个方向的持仓
  （净敞口正确，但多付一份手续费与保证金）。这是为归因清晰做的取舍。

## 开发

```bash
python -m unittest discover -s python/tests -t python   # 233 个单测
node --check lib/index.js                              # 插件语法
node tools/smoke.mjs                                   # 插件冒烟（假 ctx + 真引擎）
cd python && python -m qcta backtest --pool starter --start 2021-01-01 --name ci_run
python tools/verify_invariants.py runs/ci_run          # 三条账本恒等式
python tools/check_consistency.py                      # 文档里的数字与代码对得上
```

## 许可证

MIT
