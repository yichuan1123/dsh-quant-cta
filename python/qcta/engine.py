"""回测引擎。

事件顺序（改这里之前先读完）：
  每个交易日 t：
    1. 开盘 —— 执行「t-1 收盘时生成的目标簿」，成交价 = open(t) ± 滑点
    2. 收盘 —— 全部持仓按 close(t) 盯市
    3. 收盘后 —— 策略决策：读 close(t) 数据，生成 t+1 的目标簿
    4. 结算 —— 记一条权益快照；组合级回撤熔断在这里判定

「信号在 t 收盘产生、在 t+1 开盘成交」是防止未来函数的核心约定。
如果某个策略直接在 t 的收盘价成交，回测会系统性高估收益。
"""

from .account import Account
from .indicators import atr
from .sizing import cap_by_leverage, daily_risk_budget, lots_for_risk, normalize_weights
from .specs import get_spec
from .strategies import build as build_strategy
from .types import EngineConfig

MAX_PENDING_DAYS = 5


class Sleeve:
    """一条策略腿：独立资金、独立账本、独立归因。"""

    def __init__(self, name: str, strategy, capital: float, alloc: float):
        self.name = name
        self.strategy = strategy
        self.alloc = alloc
        self.capital = capital
        self.account = Account(name, capital)
        self.signals = {}
        self.pending = None
        self.pending_age = 0


class BacktestResult:
    def __init__(self, cfg, bundle, sleeves, dates, aggregate):
        self.cfg = cfg
        self.bundle = bundle
        self.sleeves = sleeves
        self.dates = dates
        self.aggregate = aggregate      # list[DailyRecord]
        self.metrics = {}
        self.costs = {}

    @property
    def fills(self):
        out = []
        for s in self.sleeves:
            out.extend(s.account.fills)
        return sorted(out, key=lambda f: f.date)

    @property
    def trades(self):
        out = []
        for s in self.sleeves:
            out.extend(s.account.trades)
        return sorted(out, key=lambda t: t.close_date)


class Backtester:
    def __init__(self, cfg: EngineConfig, bundle):
        self.cfg = cfg
        self.bundle = bundle
        self.specs = {code: get_spec(code) for code in bundle.bars_by_code}
        self.index = {}
        for code, bars in bundle.bars_by_code.items():
            self.index[code] = {b.date: b for b in bars}
        self.atr_by_code = {}
        for code, bars in bundle.bars_by_code.items():
            series = atr(bars, cfg.atr_period)
            self.atr_by_code[code] = {
                bars[i].date: series[i] for i in range(len(bars)) if series[i] is not None
            }
        self.all_dates = self._dates_in_range(bundle.dates)

    def _dates_in_range(self, dates):
        lo = self.cfg.start_date or "0000-00-00"
        hi = self.cfg.end_date or "9999-99-99"
        return [d for d in dates if lo <= d <= hi]

    # ── 组装 ──────────────────────────────────────────────

    def build_sleeves(self):
        specs = list(self.cfg.sleeves) if self.cfg.sleeves else [
            ("trend", "trend", 0.6, {}),
            ("xsec", "xsec", 0.4, {}),
        ]
        sleeves = []
        for item in specs:
            if isinstance(item, dict):
                name = item.get("name") or item.get("strategy")
                strat_name = item.get("strategy") or name
                alloc = float(item.get("alloc", 0.0))
                kwargs = item.get("kwargs") or {}
            else:
                name, strat_name, alloc, kwargs = item
            strategy = build_strategy(strat_name, **kwargs)
            capital = self.cfg.initial_capital * float(alloc)
            sleeves.append(Sleeve(name, strategy, capital, float(alloc)))
        total = sum(s.alloc for s in sleeves)
        if total <= 0:
            raise ValueError("sleeve 的资金分配合计为 0")
        # 归一化，避免用户写成 6/4 而不是 0.6/0.4
        if abs(total - 1.0) > 1e-9:
            for s in sleeves:
                s.alloc = s.alloc / total
                s.capital = self.cfg.initial_capital * s.alloc
                s.account = Account(s.name, s.capital)
        for s in sleeves:
            s.account.bind_specs(self.specs)
        return sleeves

    # ── 主循环 ────────────────────────────────────────────

    def run(self, on_day=None, liquidate=True) -> BacktestResult:
        if not self.all_dates:
            raise ValueError("所选区间内没有任何行情数据，检查开始/结束日期或数据源")
        sleeves = self.build_sleeves()
        for s in sleeves:
            s.signals = s.strategy.generate(self.bundle, self.cfg) or {}

        aggregate = []
        peak = self.cfg.initial_capital
        halted = False
        prev_date = None

        for date in self.all_dates:
            # 1) 开盘：执行上一交易日收盘生成的目标簿
            for s in sleeves:
                if s.pending is not None:
                    done = self._execute(s, date, allow_open=not halted)
                    if done:
                        s.pending = None
                        s.pending_age = 0
                    else:
                        s.pending_age += 1
                        if s.pending_age > MAX_PENDING_DAYS:
                            s.pending = None
                            s.pending_age = 0

            # 2) 收盘盯市
            for s in sleeves:
                for code in list(s.account.positions.keys()):
                    bar = self.index.get(code, {}).get(date)
                    if bar is not None:
                        s.account.mark(code, bar.close)

            # 3) 收盘后决策 → 生成次日目标簿
            for s in sleeves:
                self.decide(s, date)

            # 4) 结算 + 组合级熔断
            for s in sleeves:
                s.account.settle(date)
            total_equity = sum(s.account.equity() for s in sleeves)
            peak = max(peak, total_equity)
            dd = 0.0 if peak <= 0 else (peak - total_equity) / peak
            if dd >= self.cfg.max_drawdown_halt and not halted:
                halted = True
            for s in sleeves:
                if s.account.curve:
                    s.account.curve[-1].drawdown = dd
                    s.account.curve[-1].halted = halted
            aggregate.append(
                type(sleeves[0].account.curve[-1])(
                    date=date,
                    equity=total_equity,
                    cash=sum(s.account.cash for s in sleeves),
                    margin=sum(s.account.margin_used() for s in sleeves),
                    gross_exposure=sum(s.account.gross_exposure() for s in sleeves),
                    net_exposure=sum(s.account.net_exposure() for s in sleeves),
                    drawdown=dd,
                    halted=halted,
                )
            )
            if on_day is not None:
                on_day(date, sleeves, aggregate[-1])
            prev_date = date

        # 收尾：回测按最后收盘价强平，让成交与统计闭合。
        # paper 传 liquidate=False —— 模拟盘要保留持仓，那才是账户真实状态。
        last = self.all_dates[-1]
        if liquidate:
            for s in sleeves:
                prices = {c: (self.index.get(c, {}).get(last).close if self.index.get(c, {}).get(last) else 0.0)
                          for c in list(s.account.positions.keys())}
                s.account.close_all(prices, last, self.specs, self.cfg.slip_ticks)
        if aggregate:
            # 总权益与各腿曲线必须同时对齐到「收尾之后」的状态，
            # 否则分腿归因加起来对不上 headline（差的就是强平成本）。
            aggregate[-1].equity = sum(s.account.equity() for s in sleeves)
            if liquidate:
                aggregate[-1].gross_exposure = 0.0
                aggregate[-1].net_exposure = 0.0
                aggregate[-1].margin = 0.0
            for s in sleeves:
                if s.account.curve:
                    s.account.curve[-1].equity = s.account.equity()
                    if liquidate:
                        s.account.curve[-1].gross_exposure = 0.0
                        s.account.curve[-1].net_exposure = 0.0
                        s.account.curve[-1].margin = 0.0

        return BacktestResult(self.cfg, self.bundle, sleeves, self.all_dates, aggregate)

    def decide(self, sleeve: Sleeve, date: str):
        """收盘后决策：只在策略给出信号的日期重建目标簿。"""
        if date not in sleeve.signals:
            return False
        targets = sleeve.signals[date]
        stops = self._stop_hits(sleeve, date)
        if stops:
            targets = [t for t in targets if t.symbol not in stops]
        sleeve.pending = {
            "targets": targets,
            "atr": {c: self.atr_by_code.get(c, {}).get(date) for c in self.bundle.bars_by_code},
            "equity": sleeve.account.equity(),
            "from": date,
        }
        sleeve.pending_age = 0
        return True

    def mark_all(self, sleeve: Sleeve, date: str):
        for code in list(sleeve.account.positions.keys()):
            bar = self.index.get(code, {}).get(date)
            if bar is not None:
                sleeve.account.mark(code, bar.close)

    # ── 目标簿执行 ────────────────────────────────────────

    def _desired(self, sleeve: Sleeve, spec_snapshot):
        targets = spec_snapshot["targets"]
        equity = spec_snapshot["equity"]
        atr_map = spec_snapshot["atr"]
        if equity <= 0:
            return {}
        weights = normalize_weights([t.weight for t in targets])
        budget_total = daily_risk_budget(equity, self.cfg.target_vol)
        desired = {}
        for t, w in zip(targets, weights):
            spec = self.specs.get(t.symbol)
            if spec is None or t.direction == 0:
                continue
            a = atr_map.get(t.symbol) or 0.0
            lots = lots_for_risk(spec, equity, a, budget_total * w, self.cfg.max_symbol_risk)
            if lots > 0:
                desired[t.symbol] = [int(t.direction), lots]
        prices = {c: (self.index.get(c, {}).get(spec_snapshot["from"]).close if self.index.get(c, {}).get(spec_snapshot["from"]) else 0.0)
                  for c in desired}
        desired = cap_by_leverage({c: (v[0], v[1]) for c, v in desired.items()},
                                  prices, self.specs, equity, self.cfg.max_leverage)
        return {c: [d, l] for c, (d, l) in desired.items() if l > 0}

    def _execute(self, sleeve: Sleeve, date: str, allow_open: bool) -> bool:
        snap = sleeve.pending
        if snap is None:
            return True
        desired = self._desired(sleeve, snap)
        acct = sleeve.account
        traded_any = False
        all_codes = set(acct.positions.keys()) | set(desired.keys())
        missing_bar = False

        for code in sorted(all_codes):
            bar = self.index.get(code, {}).get(date)
            if bar is None or bar.volume <= 0 or not bar.is_valid():
                missing_bar = True
                continue
            spec = self.specs[code]
            cur = acct.positions.get(code)
            cur_dir = cur.direction if cur else 0
            cur_lots = cur.lots if cur else 0
            d = desired.get(code)
            d_dir = int(d[0]) if d else 0
            d_lots = int(d[1]) if d else 0

            slip = self.cfg.slip_ticks * spec.tick

            def buy(lots, reason):
                acct.fill(code, spec, +1, lots, bar.open + slip, bar.open, date, slip * spec.multiplier * lots, reason=reason)
                traded_any = True

            def sell(lots, reason):
                acct.fill(code, spec, -1, lots, bar.open - slip, bar.open, date, slip * spec.multiplier * lots, reason=reason)
                traded_any = True

            if d_dir == 0:
                if cur_lots > 0:
                    (sell if cur_dir > 0 else buy)(cur_lots, "flat")
                continue

            if cur_dir == 0:
                if allow_open:
                    self._open(sleeve, code, d_dir, d_lots, bar, date, buy, sell)
                continue

            if cur_dir == d_dir:
                delta = d_lots - cur_lots
                if delta > 0:
                    if allow_open:
                        self._open(sleeve, code, d_dir, delta, bar, date, buy, sell)
                elif delta < 0:
                    (sell if cur_dir > 0 else buy)(-delta, "resize")
            else:
                (sell if cur_dir > 0 else buy)(cur_lots, "reverse")
                if allow_open:
                    self._open(sleeve, code, d_dir, d_lots, bar, date, buy, sell)

        if missing_bar and not traded_any:
            return False
        return True

    def _open(self, sleeve, code, direction, lots, bar, date, buy, sell):
        """带保证金校验的开仓。可用资金不足时按可负担手数缩减。"""
        if lots <= 0:
            return
        spec = self.specs[code]
        acct = sleeve.account
        price = bar.open
        margin_per_lot = spec.margin(1, price)
        free = acct.equity() * 0.85 - acct.margin_used()
        affordable = int(free / margin_per_lot) if margin_per_lot > 0 else 0
        if affordable <= 0:
            acct.rejections.append({"date": date, "symbol": code, "want": lots, "got": 0, "reason": "margin"})
            return
        lots = min(lots, affordable)
        if direction > 0:
            buy(lots, "open")
        else:
            sell(lots, "open")

    def _stop_hits(self, sleeve: Sleeve, date: str):
        """ATR 止损：反向 excursion 超过 stop_atr 倍 ATR 的持仓，次日开盘平掉。"""
        stop_atr = getattr(self.cfg, "stop_atr", 0.0)
        if not stop_atr or stop_atr <= 0:
            return set()
        hits = set()
        for code, pos in sleeve.account.positions.items():
            a = self.atr_by_code.get(code, {}).get(date)
            if not a or a <= 0:
                continue
            adverse = pos.direction * (pos.last_price - pos.avg_price)
            if adverse < 0 and abs(adverse) > stop_atr * a:
                hits.add(code)
        return hits
