"""滚动模型：把「最优配比 + 参数估计」变成可增量更新的状态。

这是「对量化模型进行实时更新」的落点：

- 每 N 个交易日（或每次 paper 推进）用**最近 lookback 根**数据重新估计
  协方差与最优权重，产出新的 ModelState
- 状态带 version 与 asof，可落盘、可追溯；旧版本不覆盖，最新一份写
  state/model.json
- 回测里是 walk-forward（每个调仓日只用当日之前的数据），paper 里是
  每次推进重算 —— 两者共用同一个 RollingModel，口径一致

**注意**：这里更新的是「风险预算怎么分」，不是「策略方向」。
方向仍由策略决定，模型只回答「每个品种该分多少风险」。
"""

import datetime as _dt
import json
import os

from .optimizer import METHODS, optimize
from .specs import SPECS
from .universe import return_matrix, sector_map, select_universe


class RollingModel:
    def __init__(self, method="risk_parity", lookback=250, min_bars=250,
                 max_weight=0.15, sector_cap=0.35, exclude_sectors=(),
                 risk_aversion=2.0):
        if method not in METHODS:
            raise ValueError("未知优化方法 %r，可用：%s" % (method, ", ".join(METHODS)))
        self.method = method
        self.lookback = int(lookback)
        self.min_bars = int(min_bars)
        self.max_weight = float(max_weight)
        self.sector_cap = float(sector_cap)
        self.exclude_sectors = tuple(exclude_sectors or ())
        self.risk_aversion = float(risk_aversion)
        self.version = 0

    def update(self, bars_by_code, asof=""):
        """用截至 asof（含当日）的数据重算权重。返回 ModelState dict。"""
        sliced = {}
        for code, bars in bars_by_code.items():
            if asof:
                sliced[code] = [b for b in bars if b.date <= asof]
            else:
                sliced[code] = list(bars)

        codes, report = select_universe(
            sliced, min_bars=self.min_bars, exclude_sectors=self.exclude_sectors
        )
        if not codes:
            self.version += 1
            return {
                "version": self.version,
                "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "asof": asof,
                "method": self.method,
                "weights": {},
                "diagnostics": {"error": "没有品种满足条件", "universe": report},
            }

        matrix = return_matrix(sliced, codes, lookback=self.lookback)
        sectors = sector_map(codes)
        expected = None
        if self.method == "mean_variance":
            # 用样本均值日收益做预期收益（噪声大，仅作 demo；报告里会标明）
            expected = [
                (sum(series) / len(series)) if series else 0.0 for series in matrix
            ]
        res = optimize(
            self.method, matrix, codes, sectors,
            max_weight=self.max_weight, sector_cap=self.sector_cap,
            expected_returns=expected, risk_aversion=self.risk_aversion,
        )
        self.version += 1
        diag = dict(res["diagnostics"])
        diag["universe"] = report
        diag["lookback"] = self.lookback
        diag["max_weight_cap"] = self.max_weight
        diag["sector_cap"] = self.sector_cap
        if self.method == "mean_variance":
            diag["warning"] = "预期收益用的是样本均值，噪声很大；实盘请换成收缩估计或不用 mean_variance"
        return {
            "version": self.version,
            "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "asof": asof,
            "method": self.method,
            "weights": res["weights"],
            "diagnostics": diag,
        }


def model_path(root):
    return os.path.join(root, "state", "model.json")


def save_state(path, state, history_keep=20):
    """落盘最新状态，并保留一小段历史（便于看权重怎么漂移的）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = None
    history = []
    if isinstance(old, dict) and isinstance(old.get("history"), list):
        history = old["history"]
    history.append({
        "version": state.get("version"),
        "asof": state.get("asof"),
        "updated_at": state.get("updated_at"),
        "weights": state.get("weights", {}),
    })
    history = history[-history_keep:]
    payload = dict(state)
    payload["history"] = history
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_state(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
