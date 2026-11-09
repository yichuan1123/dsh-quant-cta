"""命令行入口。

所有子命令都支持 --json，向 stdout 输出**单个** JSON 对象，供 DSH 插件消费。
人读模式打印可复制的摘要文本。
"""

import argparse
import datetime as dt
import json
import os
import sys

from . import __version__
from .data import (TqSdkError, cache_dir, clear_credentials, credentials_path,
                   credentials_status, fetch_tqsdk, load_bundle, load_cache,
                   load_credentials, probe_credentials, save_credentials, save_cache)
from .engine import Backtester
from .metrics import compute_metrics
from .report import render_markdown, write_reports
from .specs import DEFAULT_POOL, SPECS, STARTER_POOL, get_spec, resolve_pool
from .types import EngineConfig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_DIR = os.path.join(REPO_ROOT, "runs")
STATE_DIR = os.path.join(REPO_ROOT, "state")


def runs_dir():
    os.makedirs(RUNS_DIR, exist_ok=True)
    return RUNS_DIR


def state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)
    return STATE_DIR


def parse_sleeves(text):
    """trend:0.6,xsec:0.4 → [{'name','strategy','alloc','kwargs'}]"""
    if not text:
        return [{"name": "trend", "strategy": "trend", "alloc": 0.6, "kwargs": {}},
                {"name": "xsec", "strategy": "xsec", "alloc": 0.4, "kwargs": {}}]
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, alloc = part.partition(":")
        name = name.strip()
        try:
            a = float(alloc) if alloc else 0.0
        except ValueError:
            a = 0.0
        out.append({"name": name, "strategy": name, "alloc": a, "kwargs": {}})
    return out


def _emit(payload, as_json, text_fn):
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(text_fn(payload) + "\n")
    return 0


# ───────────────────────────── doctor ─────────────────────────────

def cmd_doctor(args):
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("python", sys.version_info >= (3, 8), sys.version.split()[0])

    tq_ver = None
    try:
        import tqsdk  # type: ignore

        tq_ver = getattr(tqsdk, "__version__", "unknown")
        add("tqsdk", True, tq_ver)
    except Exception as exc:
        add("tqsdk", False, "未安装：pip install tqsdk（%s）" % exc)

    cred = load_credentials()
    add("credentials", cred is not None,
        "已配置（%s）" % (cred[0] if cred else "") if cred else
        "缺少快期账号：设置 QCTA_TQ_USER / QCTA_TQ_PASS，或写 " + credentials_path())

    add("cache_dir", os.path.isdir(cache_dir()), cache_dir())
    add("runs_dir", os.path.isdir(runs_dir()), runs_dir())

    cached = {}
    for code in DEFAULT_POOL:
        bars = load_cache(code)
        if bars:
            cached[code] = {"bars": len(bars), "first": bars[0].date, "last": bars[-1].date}
    add("cache_coverage", len(cached) > 0, "%d/%d 个品种已缓存" % (len(cached), len(DEFAULT_POOL)))

    ok = all(c["ok"] for c in checks if c["name"] in ("python", "cache_dir", "runs_dir"))
    payload = {
        "ok": ok,
        "version": __version__,
        "python": sys.version.split()[0],
        "tqsdk": tq_ver,
        "credentials": bool(cred),
        "credentials_path": credentials_path(),
        "cache_dir": cache_dir(),
        "runs_dir": runs_dir(),
        "checks": checks,
        "cached": cached,
        "notice": "没有快期凭据时，回测会使用确定性合成数据并在报告里标注 synthetic。",
    }
    return _emit(payload, args.json, lambda p: "\n".join(
        ["%s %s  %s" % ("OK " if c["ok"] else "MISS", c["name"], c["detail"]) for c in p["checks"]]
        + ["", "version %s" % p["version"]]))


# ───────────────────────────── auth ─────────────────────────────

def _read_stdin_json():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def cmd_auth(args):
    """账号密码管理。

    密码只从 stdin 读（不进 argv，不会出现在进程列表里），
    输出里永远只有脱敏账号，绝不回显密码。
    """
    action = args.action

    if action == "status":
        payload = credentials_status()
        return _emit(payload, args.json, lambda p: "\n".join([
            "账号凭据：%s" % ("已配置（%s）" % p["masked_user"] if p["configured"] else "未配置"),
            "来源：%s" % (p["source"] or "—"),
            "文件：%s（%s）" % (p["path"], "存在" if p["file_exists"] else "不存在"),
            "环境变量：%s" % ("已设置" if p["env_present"] else "未设置"),
        ]))

    if action == "login":
        body = _read_stdin_json()
        user = body.get("user") or args.user
        password = body.get("password") or ""
        if not user or not password:
            return _emit({"ok": False, "error": "缺少账号或密码"}, args.json, lambda p: p["error"])
        try:
            status = save_credentials(user, password)
        except ValueError as exc:
            return _emit({"ok": False, "error": str(exc)}, args.json, lambda p: p["error"])
        payload = {"ok": True, "saved": True, "status": status}
        if args.verify:
            payload["probe"] = probe_credentials(user, password, timeout_s=args.timeout)
            payload["ok"] = True  # 保存成功即 ok；连通性单独看 probe
        return _emit(payload, args.json, lambda p: "\n".join([
            "已保存账号 %s" % p["status"]["masked_user"],
            "文件：%s" % p["status"]["path"],
            ("连通性：%s" % ("OK，%s 最新价 %s" % (p["probe"].get("symbol"), p["probe"].get("last_price"))
                            if p.get("probe", {}).get("ok") else "失败 —— %s" % p.get("probe", {}).get("error", "")))
            if "probe" in p else "提示：加 --verify 可以顺便验证连通性",
        ]))

    if action == "test":
        body = _read_stdin_json()
        user = body.get("user")
        password = body.get("password")
        res = probe_credentials(user, password, timeout_s=args.timeout)
        return _emit(res, args.json, lambda p: ("连通 OK：%s 最新价 %s" % (p.get("symbol"), p.get("last_price")))
                                              if p.get("ok") else ("连通失败：%s" % p.get("error")))

    if action == "logout":
        payload = clear_credentials()
        return _emit({"ok": True, **payload}, args.json,
                     lambda p: "已删除本机凭据文件" if p.get("removed") else "本来就没有凭据文件")

    sys.stdout.write("未知 action：%s\n" % action)
    return 2


# ───────────────────────────── specs ─────────────────────────────

def cmd_specs(args):
    rows = []
    for code in sorted(SPECS):
        s = SPECS[code]
        rows.append({
            "code": code, "name": s.name, "exchange": s.exchange,
            "multiplier": s.multiplier, "tick": s.tick, "tick_value": round(s.tick_value, 4),
            "margin_rate": s.margin_rate, "fee_rate": s.fee_rate, "fee_per_lot": s.fee_per_lot,
            "sector": s.sector, "tq_symbol": s.tq_symbol(),
        })
    return _emit({"specs": rows, "default_pool": DEFAULT_POOL, "starter_pool": STARTER_POOL},
                 args.json,
                 lambda p: "\n".join("%-4s %-8s %-6s mult=%-7g tick=%-6g 一跳=%g元 保证金=%g" % (
                     r["code"], r["name"], r["exchange"], r["multiplier"], r["tick"],
                     r["tick_value"], r["margin_rate"]) for r in p["specs"]))


# ───────────────────────────── data ─────────────────────────────

def cmd_data(args):
    if args.action == "status":
        rows = []
        for code in sorted(SPECS):
            bars = load_cache(code)
            rows.append({"code": code, "bars": len(bars),
                         "first": bars[0].date if bars else "",
                         "last": bars[-1].date if bars else ""})
        return _emit({"cache_dir": cache_dir(), "symbols": rows}, args.json,
                     lambda p: "\n".join("%-4s %5d 根  %s → %s" % (r["code"], r["bars"], r["first"], r["last"])
                                          for r in p["symbols"]))

    codes = resolve_pool(args.pool)
    results = []
    for code in codes:
        spec = get_spec(code)
        entry = {"code": code, "source": None, "bars": 0}
        try:
            if args.live:
                bars, adj = fetch_tqsdk(code, spec=spec)
                save_cache(code, bars)
                entry.update(source="tqsdk", bars=len(bars), adj=adj)
            else:
                bars = load_cache(code)
                if not bars:
                    entry.update(source="cache-miss", bars=0)
                else:
                    entry.update(source="cache", bars=len(bars))
        except TqSdkError as exc:
            entry.update(source="tqsdk-failed", error=str(exc))
        results.append(entry)
    return _emit({"cache_dir": cache_dir(), "synced": results}, args.json,
                 lambda p: "\n".join("%-4s %-12s %s" % (r["code"], r.get("source"), r.get("error", ""))
                                      for r in p["synced"]))


# ───────────────────────────── optimize ─────────────────────────────

def cmd_optimize(args):
    """只解配比、不跑回测：适合先看一眼「全盘该给谁多少风险预算」。"""
    from .model import RollingModel

    codes = resolve_pool(args.pool)
    bundle = load_bundle(codes, args.start, args.end or "", prefer_live=args.live, refresh=args.refresh)
    missing = [c for c, bars in bundle.bars_by_code.items() if not bars]
    if missing:
        return _emit({"ok": False, "error": "以下品种没有数据：%s" % ", ".join(missing)}, args.json,
                     lambda p: p["error"])
    model = RollingModel(
        method=args.method,
        lookback=args.lookback,
        min_bars=args.min_bars,
        max_weight=args.max_weight,
        sector_cap=args.sector_cap,
        exclude_sectors=tuple(x for x in (args.exclude_sectors or "").split(",") if x),
    )
    state = model.update(bundle.bars_by_code, asof=args.asof or "")
    payload = {
        "ok": True,
        "data_source": bundle.source,
        "notes": bundle.notes,
        "method": state["method"],
        "asof": state["asof"],
        "weights": state["weights"],
        "diagnostics": state["diagnostics"],
    }
    ranked = sorted(state["weights"].items(), key=lambda kv: -kv[1])

    def text(p):
        d = p["diagnostics"]
        lines = [
            "最优配比（%s，数据来源 %s）" % (p["method"], p["data_source"]),
            "品种数 %s / 观测 %s 天 / 组合年化波动 %.2f%% / 有效品种数 %.1f" % (
                d.get("n"), d.get("observations"), d.get("ann_vol_pct", 0), d.get("effective_n", 0)),
            "",
            "品种  权重    风险贡献",
        ]
        rc = d.get("risk_contributions", {})
        for code, w in ranked:
            lines.append("  %-4s %6.2f%%  %6.2f%%" % (code, w * 100, rc.get(code, 0)))
        if d.get("sector_totals_pct"):
            lines.append("")
            lines.append("板块：" + "  ".join("%s %.1f%%" % (k, v) for k, v in d["sector_totals_pct"].items()))
        dropped = d.get("universe", {}).get("dropped", [])
        if dropped:
            lines.append("剔除 %d 个：" % len(dropped) + ", ".join(
                "%s(%s)" % (x["code"], x["reason"]) for x in dropped[:10]))
        return "\n".join(lines)

    return _emit(payload, args.json, text)


# ───────────────────────────── backtest ─────────────────────────────

def _config_from_args(args):
    return EngineConfig(
        start_date=args.start,
        end_date=args.end or "",
        initial_capital=args.capital,
        target_vol=args.vol,
        atr_period=args.atr,
        slip_ticks=args.slip,
        max_leverage=args.leverage,
        max_drawdown_halt=args.dd_halt,
        max_symbol_risk=args.symbol_risk,
        stop_atr=args.stop_atr,
        sleeves=tuple(parse_sleeves(args.sleeves)),
        sizing=getattr(args, "sizing", "vol_parity"),
        model_method=getattr(args, "method", "risk_parity"),
        model_lookback=getattr(args, "lookback", 250),
        model_rebalance=getattr(args, "model_rebalance", 20),
        model_max_weight=getattr(args, "max_weight", 0.15),
        model_sector_cap=getattr(args, "sector_cap", 0.35),
        model_min_bars=getattr(args, "model_min_bars", 250),
        model_exclude_sectors=tuple(x for x in (getattr(args, "exclude_sectors", "") or "").split(",") if x),
    )


def cmd_backtest(args):
    codes = resolve_pool(args.pool)
    cfg = _config_from_args(args)
    bundle = load_bundle(codes, args.start, args.end or "", prefer_live=args.live, refresh=args.refresh)

    missing = [c for c, bars in bundle.bars_by_code.items() if not bars]
    if missing:
        return _emit({"ok": False, "error": "以下品种没有数据：%s" % ", ".join(missing)}, args.json,
                     lambda p: p["error"])

    bt = Backtester(cfg, bundle)
    result = bt.run()
    result.metrics = compute_metrics(result.aggregate, result.trades, cfg.initial_capital,
                                    cfg.risk_free, result.fills)

    run_id = args.name or "%s_%s" % (dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
                                     args.sleeves.replace(":", "").replace(",", "-") or "default")
    out = os.path.join(runs_dir(), run_id)
    meta = {
        "run_id": run_id,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "data_source": bundle.source,
        "notes": bundle.notes,
        "coverage": bundle.coverage(),
        "pool": codes,
        "version": __version__,
    }
    write_reports(out, result, meta)
    payload = {
        "ok": True,
        "run_id": run_id,
        "run_dir": out,
        "report_md": os.path.join(out, "report.md"),
        "report_json": os.path.join(out, "report.json"),
        "report_svg": os.path.join(out, "report.svg"),
        "data_source": bundle.source,
        "notes": bundle.notes,
        "metrics": result.metrics,
        "sleeves": {s.name: {"strategy": s.strategy.name, "alloc": s.alloc} for s in result.sleeves},
    }
    def text(p):
        m = p["metrics"]
        return "\n".join([
            "回测完成：%s" % p["run_id"],
            "数据来源：%s" % p["data_source"],
            "区间 %s → %s（%d 交易日）" % (m["start"], m["end"], m["trading_days"]),
            "总收益 %+.2f%% / 年化 %+.2f%% / 波动 %.2f%%" % (m["total_return_pct"], m["cagr_pct"], m["ann_vol_pct"]),
            "夏普 %.2f / 最大回撤 %.2f%% / 卡玛 %.2f" % (m["sharpe"], m["max_drawdown_pct"], m["calmar"]),
            "交易 %d 笔 / 胜率 %.1f%% / PF %.2f" % (m["trades"], m["win_rate_pct"], m["profit_factor"]),
            "成本 %.0f 元（占初始资金 %.2f%%）" % (m["cost_total"], m["cost_pct_initial"]),
            "报告：%s" % p["report_md"],
        ])
    return _emit(payload, args.json, text)


# ───────────────────────────── runs / report ─────────────────────────────

def list_runs():
    base = runs_dir()
    out = []
    for name in sorted(os.listdir(base), reverse=True):
        rj = os.path.join(base, name, "report.json")
        if not os.path.exists(rj):
            continue
        try:
            with open(rj, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        m = data.get("metrics", {})
        out.append({
            "run_id": name,
            "path": os.path.join(base, name),
            "generated_at": data.get("meta", {}).get("generated_at", ""),
            "data_source": data.get("meta", {}).get("data_source", ""),
            "cagr_pct": m.get("cagr_pct"), "sharpe": m.get("sharpe"),
            "max_drawdown_pct": m.get("max_drawdown_pct"), "trades": m.get("trades"),
        })
    return out


def cmd_runs(args):
    rows = list_runs()
    return _emit({"runs": rows}, args.json,
                 lambda p: "\n".join("%-28s %s CAGR=%s%% 夏普=%s 回撤=%s%%" % (
                     r["run_id"], r["data_source"], r["cagr_pct"], r["sharpe"], r["max_drawdown_pct"])
                     for r in p["runs"]) or "（还没有回测记录）")


def cmd_report(args):
    base = runs_dir()
    if args.run:
        path = args.run if os.path.isdir(args.run) else os.path.join(base, args.run)
    else:
        rows = list_runs()
        if not rows:
            return _emit({"ok": False, "error": "还没有任何回测记录，先跑 qcta backtest"}, args.json, lambda p: p["error"])
        path = rows[0]["path"]
    rj = os.path.join(path, "report.json")
    if not os.path.exists(rj):
        return _emit({"ok": False, "error": "找不到 %s" % rj}, args.json, lambda p: p["error"])
    with open(rj, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if args.json:
        return _emit(data, True, lambda p: "")
    md_path = os.path.join(path, "report.md")
    if os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as fh:
            sys.stdout.write(fh.read() + "\n")
    else:
        sys.stdout.write(json.dumps(data.get("metrics", {}), ensure_ascii=False, indent=2) + "\n")
    return 0


# ───────────────────────────── main ─────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(prog="qcta", description="商品期货 CTA 研究 / 回测 / 模拟盘引擎")
    p.add_argument("--version", action="version", version="qcta " + __version__)
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    au = sub.add_parser("auth", help="快期账号管理（密码只从 stdin 读）")
    au.add_argument("action", choices=["status", "login", "test", "logout"])
    au.add_argument("--stdin", action="store_true", help="从 stdin 读 JSON（密码不进 argv）")
    au.add_argument("--user", default="", help="账号（也可从 stdin JSON 给）")
    au.add_argument("--verify", action="store_true", help="保存后顺便验证连通性")
    au.add_argument("--timeout", type=int, default=20)
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=cmd_auth)

    s = sub.add_parser("specs", help="列出合约规格")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_specs)

    dd = sub.add_parser("data", help="行情缓存")
    dd.add_argument("action", choices=["status", "sync"])
    dd.add_argument("--pool", default="default")
    dd.add_argument("--live", action="store_true", help="从天勤拉取（需要快期账号）")
    dd.add_argument("--json", action="store_true")
    dd.set_defaults(func=cmd_data)

    b = sub.add_parser("backtest", help="跑回测")
    b.add_argument("--pool", default="default")
    b.add_argument("--start", default="2018-01-01")
    b.add_argument("--end", default="")
    b.add_argument("--capital", type=float, default=1_000_000.0)
    b.add_argument("--vol", type=float, default=0.10, help="组合目标年化波动")
    b.add_argument("--atr", type=int, default=20)
    b.add_argument("--slip", type=float, default=1.0, help="每笔滑点（跳）")
    b.add_argument("--leverage", type=float, default=1.5)
    b.add_argument("--dd-halt", dest="dd_halt", type=float, default=0.20)
    b.add_argument("--symbol-risk", dest="symbol_risk", type=float, default=0.02)
    b.add_argument("--stop-atr", dest="stop_atr", type=float, default=0.0)
    b.add_argument("--sleeves", default="", help="trend:0.6,xsec:0.4")
    b.add_argument("--live", action="store_true")
    b.add_argument("--refresh", action="store_true")
    b.add_argument("--sizing", choices=["vol_parity", "optimizer"], default="vol_parity",
                   help="vol_parity=用策略给的权重；optimizer=按最优配比分配风险")
    b.add_argument("--method", default="risk_parity",
                   choices=["equal_weight", "inverse_vol", "risk_parity", "min_variance", "mean_variance"])
    b.add_argument("--lookback", type=int, default=250)
    b.add_argument("--model-rebalance", dest="model_rebalance", type=int, default=20)
    b.add_argument("--max-weight", dest="max_weight", type=float, default=0.15)
    b.add_argument("--sector-cap", dest="sector_cap", type=float, default=0.35)
    b.add_argument("--model-min-bars", dest="model_min_bars", type=int, default=250)
    b.add_argument("--exclude-sectors", dest="exclude_sectors", default="")
    b.add_argument("--name", default="")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_backtest)

    op = sub.add_parser("optimize", help="解最优配比（不跑回测）")
    op.add_argument("--pool", default="all")
    op.add_argument("--method", default="risk_parity",
                    choices=["equal_weight", "inverse_vol", "risk_parity", "min_variance", "mean_variance"])
    op.add_argument("--lookback", type=int, default=250)
    op.add_argument("--min-bars", dest="min_bars", type=int, default=250)
    op.add_argument("--max-weight", dest="max_weight", type=float, default=0.15)
    op.add_argument("--sector-cap", dest="sector_cap", type=float, default=0.35)
    op.add_argument("--exclude-sectors", dest="exclude_sectors", default="")
    op.add_argument("--start", default="2016-01-01")
    op.add_argument("--end", default="")
    op.add_argument("--asof", default="", help="用截至哪一天的数据（空=全部）")
    op.add_argument("--live", action="store_true")
    op.add_argument("--refresh", action="store_true")
    op.add_argument("--json", action="store_true")
    op.set_defaults(func=cmd_optimize)

    r = sub.add_parser("runs", help="列出历史回测")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_runs)

    rp = sub.add_parser("report", help="打印报告")
    rp.add_argument("--run", default="")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_report)

    from . import paper as _paper

    pc = sub.add_parser("paper", help="模拟盘")
    pc.add_argument("action", choices=["init", "step", "run", "status", "reset"])
    pc.add_argument("--pool", default="starter")
    pc.add_argument("--capital", type=float, default=300_000.0)
    pc.add_argument("--vol", type=float, default=0.10)
    pc.add_argument("--sleeves", default="trend:0.7,xsec:0.3")
    pc.add_argument("--interval", type=int, default=60)
    pc.add_argument("--live", action="store_true")
    pc.add_argument("--max-steps", type=int, default=0)
    pc.add_argument("--start", default="2019-01-01")
    pc.add_argument("--sizing", choices=["vol_parity", "optimizer"], default="optimizer")
    pc.add_argument("--method", default="risk_parity",
                    choices=["equal_weight", "inverse_vol", "risk_parity", "min_variance", "mean_variance"])
    pc.add_argument("--lookback", type=int, default=250)
    pc.add_argument("--model-rebalance", dest="model_rebalance", type=int, default=20)
    pc.add_argument("--max-weight", dest="max_weight", type=float, default=0.15)
    pc.add_argument("--sector-cap", dest="sector_cap", type=float, default=0.35)
    pc.add_argument("--model-min-bars", dest="model_min_bars", type=int, default=250)
    pc.add_argument("--exclude-sectors", dest="exclude_sectors", default="")
    pc.add_argument("--slip", type=float, default=1.0)
    pc.add_argument("--leverage", type=float, default=1.0)
    pc.add_argument("--dd-halt", dest="dd_halt", type=float, default=0.20)
    pc.add_argument("--symbol-risk", dest="symbol_risk", type=float, default=0.02)
    pc.add_argument("--atr", type=int, default=20)
    pc.add_argument("--stop-atr", dest="stop_atr", type=float, default=0.0)
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=_paper.cmd_paper)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
