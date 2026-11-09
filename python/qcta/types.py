"""核心数据结构。全部标准库，无第三方依赖。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bar:
    """一根日线。date 为 YYYY-MM-DD 字符串，便于 CSV 落盘与排序。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    open_interest: float = 0.0

    def is_valid(self) -> bool:
        return (
            self.open > 0
            and self.high >= self.low
            and self.high >= max(self.open, self.close)
            and self.low <= min(self.open, self.close)
        )


@dataclass
class Position:
    """单品种持仓。direction=+1 多 / -1 空；lots 为正。"""

    symbol: str
    direction: int
    lots: int
    avg_price: float
    last_price: float
    open_date: str = ""
    margin: float = 0.0
    acc_cost: float = 0.0   # 累计开仓手续费（按手数比例摊到平仓交易上）

    def market_value(self, multiplier: float) -> float:
        return self.lots * self.avg_price * multiplier

    def unrealized(self, multiplier: float) -> float:
        return self.direction * self.lots * (self.last_price - self.avg_price) * multiplier


@dataclass
class Fill:
    """一笔成交。slippage_cost 与 commission 分开记，便于成本归因。"""

    date: str
    symbol: str
    sleeve: str
    direction: int          # +1 买 / -1 卖
    lots: int
    price: float            # 含滑点后的成交价
    ref_price: float        # 理论价（开盘价/收盘价）
    slippage_cost: float    # 元
    commission: float       # 元
    close_today: bool = False
    reason: str = ""


@dataclass
class ClosedTrade:
    """一次完整的开平仓往返，用于胜率/盈亏比统计。"""

    symbol: str
    sleeve: str
    direction: int
    lots: int
    open_date: str
    close_date: str
    open_price: float
    close_price: float
    gross_pnl: float
    cost: float

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.cost

    @property
    def return_pct(self) -> float:
        base = abs(self.open_price * self.lots)
        return (self.net_pnl / base * 100.0) if base > 0 else 0.0


@dataclass
class DailyRecord:
    """每个交易日收盘后的账户快照。"""

    date: str
    equity: float
    cash: float
    margin: float
    gross_exposure: float
    net_exposure: float
    drawdown: float = 0.0
    halted: bool = False
    note: str = ""


@dataclass
class TargetPosition:
    """策略输出的目标仓位。lots 由引擎按波动率预算换算。"""

    symbol: str
    direction: int          # +1 多 / -1 空 / 0 平
    weight: float = 1.0     # 该品种在本策略内的相对风险权重


@dataclass
class EngineConfig:
    start_date: str = "2018-01-01"
    end_date: str = ""                      # 空 = 到数据最后一根
    initial_capital: float = 1_000_000.0
    target_vol: float = 0.10                # 组合年化目标波动
    atr_period: int = 20
    slip_ticks: float = 1.0                 # 每笔滑点（跳）
    max_leverage: float = 1.5               # 名义敞口/权益 上限
    max_drawdown_halt: float = 0.20         # 超过即停止开新仓
    max_symbol_risk: float = 0.02           # 单品种最大风险占权益
    stop_atr: float = 0.0                   # ATR 止损倍数，0=关闭
    risk_free: float = 0.0
    # ── 配比 / 模型 ──────────────────────────────────────
    sizing: str = "vol_parity"              # vol_parity（按策略给的权重）| optimizer（最优配比）
    model_method: str = "risk_parity"       # equal_weight|inverse_vol|risk_parity|min_variance|mean_variance
    model_lookback: int = 250               # 估计协方差用的交易日数
    model_rebalance: int = 20               # 每多少个交易日重估一次
    model_max_weight: float = 0.15          # 单品种权重上限
    model_sector_cap: float = 0.35          # 板块权重上限
    model_min_bars: int = 250               # 进入品种池所需的最少历史
    model_exclude_sectors: tuple = ()       # 例如 ("金融",)
    risk_weights: dict = None               # 显式指定权重（优先级最高，paper/外部传入用）
    # 预算回收：把「单手风险超过预算、算出 0 手」的品种预算，回填给能下单的品种。
    # 默认关闭——它会把实际波动从约 1/4 目标放大到接近目标（约 4 倍风险），
    # 静默开启等于偷偷改风险预算。要开启请同时下调 target_vol。
    recycle_budget: bool = False
    sleeves: tuple = ()
