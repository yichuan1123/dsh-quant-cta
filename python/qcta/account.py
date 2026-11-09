"""账户与撮合账本。

期货会计口径（与股票不同，务必注意）：
- 开仓不占用现金，只扣手续费；保证金是「可用资金校验」的约束，不是现金流出
- 权益 = 现金 + 浮动盈亏
- 平仓时把该笔的浮动盈亏结转进现金
这套口径下 equity 曲线与逐日盯市结算完全等价。
"""

from .specs import ContractSpec
from .types import ClosedTrade, DailyRecord, Fill, Position


class Account:
    """一个 sleeve（策略分仓）的独立账本。"""

    def __init__(self, name: str, capital: float):
        self.name = name
        self.initial_capital = float(capital)
        self.cash = float(capital)
        self.positions = {}          # code -> Position
        self.fills = []
        self.trades = []
        self.curve = []
        self.peak = float(capital)
        self.halted = False
        self.rejections = []         # 被风控/保证金拒绝的委托
        self.gross_cost = 0.0        # 手续费 + 滑点
        self.spec_table = {}         # code -> ContractSpec（由 engine 绑定）

    # ── 估值 ──────────────────────────────────────────────

    def equity(self) -> float:
        eq = self.cash
        for code, pos in self.positions.items():
            spec = self.spec_table.get(code)
            mult = spec.multiplier if spec is not None else 1.0
            eq += pos.unrealized(mult)
        return eq

    def bind_specs(self, specs):
        self.spec_table = dict(specs)

    def margin_used(self) -> float:
        total = 0.0
        for code, pos in self.positions.items():
            spec = self.spec_table.get(code)
            if spec is None:
                continue
            total += spec.margin(pos.lots, pos.last_price)
        return total

    def gross_exposure(self, prices=None) -> float:
        total = 0.0
        for code, pos in self.positions.items():
            spec = self.spec_table.get(code)
            if spec is None:
                continue
            price = prices.get(code, pos.last_price) if prices else pos.last_price
            total += pos.lots * price * spec.multiplier
        return total

    def net_exposure(self, prices=None) -> float:
        total = 0.0
        for code, pos in self.positions.items():
            spec = self.spec_table.get(code)
            if spec is None:
                continue
            price = prices.get(code, pos.last_price) if prices else pos.last_price
            total += pos.direction * pos.lots * price * spec.multiplier
        return total

    # ── 成交处理 ──────────────────────────────────────────

    def fill(self, code: str, spec: ContractSpec, direction: int, lots: int,
             price: float, ref_price: float, date: str, slip_cost: float,
             close_today: bool = False, reason: str = "") -> Fill:
        """direction: +1 买开/买平空；-1 卖开/卖平多。"""
        lots = int(lots)
        fee = spec.commission(lots, price, close_today=close_today)
        self.cash -= fee
        self.gross_cost += fee + slip_cost

        pos = self.positions.get(code)
        if pos is None or pos.lots == 0:
            # 新开仓
            self.positions[code] = Position(
                symbol=code,
                direction=direction,
                lots=lots,
                avg_price=price,
                last_price=price,
                open_date=date,
                margin=spec.margin(lots, price),
                acc_cost=fee,
            )
        elif pos.direction == direction:
            # 同向加仓：加权平均价，手续费累加
            total = pos.lots + lots
            pos.avg_price = (pos.avg_price * pos.lots + price * lots) / total
            pos.lots = total
            pos.acc_cost += fee
            pos.margin = spec.margin(pos.lots, pos.last_price)
        else:
            # 反向：先平后反手（超量部分反手开仓）
            close_lots = min(pos.lots, lots)
            realized = pos.direction * close_lots * (price - pos.avg_price) * spec.multiplier
            self.cash += realized
            # 开仓手续费按手数比例摊到本次平仓
            open_fee_alloc = pos.acc_cost * close_lots / pos.lots
            close_fee_alloc = fee * close_lots / lots
            self.trades.append(
                ClosedTrade(
                    symbol=code,
                    sleeve=self.name,
                    direction=pos.direction,
                    lots=close_lots,
                    open_date=pos.open_date,
                    close_date=date,
                    open_price=pos.avg_price,
                    close_price=price,
                    gross_pnl=realized,
                    cost=open_fee_alloc + close_fee_alloc,
                )
            )
            pos.acc_cost -= open_fee_alloc
            pos.lots -= close_lots
            if pos.lots == 0:
                del self.positions[code]
            else:
                pos.margin = spec.margin(pos.lots, pos.last_price)
            remainder = lots - close_lots
            if remainder > 0:
                self.positions[code] = Position(
                    symbol=code,
                    direction=direction,
                    lots=remainder,
                    avg_price=price,
                    last_price=price,
                    open_date=date,
                    margin=spec.margin(remainder, price),
                    acc_cost=fee - close_fee_alloc,
                )

        record = Fill(
            date=date,
            symbol=code,
            sleeve=self.name,
            direction=direction,
            lots=lots,
            price=price,
            ref_price=ref_price,
            slippage_cost=slip_cost,
            commission=fee,
            close_today=close_today,
            reason=reason,
        )
        self.fills.append(record)
        return record

    def mark(self, code: str, price: float):
        pos = self.positions.get(code)
        if pos is not None and price > 0:
            pos.last_price = price
            spec = self.spec_table.get(code)
            if spec is not None:
                pos.margin = spec.margin(pos.lots, price)

    def close_all(self, prices, date: str, spec_table, slip_ticks, reason: str = "liquidate"):
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            spec = spec_table[code]
            ref = prices.get(code, pos.last_price)
            if ref <= 0:
                continue
            n = slip_ticks.get(code, 1.0) if isinstance(slip_ticks, dict) else slip_ticks
            slip = n * spec.tick
            # 平多 => 卖出，价格下滑点；平空 => 买入，价格上滑点
            price = ref - slip if pos.direction > 0 else ref + slip
            self.fill(code, spec, -pos.direction, pos.lots, price, ref, date,
                      abs(slip) * spec.multiplier * pos.lots, reason=reason)

    def settle(self, date: str) -> DailyRecord:
        eq = self.equity()
        self.peak = max(self.peak, eq)
        dd = 0.0 if self.peak <= 0 else (self.peak - eq) / self.peak
        rec = DailyRecord(
            date=date,
            equity=eq,
            cash=self.cash,
            margin=self.margin_used(),
            gross_exposure=self.gross_exposure(),
            net_exposure=self.net_exposure(),
            drawdown=dd,
            halted=self.halted,
        )
        self.curve.append(rec)
        return rec
