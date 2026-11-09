"""模拟盘（paper trading）。

明确边界，先说清楚再谈实现：
- 本引擎**不会发任何真实委托**。没有接 CTP，也没有下单代码路径。
- 日频 CTA 的「模拟盘」= 用真实行情逐日推进同一套引擎，账本确定性地向前生长。
  它的价值是：跑通「数据 → 信号 → 仓位 → 撮合 → 结算 → 归因」全链路，
  并积累一条**事前生成**的净值轨迹。
- 它做不到的：真实盘中滑点、流动性冲击、部分成交、断线重连。这些只有实盘才有。

状态落在 state/paper.json，可随时 step、可断点续跑（重算幂等）。
"""

import datetime as dt
import json
import os
import sys
import time

from .data import load_bundle
from .engine import Backtester
from .metrics import compute_metrics
from .specs import resolve_pool
from .types import EngineConfig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_PATH = os.path.join(REPO_ROOT, "state", "paper.json")

STATE_VERSION = 1


def _state_path():
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    return STATE_PATH


def load_state():
    path = _state_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def save_state(state):
    path = _state_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _cfg_from_state(state) -> EngineConfig:
    c = state["config"]
    return EngineConfig(
        start_date=c["start_date"],
        end_date="",
        initial_capital=c["initial_capital"],
        target_vol=c["target_vol"],
        atr_period=c["atr_period"],
        slip_ticks=c["slip_ticks"],
        max_leverage=c["max_leverage"],
        max_drawdown_halt=c["max_drawdown_halt"],
        max_symbol_risk=c["max_symbol_risk"],
        stop_atr=c.get("stop_atr", 0.0),
        sleeves=tuple(c["sleeves"]),
    )


def _init_state(codes, capital, vol, sleeves, start_date, slip, leverage, dd_halt, symbol_risk, atr_period, stop_atr):
    return {
        "version": STATE_VERSION,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "updated_at": "",
        "codes": list(codes),
        "config": {
            "start_date": start_date,
            "initial_capital": float(capital),
            "target_vol": float(vol),
            "atr_period": int(atr_period),
            "slip_ticks": float(slip),
            "max_leverage": float(leverage),
            "max_drawdown_halt": float(dd_halt),
            "max_symbol_risk": float(symbol_risk),
            "stop_atr": float(stop_atr),
            "sleeves": [{"name": s["name"], "strategy": s["strategy"], "alloc": s["alloc"], "kwargs": s.get("kwargs", {})}
                        for s in sleeves],
        },
        "last_date": "",
        "data_source": "",
        "journal": [],
        "metrics": {},
        "positions": [],
        "open_orders": [],
    }


def _journal_entry(date, sleeves, record):
    fills = []
    rejections = []
    positions = []
    for s in sleeves:
        for f in s.account.fills:
            if f.date == date:
                fills.append({
                    "symbol": f.symbol, "sleeve": f.sleeve, "direction": f.direction,
                    "lots": f.lots, "price": round(f.price, 4), "reason": f.reason,
                    "commission": round(f.commission, 2), "slippage": round(f.slippage_cost, 2),
                })
        for r in s.account.rejections:
            if r.get("date") == date:
                rejections.append(r)
        for code, pos in s.account.positions.items():
            positions.append({
                "symbol": code, "sleeve": s.name, "direction": pos.direction,
                "lots": pos.lots, "avg_price": round(pos.avg_price, 4),
                "last_price": round(pos.last_price, 4),
            })
    return {
        "date": date,
        "equity": round(record.equity, 2),
        "drawdown": round(record.drawdown, 5),
        "halted": bool(record.halted),
        "fills": fills,
        "rejections": rejections,
        "positions": positions,
    }


def step(live: bool = False, quiet: bool = False):
    """把 paper 账本推进到最新交易日。幂等：同一天重复调用不会产生新记录。"""
    state = load_state()
    if state is None:
        raise RuntimeError("还没有 paper 会话，先运行 qcta paper init")

    cfg = _cfg_from_state(state)
    today = dt.date.today().isoformat()
    bundle = load_bundle(state["codes"], cfg.start_date, today, prefer_live=live, refresh=False)
    dates = bundle.dates
    if not dates:
        raise RuntimeError("没有可用的行情数据")

    bt = Backtester(cfg, bundle)
    collected = []

    def on_day(date, sleeves, record):
        collected.append(_journal_entry(date, sleeves, record))

    result = bt.run(on_day=on_day, liquidate=False)

    existing = {e["date"] for e in state["journal"]}
    new_entries = [e for e in collected if e["date"] not in existing]
    state["journal"].extend(new_entries)
    state["last_date"] = dates[-1]
    state["data_source"] = bundle.source
    state["notes"] = bundle.notes
    # liquidate=False 已保证「曲线 / 成交 / 分腿」三者口径一致，
    # 直接用回测的 aggregate 即可，不必再另造一条曲线。
    state["metrics"] = compute_metrics(result.aggregate, result.trades, cfg.initial_capital, cfg.risk_free, result.fills)
    state["positions"] = collected[-1]["positions"] if collected else []
    state["open_orders"] = []
    for s in result.sleeves:
        if s.pending is not None:
            state["open_orders"].append({
                "sleeve": s.name,
                "for_date": "next-trading-day",
                "targets": [{"symbol": t.symbol, "direction": t.direction} for t in s.pending["targets"]],
            })
    state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    save_state(state)

    if not quiet:
        m = state["metrics"]
        sys.stdout.write("paper 推进到 %s（新增 %d 个交易日）\n" % (state["last_date"], len(new_entries)))
        sys.stdout.write("权益 %s / 总收益 %s%% / 回撤 %s%% / 持仓 %d\n" % (
            "{:,.0f}".format(m.get("final_equity", 0)), m.get("total_return_pct"),
            m.get("max_drawdown_pct"), len(state["positions"])))
    return state, len(new_entries)


def cmd_paper(args):
    action = args.action
    if action == "init":
        codes = resolve_pool(args.pool)
        sleeves = _parse_sleeves(args.sleeves)
        state = _init_state(
            codes=codes,
            capital=args.capital,
            vol=args.vol,
            sleeves=sleeves,
            start_date=args.start,
            slip=args.slip,
            leverage=args.leverage,
            dd_halt=args.dd_halt,
            symbol_risk=args.symbol_risk,
            atr_period=args.atr,
            stop_atr=args.stop_atr,
        )
        save_state(state)
        payload = {"ok": True, "action": "init", "codes": codes, "state_path": STATE_PATH,
                   "config": state["config"]}
        return _emit(payload, args.json, lambda p: "paper 会话已初始化：%d 个品种，资金 %.0f，状态在 %s" % (
            len(p["codes"]), p["config"]["initial_capital"], p["state_path"]))

    if action == "reset":
        if os.path.exists(STATE_PATH):
            os.replace(STATE_PATH, STATE_PATH + ".bak")
        return _emit({"ok": True, "action": "reset"}, args.json, lambda p: "paper 状态已重置（旧文件存为 .bak）")

    if action == "status":
        state = load_state()
        if state is None:
            return _emit({"ok": False, "error": "还没有 paper 会话，先运行 qcta paper init"}, args.json, lambda p: p["error"])
        m = state.get("metrics", {})
        payload = {
            "ok": True,
            "last_date": state["last_date"],
            "data_source": state.get("data_source"),
            "config": state["config"],
            "metrics": m,
            "positions": state.get("positions", []),
            "open_orders": state.get("open_orders", []),
            "journal_tail": state["journal"][-10:],
            "state_path": STATE_PATH,
        }
        def text(p):
            lines = ["paper 最新交易日：%s（数据来源 %s）" % (p["last_date"], p["data_source"]),
                     "权益 %s / 总收益 %s%% / 回撤 %s%%" % (
                         "{:,.0f}".format(m.get("final_equity", 0)),
                         m.get("total_return_pct"), m.get("max_drawdown_pct")),
                     "持仓 %d 个：" % len(p["positions"])]
            for pos in p["positions"]:
                lines.append("  %-4s %s %d 手 @ %.2f（现价 %.2f）" % (
                    pos["symbol"], "多" if pos["direction"] > 0 else "空",
                    pos["lots"], pos["avg_price"], pos["last_price"]))
            return "\n".join(lines)
        return _emit(payload, args.json, text)

    if action == "step":
        state, added = step(live=args.live, quiet=args.json)
        if args.json:
            return _emit({"ok": True, "last_date": state["last_date"], "added_days": added,
                          "metrics": state["metrics"], "positions": state["positions"]}, True, lambda p: "")
        return 0

    if action == "run":
        steps = 0
        while True:
            try:
                state, added = step(live=args.live, quiet=False)
            except Exception as exc:
                sys.stdout.write("step 失败：%s\n" % exc)
            steps += 1
            if args.max_steps and steps >= args.max_steps:
                break
            time.sleep(max(5, args.interval))
        return 0

    sys.stdout.write("未知 action：%s\n" % action)
    return 2


def _parse_sleeves(text):
    from .cli import parse_sleeves

    return parse_sleeves(text)


def _emit(payload, as_json, text_fn):
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(text_fn(payload) + "\n")
    return 0
