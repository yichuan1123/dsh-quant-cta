"""统计体检：把回测结果翻译成「这套东西到底可不可信」。

一份回测给出年化 20%、夏普 1.5，不代表它有边缘。这个模块按顺序回答四个问题：

1. 样本够不够 —— MinTRL：要以 95% 置信声称夏普 > 0，至少要多少天
2. 夏普是不是噪声 —— PSR：扣掉偏度/峰度后的置信度；块自助法给区间
3. 试了多少次 —— DSR：把多重检验惩罚算进去
4. 风险长什么样 —— VaR/CVaR、GARCH 当前波动、回撤结构

再加两个结构检验：收益有没有自相关（Ljung-Box）、权益是不是随机游走（ADF / Hurst）。
"""

from . import bayes, risk, significance as sig, timeseries as ts

INF = float("inf")


def analyze(equity, dates, trades, n_trials=1, level=0.95):
    """equity/dates 等长；trades 为 ClosedTrade 列表（可为空）。"""
    rets = sig.daily_returns_from_equity(equity)
    n = len(rets)

    out = {
        "n_days": len(equity),
        "n_returns": n,
        "n_trades": len(trades),
    }
    if dates:
        out["start"] = dates[0]
        out["end"] = dates[-1]
    if n < 20:
        out["error"] = "样本不足（少于 20 个交易日），统计量没有意义"
        return out
    if all(abs(r) < 1e-12 for r in rets):
        out["error"] = "样本内日收益恒为 0（策略全程没有建仓），统计体检没有意义"
        return out

    try:
        _full_stats(out, equity, rets, trades, n_trials, level)
    except Exception as exc:      # 退化样本不该让整份报告生不出来
        return {"n_days": len(equity), "n_returns": n, "n_trades": len(trades),
                "error": "统计体检在 %d 个样本上算不出来（%s: %s）"
                         % (n, type(exc).__name__, exc)}
    return out


def _full_stats(out, equity, rets, trades, n_trials, level):
    n = len(rets)
    out["sharpe"] = sig.psr(rets)
    # 无穷用 None 表示：JSON 里 Infinity 不是合法字面量，会让前端 JSON.parse 直接失败
    m = sig.min_track_record_length(rets)
    out["mintrl"] = None if (m == INF or m > 10 ** 9) else m
    out["dsr"] = sig.deflated_sharpe(rets, n_trials=n_trials)
    out["sharpe_ci"] = sig.bootstrap_sharpe_ci(rets, n_boot=300, level=level, seed=7)
    out["cagr_ci"] = sig.bootstrap_cagr_ci(equity, n_boot=300, level=level, seed=7)
    out["t_test"] = sig.t_test_mean(rets)
    out["wilcoxon"] = sig.wilcoxon_signed_rank(rets)
    out["ljung_box"] = ts.ljung_box(rets, 10)
    out["autocorr1"] = ts.acf(rets, 1)[-1] if n > 3 else 0.0
    out["adf_equity"] = ts.adf(equity, max_lag=5)
    out["hurst"] = ts.hurst_dfa(rets)
    out["var"] = risk.var_report(rets, 0.95)
    out["garch"] = risk.garch11_fit(rets)
    out["underwater"] = risk.underwater_stats(equity)

    if trades:
        wins = sum(1 for t in trades if t.net_pnl > 0)
        out["win_rate_bayes"] = bayes.beta_binomial(wins, len(trades))
        out["prob_edge"] = bayes.prob_win_rate_above(wins, len(trades), 0.5)

    # 结论分档：只区分「有边缘 / 与噪声不可区分 / 稳定亏损」三种，不做程度评价
    ci = out["sharpe_ci"]
    if ci["lo"] > 0 and out["sharpe"]["psr"] >= 0.95:
        out["label"] = "有可辨识的边缘"
        out["label_level"] = "ok"
    elif ci["hi"] < 0:
        out["label"] = "稳定亏损（统计上确定是负的）"
        out["label_level"] = "bad"
    else:
        out["label"] = "与噪声不可区分（看不出正负）"
        out["label_level"] = "none"


def _pct(x, nd=2):
    return "—" if x is None else "{0:.{1}f}%".format(x * 100, nd)


def _lb_note(p, acf1):
    """大样本下 Ljung-Box 对极弱自相关也会显著，必须同时看一阶自相关的量级。"""
    if p >= 0.05:
        return "无自相关（接近白噪声）"
    if abs(acf1) < 0.05:
        return "统计显著但量级可忽略（大样本效应）"
    return "有可交易量级的自相关"


def _days(n):
    """把所需样本天数换成人类可读的量级（None = 无穷）。"""
    if n is None or n == INF:
        return "∞（不可达）"
    if n > 250 * 100:
        return "> 100 年（以当前夏普，样本上证明不了）"
    if n > 250:
        return "%d 天（约 %.1f 年）" % (n, n / 250.0)
    return "%d 天" % n


def render_markdown(v, n_trials=1):
    """把体检结果渲染成 markdown 行。"""
    L = []
    a = L.append
    a("## 统计体检")
    a("")
    if v.get("error"):
        a("> " + v["error"])
        a("")
        return L

    sh = v["sharpe"]
    mintrl = v["mintrl"]
    dsr = v["dsr"]
    ci = v["sharpe_ci"]
    cagr = v["cagr_ci"]
    lb = v["ljung_box"]
    adf = v["adf_equity"]
    hurst = v["hurst"]
    var = v["var"]
    g = v["garch"]
    uw = v["underwater"]
    n = v["n_returns"]

    # ── 结论先行 ──
    a("> **结论：%s**" % v.get("label", "—"))
    if mintrl is None or n < mintrl:
        a("> 样本不足：要以 95%% 置信声称夏普 > 0，需要约 %s，当前只有 %d 天。" % (_days(mintrl), n))
    ok_psr = sh["psr"] >= 0.95
    a("> 年化夏普 %.2f，真实夏普 > 0 的概率 **%.1f%%**%s"
      % (sh["sr_ann"], sh["psr"] * 100,
         "（通过 95% 门槛）" if ok_psr else "（未过 95% 门槛，不能排除是噪声）"))
    if n_trials > 1:
        a("> 计入试过 %d 次参数后的通缩夏普 DSR = **%.1f%%**%s"
          % (n_trials, dsr["dsr"] * 100,
             "（仍显著）" if dsr["dsr"] >= 0.95
             else "（**不再显著**：很可能只是挑出来的最好那个）"))
    a("")

    a("| 检验 | 结果 | 判读 |")
    a("|---|---|---|")
    a("| 年化夏普（点估计） | %.2f | — |" % sh["sr_ann"])
    a("| 夏普 95%% 置信区间（块自助） | [%.2f, %.2f] | %s |" % (
        ci["lo"], ci["hi"],
        "不含 0，方向站得住" if ci["lo"] > 0 else "**跨过 0，与噪声不可区分**"))
    a("| 年化收益 95%% 置信区间 | [%+.2f%%, %+.2f%%] | 中位 %+.2f%% |" % (
        cagr["lo"] * 100, cagr["hi"] * 100, cagr["point"] * 100))
    a("| PSR（概率性夏普） | %.1f%% | %s |" % (
        sh["psr"] * 100, "≥95% 可信" if ok_psr else "<95%，不显著"))
    a("| DSR（通缩夏普，N=%d） | %.1f%% | %s |" % (
        n_trials, dsr["dsr"] * 100,
        "扣掉挑选偏差后仍可信" if dsr["dsr"] >= 0.95 else "扣掉挑选偏差后不可信"))
    a("| MinTRL（所需样本） | %s | 当前 %d 天 |" % (_days(mintrl), n))
    a("| 偏度 / 超额峰度 | %+.2f / %+.2f | %s |" % (
        sh["skew"], sh["kurt"],
        "肥尾，正态假设会低估风险" if sh["kurt"] > 1 else "接近正态"))
    a("| 日收益 t 检验 | p = %.3g | %s |" % (
        v["t_test"]["p"], "均值显著不为 0" if v["t_test"]["p"] < 0.05 else "均值与 0 无显著差异"))
    a("| 符号秩检验 | p = %.3g | %s |" % (
        v["wilcoxon"]["p"], "显著" if v["wilcoxon"]["p"] < 0.05 else "不显著"))
    a("| Ljung-Box（收益自相关 q=%.1f, p=%.3f） | 一阶自相关 %+.3f | %s |" % (
        lb["q"], lb["p"], v.get("autocorr1", 0.0),
        _lb_note(lb["p"], v.get("autocorr1", 0.0))))
    if adf.get("insufficient"):
        a("| ADF（权益序列） | 样本不足（< 30 天） | 不检验 |")
    else:
        a("| ADF（权益序列） | %.2f（5%% 临界 %.2f） | %s |" % (
            adf["stat"], adf["crit"]["5%"],
            "拒绝单位根，有均值回归" if adf["reject"] else "不能拒绝单位根，像随机游走"))
    a("| Hurst（收益 DFA） | %.3f | %s |" % (
        hurst, "趋势持续（动量可能有效）" if hurst > 0.55
        else ("均值回归（反转可能有效）" if hurst < 0.45 else "接近随机游走")))
    a("")
    a("### 风险结构")
    a("")
    a("| 指标 | 值 |")
    a("|---|---|")
    a("| 95%% VaR（历史 / 正态 / CF 修正） | %s / %s / %s |" % (
        _pct(var["historical"]), _pct(var["parametric"]), _pct(var["cornish_fisher"])))
    a("| 95%% CVaR（尾部期望损失） | %s |" % _pct(var["cvar_historical"]))
    if g.get("ok"):
        a("| GARCH 长期波动 / 当前波动 | %s / %s |" % (
            _pct(g["long_run_vol_ann"]), _pct(g["next_vol_ann"])))
        a("| GARCH 持续性 α+β | %.3f |" % g["persistence"])
    a("| 回撤次数 / 最深 / 平均 | %d / %s / %s |" % (
        uw["episodes"], _pct(uw["max_depth"]), _pct(uw["avg_depth"])))
    a("| 最长水下交易日 | %d |" % uw["longest_underwater_days"])
    if "win_rate_bayes" in v:
        wb = v["win_rate_bayes"]
        a("| 胜率后验（Beta-Binomial） | %s，95%% 区间 [%s, %s] |" % (
            _pct(wb["posterior_mean"], 1), _pct(wb["ci95"][0], 1), _pct(wb["ci95"][1], 1)))
        a("| P(真实胜率 > 50%%) | %.1f%% |" % (v["prob_edge"] * 100))
    a("")
    a("> 统计体检只回答「这个结果能不能被噪声解释」，不回答「值不值得做」。"
      "样本不足或置信区间跨 0 时，任何优化都是在拟合噪声。")
    a("")
    return L
