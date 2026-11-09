"""合约规格表。

数字来源：各交易所公开合约细则的**近似值**，用于回测的成本/保证金建模。
手续费、保证金率会随交易所公告调整，**下单前必须以最新公告为准**。
每张表都带 verified 标记：False 表示未经对照当日公告核验。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    code: str            # 品种代码，如 rb
    name: str            # 中文名
    exchange: str        # SHFE / DCE / CZCE / INE / CFFEX
    multiplier: float    # 合约乘数（元/点 → 每手价值 = 价格 × multiplier）
    tick: float          # 最小变动价位（价格单位）
    margin_rate: float   # 交易所保证金率（期货公司另加 2~5%）
    fee_rate: float      # 按成交额计的手续费率
    fee_per_lot: float   # 按手数计的固定手续费（元/手）
    close_today_mult: float  # 平今仓手续费倍率
    sector: str
    base_price: float = 0.0  # 仅合成数据用
    verified: bool = False

    @property
    def tick_value(self) -> float:
        """一跳的价值（元）。滑点按跳计，所以这是所有成本计算的基准。"""
        return self.multiplier * self.tick

    @property
    def notional(self) -> float:
        return self.multiplier * self.base_price

    def tq_symbol(self) -> str:
        """天勤主连代码。KQ.m@ 为加权主连（可交易口径）。"""
        return "KQ.m@" + self.exchange + "." + self.code

    def commission(self, lots: int, price: float, close_today: bool = False) -> float:
        by_rate = self.fee_rate * price * self.multiplier * lots
        by_lot = self.fee_per_lot * lots
        fee = max(by_rate, by_lot)
        if close_today and self.close_today_mult > 1.0:
            fee *= self.close_today_mult
        return float(fee)

    def margin(self, lots: int, price: float, buffer: float = 1.35) -> float:
        """占用保证金。buffer 模拟期货公司在交易所比例上的加收。"""
        return float(lots * price * self.multiplier * self.margin_rate * buffer)


# 说明：base_price 只服务于「无凭据时的合成数据」，让合成价格量级接近真实。
SPECS = {
    # ── 上期所 ───────────────────────────────────────────
    "rb": ContractSpec("rb", "螺纹钢", "SHFE", 10, 1, 0.09, 0.0001, 0, 1.0, "黑色", 3600),
    "hc": ContractSpec("hc", "热卷", "SHFE", 10, 1, 0.09, 0.0001, 0, 1.0, "黑色", 3700),
    "cu": ContractSpec("cu", "沪铜", "SHFE", 5, 10, 0.10, 0.00005, 0, 1.0, "有色", 72000),
    "al": ContractSpec("al", "沪铝", "SHFE", 5, 5, 0.10, 0.00006, 3, 1.0, "有色", 19500),
    "zn": ContractSpec("zn", "沪锌", "SHFE", 5, 5, 0.10, 0.00006, 0, 1.0, "有色", 23000),
    "ni": ContractSpec("ni", "沪镍", "SHFE", 1, 10, 0.12, 0.00006, 3, 1.0, "有色", 130000),
    "ru": ContractSpec("ru", "橡胶", "SHFE", 10, 5, 0.10, 0.00005, 0, 1.0, "能化", 14000),
    "au": ContractSpec("au", "沪金", "SHFE", 1000, 0.02, 0.10, 0.0001, 10, 1.0, "贵金属", 560),
    "ag": ContractSpec("ag", "沪银", "SHFE", 15, 1, 0.12, 0.00005, 0, 1.0, "贵金属", 7500),
    # ── 大商所 ───────────────────────────────────────────
    "i": ContractSpec("i", "铁矿石", "DCE", 100, 0.5, 0.12, 0.0001, 0, 1.0, "黑色", 800),
    "j": ContractSpec("j", "焦炭", "DCE", 100, 0.5, 0.12, 0.0001, 0, 1.4, "黑色", 2200),
    "jm": ContractSpec("jm", "焦煤", "DCE", 60, 0.5, 0.12, 0.0001, 0, 1.4, "黑色", 1600),
    "m": ContractSpec("m", "豆粕", "DCE", 10, 1, 0.09, 0.0, 1.5, 1.0, "农产品", 3100),
    "y": ContractSpec("y", "豆油", "DCE", 10, 2, 0.09, 0.0, 2.5, 1.0, "农产品", 8200),
    "p": ContractSpec("p", "棕榈油", "DCE", 10, 2, 0.09, 0.0, 2.5, 1.0, "农产品", 7800),
    "c": ContractSpec("c", "玉米", "DCE", 10, 1, 0.08, 0.0, 1.2, 1.0, "农产品", 2400),
    "v": ContractSpec("v", "PVC", "DCE", 5, 1, 0.09, 0.0001, 0, 1.0, "能化", 6200),
    # ── 郑商所 ───────────────────────────────────────────
    "TA": ContractSpec("TA", "PTA", "CZCE", 5, 2, 0.08, 0.0, 3.0, 1.0, "能化", 5600),
    "MA": ContractSpec("MA", "甲醇", "CZCE", 10, 1, 0.09, 0.0, 2.0, 1.0, "能化", 2500),
    "SR": ContractSpec("SR", "白糖", "CZCE", 10, 1, 0.07, 0.0, 3.0, 1.0, "农产品", 6000),
    "CF": ContractSpec("CF", "棉花", "CZCE", 5, 5, 0.09, 0.0, 4.3, 1.0, "农产品", 15000),
    "FG": ContractSpec("FG", "玻璃", "CZCE", 20, 1, 0.09, 0.0, 6.0, 1.0, "能化", 1600),
    # ── 能源中心 ─────────────────────────────────────────
    "sc": ContractSpec("sc", "原油", "INE", 1000, 0.1, 0.12, 0.0, 20.0, 1.0, "能化", 520),
    # ── 中金所（平今费显著更高，日频策略应避免日内进出） ──────
    "IF": ContractSpec("IF", "沪深300", "CFFEX", 300, 0.2, 0.12, 0.000023, 0, 10.0, "金融", 3900),
    "IC": ContractSpec("IC", "中证500", "CFFEX", 200, 0.2, 0.14, 0.000023, 0, 10.0, "金融", 5600),
    "IH": ContractSpec("IH", "上证50", "CFFEX", 300, 0.2, 0.12, 0.000023, 0, 10.0, "金融", 2700),
    "IM": ContractSpec("IM", "中证1000", "CFFEX", 200, 0.2, 0.15, 0.000023, 0, 10.0, "金融", 6000),
    "T": ContractSpec("T", "十年国债", "CFFEX", 10000, 0.005, 0.02, 0.0, 3.0, 1.0, "金融", 105),
}

# 默认品种池：覆盖 5 大板块，流动性充足，有较长历史。
DEFAULT_POOL = ["rb", "i", "cu", "al", "zn", "m", "y", "p", "TA", "MA", "SR", "CF", "ru", "c", "v", "FG"]

# 最小可用池：资金小、先跑通流程时用。
STARTER_POOL = ["rb", "i", "cu", "m", "y", "TA"]


def get_spec(code: str) -> ContractSpec:
    if code not in SPECS:
        raise KeyError("未知品种代码 %r。可用：%s" % (code, ", ".join(sorted(SPECS))))
    return SPECS[code]


def resolve_pool(names) -> list:
    """把 'default' / 'starter' / 逗号分隔列表 解析为品种代码列表。"""
    if not names:
        return list(DEFAULT_POOL)
    if isinstance(names, str):
        items = [p.strip() for p in names.split(",") if p.strip()]
        if not items:
            return list(DEFAULT_POOL)
        if len(items) == 1 and items[0] == "default":
            return list(DEFAULT_POOL)
        if len(items) == 1 and items[0] == "all":
            return sorted(SPECS.keys())
        if len(items) == 1 and items[0] == "starter":
            return list(STARTER_POOL)
        return items
    return list(names)
