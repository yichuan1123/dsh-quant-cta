"""数据层：命令行/回测共用的 K 线来源。

三个来源，优先级从高到低：
1. 本地 CSV 缓存（data/cache/<code>.csv）—— 离线可复现
2. TqSdk 真实行情（需快期账号，见 credentials）
3. 确定性合成数据 —— 无凭据时的兜底，**结果里一定会标注 synthetic**

合成数据存在的理由：回测框架、撮合、成本模型必须先在没有网络/账号时
也能被验证。它绝不是"假行情代替真行情"，报告里会显著标明来源。
"""

import csv
import datetime as _dt
import hashlib
import json
import math
import os
import random
import time as _time

from .secrets import SCHEME_DPAPI, SCHEME_PLAIN, protect, unprotect
from .specs import ContractSpec, get_spec
from .types import Bar

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data", "cache")
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".config")


def cache_dir() -> str:
    d = os.path.abspath(os.environ.get("QCTA_DATA_DIR") or CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def cache_path(code: str) -> str:
    return os.path.join(cache_dir(), code + ".csv")


def cache_meta_path(code: str) -> str:
    return os.path.join(cache_dir(), code + ".meta.json")


def cache_meta(code: str):
    """读缓存来源元信息（source/adj/synced_at）；没有就返回 None。"""
    p = cache_meta_path(code)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# ───────────────────────────── 缓存读写 ─────────────────────────────

def save_cache(code: str, bars, source: str = "unknown", adj=None) -> str:
    path = cache_path(code)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume", "open_interest"])
        for b in bars:
            w.writerow([b.date, _fmt(b.open), _fmt(b.high), _fmt(b.low), _fmt(b.close), b.volume, b.open_interest])
    os.replace(tmp, path)
    # 记下来源：面板/报告据此区分「天勤真实数据」与「本地缓存」，
    # 而不是永远显示 cache 让人以为没接真实行情。
    try:
        meta = {
            "code": code,
            "source": source,
            "adj": adj,
            "bars": len(bars),
            "first": bars[0].date if bars else "",
            "last": bars[-1].date if bars else "",
            "synced_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        mp = cache_meta_path(code)
        with open(mp + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        os.replace(mp + ".tmp", mp)
    except OSError:
        pass
    return path


def _fmt(x: float) -> str:
    return ("%.6f" % x).rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def load_cache(code: str):
    path = cache_path(code)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(
                    Bar(
                        date=row["date"],
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume") or 0),
                        open_interest=float(row.get("open_interest") or 0),
                    )
                )
            except (KeyError, ValueError):
                continue
    out.sort(key=lambda b: b.date)
    return out


def slice_bars(bars, start: str = "", end: str = ""):
    lo = start or "0000-00-00"
    hi = end or "9999-99-99"
    return [b for b in bars if lo <= b.date <= hi]


# ───────────────────────────── 凭据 ─────────────────────────────

def credentials_path() -> str:
    """凭据文件位置。QCTA_CRED_PATH 可覆盖（测试与多账户场景用）。"""
    override = os.environ.get("QCTA_CRED_PATH")
    if override:
        return os.path.abspath(override)
    return os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "credentials.json"))


def load_credentials():
    """返回 (user, password) 或 None。来源优先级：环境变量 > credentials.json。"""
    user = os.environ.get("QCTA_TQ_USER") or os.environ.get("TQ_USER")
    pwd = os.environ.get("QCTA_TQ_PASS") or os.environ.get("TQ_PASS")
    if user and pwd:
        return (user, pwd)
    path = credentials_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            u = obj.get("tq_user") or obj.get("user")
            if not u:
                return None
            enc = obj.get("enc")
            if obj.get("tq_pass_enc"):
                p = unprotect(obj["tq_pass_enc"], enc or "dpapi-v1")
            else:
                p = obj.get("tq_pass") or obj.get("password")
            if p:
                return (str(u), str(p))
        except Exception:
            return None
    return None


def mask_user(user: str) -> str:
    """脱敏：只露前 3 位与后 2 位。任何对外输出都必须走这里。"""
    u = str(user or "")
    if len(u) <= 5:
        return "*" * len(u)
    return u[:3] + "*" * (len(u) - 5) + u[-2:]


def credentials_status() -> dict:
    """凭据状态。

    **绝不返回密码**。账号（user）会明文返回，因为面板要回填输入框——
    这个接口只在本机回环 + 同源下可访问（宿主端有 isLocalRequest 校验）；
    需要展示给人看的场景一律用 masked_user。
    """
    env_user = os.environ.get("QCTA_TQ_USER") or os.environ.get("TQ_USER")
    env_pass = os.environ.get("QCTA_TQ_PASS") or os.environ.get("TQ_PASS")
    path = credentials_path()
    file_exists = os.path.exists(path)
    file_user = None
    storage = None
    if file_exists:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            file_user = obj.get("tq_user") or obj.get("user")
            storage = obj.get("enc") or (SCHEME_PLAIN if obj.get("tq_pass") else None)
        except Exception:
            file_user = None

    if env_user and env_pass:
        source, user = "env", env_user
    elif file_user:
        source, user = "file", file_user
    else:
        source, user = None, None

    return {
        "configured": bool(source),
        "source": source,
        "masked_user": mask_user(user) if user else "",
        # 面板要用它回填账号输入框（不回填密码）。只在本机回环+同源下可取。
        "user": user or "",
        "storage": storage if source == "file" else ("env" if source == "env" else None),
        "path": path,
        "file_exists": file_exists,
        "env_present": bool(env_user and env_pass),
    }


def save_credentials(user: str, password: str) -> dict:
    """把账号密码写到本机文件（gitignore 覆盖）。密码不落日志、不回显。"""
    u = str(user or "").strip()
    p = str(password or "")
    if not u or not p:
        raise ValueError("账号和密码都不能为空")
    path = credentials_path()
    stored, scheme = protect(p)
    payload = {
        "tq_user": u,
        "enc": scheme,
        "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    if scheme == SCHEME_DPAPI:
        payload["tq_pass_enc"] = stored
    else:
        payload["tq_pass"] = stored
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError as exc:
        # 常见原因：路径在工作区之外被沙箱拒绝、或目录只读。
        # 抛 ValueError，让 CLI 把它变成一句可读的 error，而不是 traceback。
        raise ValueError("无法写入凭据文件 %s：%s（可把 QCTA_CRED_PATH 指到工作区内）" % (path, exc))
    return credentials_status()


def clear_credentials() -> dict:
    path = credentials_path()
    removed = False
    if os.path.exists(path):
        os.remove(path)
        removed = True
    status = credentials_status()
    status["removed"] = removed
    return status


def probe_credentials(user: str = None, password: str = None, timeout_s: int = 20) -> dict:
    """真连一次天勤，验证账号密码可用。不保存任何东西。"""
    if user is None or password is None:
        cred = load_credentials()
        if not cred:
            return {"ok": False, "error": "还没有保存账号密码"}
        user, password = cred
    try:
        from tqsdk import TqApi, TqAuth  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": "未安装 tqsdk：" + str(exc)}
    import time as _time

    api = None
    try:
        api = TqApi(auth=TqAuth(user, password))
        quote = api.get_quote("KQ.m@SHFE.rb")
        deadline = _time.time() + timeout_s
        last = None
        while _time.time() < deadline:
            # 每次只等 1 秒：wait_update 不带 deadline 时可能长时间阻塞，
            # 而外层是 UI 请求，必须能被超时兜住。
            try:
                api.wait_update(deadline=min(deadline, _time.time() + 1.0))
            except TypeError:
                api.wait_update()
            try:
                v = float(quote.last_price)
            except Exception:
                v = None
            if v is not None and v > 0:
                last = v
                break
        if last is None:
            return {"ok": False, "error": "连上了但 %d 秒内没拿到行情，可能是账号无权限或网络受限" % timeout_s}
        return {
            "ok": True,
            "symbol": "KQ.m@SHFE.rb",
            "last_price": last,
            "masked_user": mask_user(user),
            "checked_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as exc:
        msg = str(exc)
        hint = ""
        if "auth" in msg.lower() or "password" in msg.lower() or "登录" in msg:
            hint = "（账号或密码不对）"
        return {"ok": False, "error": msg[:400] + hint}
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass


# ───────────────────────────── 交易日历（合成数据用） ─────────────────────────────

_HOLIDAYS = set()


def _build_holidays(years):
    """粗略节假日：元旦、劳动节、国庆。用于合成数据的交易日序列。"""
    for y in years:
        for m, d in ((1, 1), (5, 1), (5, 2), (5, 3)):
            _HOLIDAYS.add("%04d-%02d-%02d" % (y, m, d))
        for d in range(1, 8):
            _HOLIDAYS.add("%04d-10-%02d" % (y, d))


def trading_days(start: str, end: str):
    y0 = int(start[:4])
    y1 = int(end[:4])
    if not _HOLIDAYS:
        _build_holidays(range(y0 - 1, y1 + 2))
    cur = _dt.date.fromisoformat(start)
    last = _dt.date.fromisoformat(end)
    out = []
    while cur <= last:
        if cur.weekday() < 5:
            iso = cur.isoformat()
            if iso not in _HOLIDAYS:
                out.append(iso)
        cur += _dt.timedelta(days=1)
    return out


# ───────────────────────────── 合成数据源 ─────────────────────────────

_SECTOR_VOL = {
    "黑色": 0.014,
    "有色": 0.012,
    "能化": 0.016,
    "农产品": 0.010,
    "贵金属": 0.011,
    "金融": 0.013,
}


def _round_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return x
    return round(round(x / tick) * tick, 8)


def synthetic_bars(code: str, start: str = "2018-01-01", end: str = "", spec: ContractSpec = None):
    """确定性合成日线：按品种代码哈希取种子，分段趋势 + 波动聚集。

    同一 code + 同一日期区间 → 永远同一组数据（便于测试与复现）。
    """
    spec = spec or get_spec(code)
    if not end:
        end = _dt.date.today().isoformat()
    seed = int(hashlib.sha256(("qcta:" + code).encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)

    days = trading_days(start, end)
    if not days:
        return []

    price = float(spec.base_price) or 1000.0
    vol_base = _SECTOR_VOL.get(spec.sector, 0.013)
    bars = []
    i = 0
    n = len(days)
    while i < n:
        seg_len = rng.randint(25, 110)
        drift = rng.gauss(0.0, 0.0022)
        vol = vol_base * rng.uniform(0.65, 1.7)
        for _ in range(seg_len):
            if i >= n:
                break
            o = price * (1.0 + rng.gauss(0.0, vol * 0.22))
            c = price * (1.0 + drift + rng.gauss(0.0, vol))
            hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol * 0.45)))
            lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol * 0.45)))
            o, hi, lo, c = (_round_tick(x, spec.tick) for x in (o, hi, lo, c))
            if lo <= 0 or c <= 0:
                c = max(spec.tick * 10, price)
                o = c
                hi = c
                lo = c
            bars.append(
                Bar(
                    date=days[i],
                    open=o,
                    high=max(hi, o, c),
                    low=min(lo, o, c),
                    close=c,
                    volume=float(int(rng.lognormvariate(11.0, 0.5))),
                    open_interest=float(int(rng.lognormvariate(11.5, 0.4))),
                )
            )
            price = c
            i += 1
    return bars


# ───────────────────────────── TqSdk 真实数据源 ─────────────────────────────

class TqSdkError(RuntimeError):
    pass


def _serial_to_bars(serial):
    """把天勤的 K 线 DataFrame 转成 Bar 列表（跳过 NaN / 无效行）。"""
    rows = []
    for _, r in serial.iterrows():
        ns = r.get("datetime")
        if ns is None or (isinstance(ns, float) and math.isnan(ns)):
            continue
        ts = _dt.datetime.fromtimestamp(int(ns) / 1e9, tz=_dt.timezone(_dt.timedelta(hours=8)))
        o = float(r["open"]); h = float(r["high"]); l = float(r["low"]); c = float(r["close"])
        if any(math.isnan(x) for x in (o, h, l, c)) or o <= 0:
            continue
        rows.append(
            Bar(
                date=ts.date().isoformat(),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=float(r.get("volume", 0) or 0),
                open_interest=float(r.get("open_oi", r.get("close_oi", 0)) or 0),
            )
        )
    rows.sort(key=lambda b: b.date)
    return rows


def _require_credentials():
    cred = load_credentials()
    if not cred:
        raise TqSdkError(
            "缺少快期账号凭据。请设置环境变量 QCTA_TQ_USER / QCTA_TQ_PASS，"
            "或在 " + credentials_path() + " 写入 {\"tq_user\":\"..\",\"tq_pass\":\"..\"}。"
        )
    return cred


def _adj_candidates():
    # 复权口径因 tqsdk 版本而异：
    #   3.x 只认 None / 'F'(前复权) / 'B'(后复权)；老教程里的 'BFD' 会被拒。
    # 主连（KQ.m@）本身已处理换月，前复权能让最新价等于当前真实价，
    # 保证金/手数换算才准，所以优先 'F'。
    return ("F", "B", None)


PROXY_URL = "http://127.0.0.1:7897"


def _tqapi_with_timeout(TqApi, TqAuth, cred, timeout_s: float = 45.0):
    """在后台线程里建连，超时就放弃这次尝试。

    `TqApi()` 内部要建 WebSocket；网络半通时它会**无限期**阻塞（实测卡过 6 分钟没动静），
    重试策略形同虚设。用线程 + join 超时把它变成可放弃的一次尝试。
    """
    import threading

    box = {}

    def work():
        try:
            box["api"] = TqApi(auth=TqAuth(cred[0], cred[1]))
        except BaseException as exc:      # 线程里只能自己兜住
            box["err"] = exc

    th = threading.Thread(target=work, name="tqapi-open", daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        raise TqSdkError("建立天勤连接超时（%.0fs 未完成）" % timeout_s)
    if "err" in box:
        raise box["err"]
    return box["api"]


def open_api(cred=None, attempts: int = 8):
    """开一个 TqApi，带重试。

    天勤的登录请求偶尔会被服务端直接掐断 TLS（SSLError: EOF occurred in violation
    of protocol），而 tqsdk 自己不重试——一次瞬断就整批拉取失败。这里交替「直连 / 本地代理」
    重试，并把最后一次异常原样抛出。
    """
    cred = cred or _require_credentials()
    try:
        from tqsdk import TqApi, TqAuth  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise TqSdkError("未安装 tqsdk（pip install tqsdk）：%s" % exc)

    saved = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY")}
    last = None
    try:
        for i in range(attempts):
            # 实测：到 auth.shinnytech.com 的 TLS 握手丢包严重（直连约 3/8 通、走本地代理约 5/8 通），
            # 而且失败是成簇的。所以交替两条路径 + 递增退避 + 随机抖动，别在同一个坏窗口里死磕。
            use_proxy = i % 2 == 0
            if use_proxy:
                os.environ["HTTPS_PROXY"] = PROXY_URL
                os.environ["HTTP_PROXY"] = PROXY_URL
            else:
                os.environ.pop("HTTPS_PROXY", None)
                os.environ.pop("HTTP_PROXY", None)
            try:
                api = _tqapi_with_timeout(TqApi, TqAuth, cred, timeout_s=25)
                return api
            except Exception as exc:      # 网络层异常类型很多，统一兜住重试
                last = exc
                _time.sleep(0.8 + i * 0.6 + random.random() * 0.7)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    raise TqSdkError("天勤登录连续失败 %d 次：%s" % (attempts, last))


def fetch_tqsdk_many(codes, data_length: int = 3000):
    """**一次登录**拉多个品种的主连日线。

    为什么不是每个品种各开一个 TqApi：登录+订阅是重操作，逐品种登录在 28 个品种上
    要跑十几分钟；一次会话批量订阅只需几秒。

    返回 {code: {"bars": [...], "adj": "F"}}，失败品种带 "error"。
    """
    cred = _require_credentials()
    api = open_api(cred)
    out = {}
    try:
        serials = {}
        adjs = {}
        for code in codes:
            spec = get_spec(code)
            sym = spec.tq_symbol()
            last_err = None
            for adj in _adj_candidates():
                try:
                    serials[code] = api.get_kline_serial(sym, 86400, data_length=data_length, adj_type=adj)
                    adjs[code] = adj
                    break
                except Exception as exc:  # 不同版本抛的异常类型不一致，统一兜住
                    last_err = exc
                    continue
            if code not in serials:
                out[code] = {"error": "拉取失败：%s" % last_err}

        # 重要：get_kline_serial 返回时历史通常**已经带回**（实测 data_length 根），
        # 而 wait_update() 等的是「下一次变化」——行情静默时它会无限期干等，
        # 这就是之前「批量拉取卡住」的原因。所以这里只做有界的补充等待。
        budget = _time.time() + 15.0
        while serials and _time.time() < budget:
            if all(len(s) > 0 for s in serials.values()):
                break
            try:
                api.wait_update(deadline=min(budget, _time.time() + 1.0))
            except TypeError:      # 老版本不支持 deadline
                break

        for code, serial in serials.items():
            bars = _serial_to_bars(serial)
            if not bars:
                out[code] = {"error": "天勤返回空数据（品种 %s）" % code}
            else:
                out[code] = {"bars": bars, "adj": adjs.get(code)}
    finally:
        try:
            api.close()
        except Exception:
            pass
    return out


def fetch_tqsdk_series(tq_symbols, period_seconds: int = 86400, data_length: int = 3000, adj="F"):
    """订阅任意天勤代码（合约、指数、分钟线），返回 {symbol: {"bars": [...], "error": ...}}。

    `fetch_tqsdk_many` 是「按品种池拉主连日线」的专用路径；这个是通用口，
    期限结构（具体合约）和滑点估计（分钟线）都走它。
    """
    cred = _require_credentials()
    api = open_api(cred)
    out = {}
    try:
        serials = {}
        for sym in tq_symbols:
            try:
                serials[sym] = api.get_kline_serial(sym, period_seconds,
                                                    data_length=data_length, adj_type=adj)
            except TypeError:
                serials[sym] = api.get_kline_serial(sym, period_seconds, data_length=data_length)
            except Exception as exc:
                out[sym] = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:100])}
        budget = _time.time() + 20.0
        while serials and _time.time() < budget:
            if all(len(s) > 0 for s in serials.values()):
                break
            try:
                api.wait_update(deadline=min(budget, _time.time() + 1.0))
            except TypeError:
                break
        for sym, serial in serials.items():
            bars = _serial_to_bars(serial)
            out[sym] = {"bars": bars} if bars else {"error": "空数据"}
    finally:
        try:
            api.close()
        except Exception:
            pass
    return out


def fetch_tqsdk(code: str, data_length: int = 3000, spec: ContractSpec = None):
    """单个品种（内部走批量接口，只开一次会话）。返回 (bars, adj)。"""
    res = fetch_tqsdk_many([code], data_length=data_length).get(code) or {}
    if "error" in res:
        raise TqSdkError(res["error"])
    return res["bars"], res.get("adj")


# ───────────────────────────── 统一入口 ─────────────────────────────

class DataBundle:
    """一次回测用到的全部行情 + 来源元信息。"""

    def __init__(self, bars_by_code, source: str, notes=None):
        self.bars_by_code = bars_by_code
        self.source = source
        self.notes = list(notes or [])

    @property
    def dates(self):
        s = set()
        for bars in self.bars_by_code.values():
            s.update(b.date for b in bars)
        return sorted(s)

    def coverage(self):
        out = {}
        for code, bars in self.bars_by_code.items():
            out[code] = {
                "bars": len(bars),
                "first": bars[0].date if bars else "",
                "last": bars[-1].date if bars else "",
            }
        return out


def load_bundle(codes, start: str, end: str, prefer_live: bool = False, refresh: bool = False) -> DataBundle:
    """按 缓存 → 天勤 → 合成 的顺序取数，并把实际来源记下来。"""
    bars_by_code = {}
    notes = []
    sources = set()

    for code in codes:
        spec = get_spec(code)
        cached = load_cache(code)
        if cached and not refresh:
            meta = cache_meta(code) or {}
            # 缓存若是从天勤同步来的，来源就按 tqsdk 记——否则报告永远写 cache，
            # 看不出到底用的是不是真实行情。
            src = "tqsdk" if meta.get("source") == "tqsdk" else "cache"
            sources.add(src)
            if meta.get("synced_at"):
                notes.append("%s: %s 数据（%s 同步，%s 根，adj=%s）" % (
                    code, "天勤" if src == "tqsdk" else "本地缓存", meta["synced_at"],
                    meta.get("bars", len(cached)), meta.get("adj")))
            bars_by_code[code] = slice_bars(cached, start, end)
            continue

        bars = []
        src = None
        if prefer_live:
            try:
                bars, adj = fetch_tqsdk(code, spec=spec)
                save_cache(code, bars)
                notes.append("%s: 天勤实时拉取 %d 根，adj_type=%s" % (code, len(bars), adj))
                src = "tqsdk"
            except TqSdkError as exc:
                notes.append("%s: 天勤不可用（%s）" % (code, exc))
        # refresh 但没开 live / 或 live 失败：有缓存就必须用缓存，
        # 绝不能因为「刷新」把真实数据静默换成合成数据。
        if not bars and cached:
            bars = cached
            src = "cache"
            notes.append("%s: 使用本地缓存 %d 根" % (code, len(cached)))
        if not bars:
            bars = synthetic_bars(code, "2016-01-01", end or _dt.date.today().isoformat(), spec)
            src = "synthetic"
        bars_by_code[code] = slice_bars(bars, start, end)
        sources.add(src)

    if "synthetic" in sources:
        source = "synthetic" if sources == {"synthetic"} else "mixed"
        notes.append("警告：本次结果包含合成数据，只能用于验证流程，不可当作真实行情结论。")
    elif source_priority(sources) == "tqsdk":
        source = "tqsdk"
    else:
        source = "cache"
    return DataBundle(bars_by_code, source, notes)


def source_priority(sources):
    if "tqsdk" in sources:
        return "tqsdk"
    return "cache"
