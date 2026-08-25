#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 端到端生成「股价创52周新低」HTML 日报（A股 + 美股 + 港股 三 tab）：
#   1) A股：本地全量枚举代码 + gtimg 行情（前复权52周低 f[68]）；
#      美股/港股：universe 代码来自每周自动化任务产出的全市场清单
#      (cloned_repo/assets/us_stocks.json, hk_stocks.json)，行情经 gtimg 刷新。
#   2) 主口径：收盘价距52周最低 ≤ 5%；生成带 tab 切换的可交互 HTML 报告到本脚本同目录。
# 用法：python3 generate_52week_low.py            # 每日：复用已同步的 universe，仅刷新行情
#       python3 generate_52week_low.py --weekly   # 每周：强制同步 universe 后刷新行情
import json, datetime, os, subprocess, sys, re, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW_A  = os.path.join(DATA, "near_a_raw.json")   # A股：收盘接近≤5%
RAW_US = os.path.join(DATA, "near_us_raw.json")  # 美股：收盘接近≤5%
RAW_HK = os.path.join(DATA, "near_hk_raw.json")  # 港股：收盘接近≤5%
SNAP   = os.path.join(DATA, "snapshot.json")     # 给本地服务用的 JSON 快照

# 全市场代码清单（来自每周自动化任务 "每周更新股票基金JSON列表数据"，每周日16:00 写入）
# 美股/港股候选池冻结根因：8-17 westock-tool 被删除后不再生成 universe，
# near_us_raw.json/near_hk_raw.json 停留在 8-17 快照。改用下列清单解冻候选池。
# 可用环境变量 UNIVERSE_DIR 覆盖 cloned_repo 路径（默认指向该自动化任务的 cloned_repo/assets）。
UNIVERSE_DIR = os.environ.get("UNIVERSE_DIR",
    "/Users/green/WorkBuddy/2026-07-28-10-26-17/cloned_repo/assets")
US_UNIVERSE_JSON = os.path.join(UNIVERSE_DIR, "us_stocks.json")   # 美股全量清单
HK_UNIVERSE_JSON = os.path.join(UNIVERSE_DIR, "hk_stocks.json")  # 港股全量清单
# 每周同步后的 universe 代码（gtimg 格式 usXXX / hkXXXXX），每日刷新行情时读取，避免每日重扫全市场
US_CODES = os.path.join(DATA, "us_codes.json")
HK_CODES = os.path.join(DATA, "hk_codes.json")
OUT_INDEX = os.path.join(HERE, "index.html")     # 供 Vercel / 静态托管使用的默认入口页
OUT_CN = os.path.join(HERE, "股价创52周新低.html")  # 中文名入口（中性，含双市场）

# 托管运行时路径（隔离、稳定）
NODE = "/Users/green/.workbuddy/binaries/node/versions/22.22.2/bin/node"
WSTOOL_DIR = "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/builtin-skills/westock-tool/scripts"

# 主口径：收盘价距 52周最低 ≤ 5%
EXPR = "intersect([LowPrice > 0, ClosePrice <= Week52Low * 1.05, TotalMV > 0])"
# A股粗筛：fetch_one 已用 f[68](前复权) 作为 Week52Low，数据正确，
# 故粗筛可直接用标准 1.05 阈值；build_a 再精确筛 ≤5%。
EXPR_A = "intersect([LowPrice > 0, ClosePrice <= Week52Low * 1.05, TotalMV > 0])"

# 52周最低价在 gtimg 中的字段索引（不同市场不同）
# A股: f[68]=52周最低(前复权口径，与行情软件一致);
#   注意 f[48] 是后复权虚高值(≈现价)，不可用(见 2026-08-20 诊断)。
# 美股/港股: f[49]=52周最低
WL_IDX_A = 68
WL_IDX_US = 49
WL_IDX_HK = 49

def _gen_a_codes():
    """生成所有可能的 A 股代码（沪市主板 + 科创板 + 深市主板 + 创业板）"""
    codes = []
    # 沪市主板: 600000-605999
    for n in range(600000, 606000):
        codes.append("sh%06d" % n)
    # 科创板: 688000-689999
    for n in range(688000, 690000):
        codes.append("sh%06d" % n)
    # 深市主板: 000001-003999
    for n in range(1, 4000):
        codes.append("sz%06d" % n)
    # 创业板: 300001-301999
    for n in range(300001, 302000):
        codes.append("sz%06d" % n)
    return codes

def _get_codes_from_file(path):
    """从旧数据文件中提取股票代码列表"""
    try:
        d = json.load(open(path, encoding="utf-8"))
        rows = d if isinstance(d, list) else (d.get("data") or d.get("list") or d.get("rows") or [])
        return [r["code"] for r in rows if r.get("code")]
    except Exception:
        return []

# 股票/基金过滤：universe 清单为"股票+基金+ETF"混合（无证券类型字段），
# 故用「代码后缀(美股) + 名称语义(美股/港股)」启发式仅保留纯普通股/股票。
# 美股：代码含 "_"（Series/Notes，如 AAPL22/ABR_D）或名称含 ETF/基金/债券/优先股/权证等 → 排除
# 港股：名称含 ETF/基金/指数等关键词 → 排除（港股 ETF 名称集中，不可用代码段一刀切，否则误杀 028xx 段主板股票）
_US_STOCK_RE = re.compile(
    r"\bETF\b|\bETN\b|\bFund\b|\bFunds\b|\bIndex\b|Portfolio|Income|Yield|"
    r"\bBond\b|Treasury|\bNote\b|\bNotes\b|Warrant|\bWt\b|\bWts\b|Right|Unit|"
    r"Preferred|Pfd|Series [A-Z]|Proshares",
    re.I)
_HK_STOCK_RE = re.compile(
    r"ETF|ETN|基金|指数|SPDR|iShares|盈富|领航|易方达|标智|安硕|南方东英|"
    r"恒生指数|沪深300|标普|纳指", re.I)

def _us_is_stock(rec):
    """美股 universe 预筛：代码含 _（Series/Notes/Units/Warrants/SPAC）或非股票中文名 → 排除。
    注：universe 清单 name 为中文，英文基金名(ETF/Fund)在消费端(gtimg 返回的英文名)再过滤。"""
    if "_" in (rec.get("code") or ""):
        return False
    return not re.search(r"基金|ETF|ETN", rec.get("name") or "")

def _hk_is_stock(rec):
    """港股记录是否为纯股票（非 ETF/基金/指数产品）"""
    return not _HK_STOCK_RE.search(rec.get("name") or "")

def _sync_universe():
    """从每周更新的全市场清单同步美股/港股 universe（仅保留纯股票），写入 data/us_codes.json / hk_codes.json。
    冻结根因：8-17 westock-tool 被删除后不再生成 universe；改用此清单解冻并过滤基金/ETF。
    若清单缺失则回退到现有 near_*_raw.json 代码（不过滤）。"""
    # 美股：裸 ticker -> us+ticker（含 .B 等后缀，gtimg 直接识别）；过滤非股票
    us_codes, us_drop = [], 0
    try:
        d = json.load(open(US_UNIVERSE_JSON, encoding="utf-8"))
        for r in d:
            if not _us_is_stock(r):
                us_drop += 1
                continue
            c = (r.get("code") or "").strip()
            if c:
                us_codes.append("us" + c)
    except Exception as e:
        sys.stderr.write("读 %s 失败: %s，回退 near_us_raw.json\n" % (US_UNIVERSE_JSON, e))
        us_codes = _get_codes_from_file(RAW_US)
    # 港股：5位裸代码 -> hk+补零到5位；过滤非股票
    hk_codes, hk_drop = [], 0
    try:
        d = json.load(open(HK_UNIVERSE_JSON, encoding="utf-8"))
        for r in d:
            if not _hk_is_stock(r):
                hk_drop += 1
                continue
            c = (r.get("code") or "").strip()
            if c:
                hk_codes.append("hk" + c.zfill(5))
    except Exception as e:
        sys.stderr.write("读 %s 失败: %s，回退 near_hk_raw.json\n" % (HK_UNIVERSE_JSON, e))
        hk_codes = _get_codes_from_file(RAW_HK)
    us_codes = list(dict.fromkeys(us_codes))   # 去重保序
    hk_codes = list(dict.fromkeys(hk_codes))
    json.dump(us_codes, open(US_CODES, "w", encoding="utf-8"))
    json.dump(hk_codes, open(HK_CODES, "w", encoding="utf-8"))
    print("UNIVERSE 同步: 美股 %d (过滤%d非股票) / 港股 %d (过滤%d非股票) -> %s"
          % (len(us_codes), us_drop, len(hk_codes), hk_drop, DATA))
    return us_codes, hk_codes

def _ensure_codes(market):
    """返回美股/港股 universe 代码（gtimg 格式）。
    每周自动从 cloned_repo 清单同步（其 mtime 更新即重同步），每日若清单未更新则直接复用
    本地 codes 文件（仅刷新行情），从而实现「每周同步一次 universe、每日只刷新行情」。"""
    codes_path = US_CODES if market == "us" else HK_CODES
    src_path = US_UNIVERSE_JSON if market == "us" else HK_UNIVERSE_JSON
    need = True
    if os.path.exists(codes_path):
        if os.path.exists(src_path):
            need = os.path.getmtime(src_path) > os.path.getmtime(codes_path)
        else:
            need = False   # 源缺失，保留上次同步结果，不破坏每日运行
    if need:
        _sync_universe()
    try:
        return json.load(open(codes_path, encoding="utf-8"))
    except Exception:
        return _get_codes_from_file(RAW_US if market == "us" else RAW_HK)

def fetch_one(outp, market):
    """通过 gtimg API 批量获取股票数据（替代已失效的 westock-tool CLI）"""
    label = market or "A股"
    print("FETCH", label, "via gtimg API")

    # 获取候选股票代码池
    if market in (None, "hs"):
        # A股: 生成全量代码（沪市主板+科创+深市主板+创业），确保不遗漏新进入52周低点区域的股票
        codes = _gen_a_codes()
        wl_idx = WL_IDX_A
        mv_idx = 44  # A股总市值(亿元)
        print("  候选池:", len(codes), "个 A股代码 (全量生成)")
    elif market == "us":
        codes = _ensure_codes("us")
        wl_idx = WL_IDX_US
        mv_idx = 45  # 美股总市值(亿美元)
        print("  候选池:", len(codes), "个美股代码 (from universe %s)" % os.path.basename(US_CODES))
    elif market == "hk":
        codes = _ensure_codes("hk")
        wl_idx = WL_IDX_HK
        mv_idx = 44  # 港股总市值(亿港元)
        print("  候选池:", len(codes), "个港股代码 (from universe %s)" % os.path.basename(HK_CODES))
    else:
        codes = []
        wl_idx = WL_IDX_A
        mv_idx = 44

    if not codes:
        print("  警告: 候选池为空，写入空数组")
        with open(outp, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 批量查询 gtimg 行情（每批50个）
    records = []
    batch = 50
    for attempt in range(1, 4):
        records = []
        for i in range(0, len(codes), batch):
            chunk = codes[i:i + batch]
            url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
                txt = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
            except Exception as e:
                sys.stderr.write("  gtimg batch %d failed: %s\n" % (i // batch, e))
                time.sleep(0.5)
                continue
            for line in txt.strip().split("\n"):
                if not line.startswith("v_"):
                    continue
                try:
                    var = line.split("=", 1)[0]
                    code = var[2:]
                    payload = line.split('"', 2)[1]
                    f = payload.split("~")
                    if len(f) <= max(wl_idx, mv_idx, 3, 32):
                        continue
                    cp_str = f[3]
                    wl_str = f[wl_idx]
                    if cp_str in ("", "-") or wl_str in ("", "-"):
                        continue
                    cp = float(cp_str)
                    wl = float(wl_str)
                    if cp <= 0 or wl <= 0:
                        continue
                    chg_str = f[32] if len(f) > 32 and f[32] not in ("", "-") else "0"
                    mv_str = f[mv_idx] if len(f) > mv_idx and f[mv_idx] not in ("", "-") else "0"
                    records.append({
                        "code": code,
                        "name": f[1],
                        "Week52Low": str(wl),
                        "LowPrice": str(wl),
                        "TotalMV": mv_str,
                        "ClosePrice": str(cp),
                        "ChangePCT": chg_str,
                    })
                except Exception:
                    continue
            time.sleep(0.3)
        if records:
            break
        print("  (空结果，重试 %d/3) " % attempt)
        time.sleep(3 * attempt)

    with open(outp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    print("  获取 %d 条记录" % len(records))

def fetch_data(force_sync=False):
    os.makedirs(DATA, exist_ok=True)
    if force_sync:
        _sync_universe()   # 每周强制同步 universe（cloned_repo 清单已更新）
    fetch_one(RAW_A, "hs"); time.sleep(1)
    fetch_one(RAW_US, "us"); time.sleep(1)
    fetch_one(RAW_HK, "hk")

def load(p):
    d = json.load(open(p, encoding="utf-8"))
    rows = d if isinstance(d, list) else (d.get("data") or d.get("list") or d.get("rows") or [])
    return rows

def market_of(code):
    if code.startswith("sh"): return "上海"
    if code.startswith("sz"): return "深圳"
    return "其他"
def board_of(code):
    c = code[2:]
    if code.startswith("sh688"): return "科创板"
    if code.startswith("sh"): return "沪市主板"
    if c.startswith("30"): return "创业板"
    if c.startswith("00") or c.startswith("001") or c.startswith("002") or c.startswith("003"): return "深市主板"
    if c.startswith("8") or c.startswith("4"): return "北交所"
    return "其他"

def mcap_of(_mv):
    # TotalMV 单位可能随数据源变化：值≥1e6 视为「元」需÷1e8，否则视为「亿」直接使用
    return round(_mv / 1e8, 1) if _mv >= 1e6 else round(_mv, 1)

def strip_code(code):
    # 仅去除市场前缀（sh/sz/hk/us），保留后续代码/ ticker（美股 ticker 全为字母，不可整体删除）
    return re.sub(r'^(sh|sz|hk|us)', '', code)

def _has_fields(r, *fields):
    return all(r.get(f) not in (None, "") for f in fields)

def build_a():
    # fetch_one 已用 f[68](前复权) 作为 Week52Low，数据正确，无需二次校正。
    near = [r for r in load(RAW_A)
            if not r.get("name", "").startswith("N")
            and _has_fields(r, "ClosePrice", "ChangePCT", "TotalMV", "Week52Low")]
    rows = []
    for r in near:
        cp = float(r["ClosePrice"])
        try:
            wl = float(r.get("Week52Low", 0) or 0)
        except Exception:
            continue
        if wl <= 0:
            continue
        dist = round((cp - wl) / wl * 100, 2)
        if dist > 5:
            continue
        rows.append({
            "code": strip_code(r["code"]), "name": r["name"],
            "cp": cp, "chg": float(r["ChangePCT"]),
            "low": wl, "dist": dist, "mcap": mcap_of(float(r["TotalMV"])),
            "market": market_of(r["code"]), "board": board_of(r["code"]),
        })
    rows.sort(key=lambda x: x["chg"])
    cnt = lambda p: sum(1 for r in rows if p(r))
    stats = {
        "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "near_n": len(rows),
        "mcap_unit": "亿",
        "sub": "收盘价距52周最低≤5%即计入（已排除数据缺失及新股）。",
        "cards": [
            {"v": len(rows), "k": "全部", "f": "", "v2": ""},
            {"v": cnt(lambda r: r["board"] == "沪市主板"), "k": "沪市主板", "f": "board", "v2": "沪市主板"},
            {"v": cnt(lambda r: r["board"] == "深市主板"), "k": "深市主板", "f": "board", "v2": "深市主板"},
            {"v": cnt(lambda r: r["board"] == "科创板"), "k": "科创板", "f": "board", "v2": "科创板"},
            {"v": cnt(lambda r: r["board"] == "创业板"), "k": "创业板", "f": "board", "v2": "创业板"},
        ],
    }
    return rows, stats

def us_board_of_suffix(suffix):
    # 腾讯 gtimg 美股代码后缀：.OQ/.O=纳斯达克；.N/.A=纽交所(及纽交所美国)；其余归「其他」
    if suffix in ("OQ", "O"): return "纳斯达克"
    if suffix in ("N", "A"): return "纽交所"
    return "其他"

def fetch_us_meta(codes):
    # codes: ["usAZO", ...]；批量拉腾讯行情，解析交易所与总市值
    # 注意：westock-tool 的 TotalMV 对美股单位不稳定（部分股票为原始美元、且与真实值偏差巨大），
    # 故美股总市值统一改用腾讯 gtimg 的市值字段。
    #   经核对，field[45] 即为「总市值（单位：亿美元）」，与真实值吻合
    #   （如 GRAB=135.38 亿≈真实$13.5B、KIDZ=0.00861 亿≈真实$0.86M），
    #   而 field[36]/1e5 系统性偏高数倍（如 GRAB 误为 578 亿），故仅作兜底。
    meta = {}
    if not codes:
        return meta
    batch = 50
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            txt = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
        except Exception as e:
            sys.stderr.write("fetch_us_meta batch failed: %s\n" % e)
            time.sleep(0.5)
            continue
        for line in txt.strip().split("\n"):
            if not line.startswith("v_"):
                continue
            try:
                var = line.split("=", 1)[0]            # v_usAZO
                code = var[2:]                          # usAZO
                payload = line.split('"', 2)[1]         # 200~汽车地带~AZO.N~...
                f = payload.split("~")
                exch = f[2]                             # AZO.N
                suffix = exch.split(".")[-1].upper() if "." in exch else ""
                board = us_board_of_suffix(suffix)
                mcap = None
                # 主用 field[45]（已是亿美元）。保留足够小数位：
                # 微型股(如 OTC 股 ZYLMD=0.00115 亿)若四舍五入到2位会被舍成 0，
                # 进而误触发下面 field[36] 兜底造成市值虚高数十倍，故此处保留4位小数。
                if len(f) > 45 and f[45] not in ("", "-"):
                    try:
                        v = float(f[45])
                        mcap = round(v, 4) if v > 0 else 0.0
                    except Exception:
                        mcap = None
                # 兜底：仅当 field[45] 完全缺失/非法时才用 field[36]（千美元 -> 亿美元）估算；
                # 注意 field[36]/1e5 对多数股票系统性偏高，不可作为常规口径。
                if mcap is None and len(f) > 36 and f[36] not in ("", "-"):
                    try:
                        mcap = round(float(f[36]) / 1e5, 4)
                    except Exception:
                        mcap = None
                meta[code] = {"board": board, "mcap": mcap}
            except Exception:
                continue
        time.sleep(0.3)
    return meta

def fetch_a_meta(codes):
    # A股 52周最低价工具函数（备用，build_a 已直接用 fetch_one 的 f[68] 值，无需二次调用）
    # gtimg A股字段：f[3]=现价, f[47]=52周最高, f[68]=52周最低(前复权口径，与行情软件一致)
    # 注意 f[48] 是后复权虚高值(≈现价)，不可用
    meta = {}
    if not codes:
        return meta
    batch = 50
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            txt = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
        except Exception as e:
            sys.stderr.write("fetch_a_meta batch failed: %s\n" % e)
            time.sleep(0.5)
            continue
        for line in txt.strip().split("\n"):
            if not line.startswith("v_"):
                continue
            try:
                var = line.split("=", 1)[0]            # v_sh600363
                code = var[2:]                          # sh600363
                payload = line.split('"', 2)[1]
                f = payload.split("~")
                wl = None
                if len(f) > 68 and f[68] not in ("", "-"):
                    try:
                        wl = float(f[68])
                    except Exception:
                        wl = None
                meta[code] = {"wl": wl}
            except Exception:
                continue
        time.sleep(0.3)
    return meta

def build_us():
    near = [r for r in load(RAW_US) if _has_fields(r, "Week52Low", "ClosePrice", "ChangePCT", "TotalMV")]
    codes = [r["code"] for r in near]                  # usXXX
    meta = fetch_us_meta(codes)
    rows = []
    for r in near:
        low = float(r["Week52Low"]); cp = float(r["ClosePrice"])
        if low <= 0:
            continue
        dist = round((cp - low) / low * 100, 2)
        if dist > 5:
            continue
        if _US_STOCK_RE.search(r["name"]):   # 消费端英文名再过滤基金/ETF（universe 中文名无法命中）
            continue
        m = meta.get(r["code"], {})
        g_mcap = m.get("mcap")
        # 优先用腾讯 gtimg 市值（单位稳定）；仅当 gtimg 完全缺失时才回退 westock（美股 TotalMV 不可靠）
        mcap = g_mcap if g_mcap is not None else mcap_of(float(r["TotalMV"]))
        rows.append({
            "code": strip_code(r["code"]), "name": r["name"],
            "cp": cp, "chg": float(r["ChangePCT"]),
            "low": low, "dist": dist, "mcap": mcap,
            "market": "美股", "board": m.get("board", "其他"),
        })
    rows.sort(key=lambda x: x["chg"])
    cnt = lambda p: sum(1 for r in rows if p(r))
    stats = {
        "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "near_n": len(rows),
        "mcap_unit": "亿美元",
        "sub": "收盘价距52周最低≤5%即计入（已排除数据缺失）。板块按交易所划分。",
        "cards": [
            {"v": len(rows), "k": "全部", "f": "", "v2": ""},
            {"v": cnt(lambda r: r["board"] == "纽交所"), "k": "纽交所", "f": "board", "v2": "纽交所"},
            {"v": cnt(lambda r: r["board"] == "纳斯达克"), "k": "纳斯达克", "f": "board", "v2": "纳斯达克"},
        ],
    }
    return rows, stats

def hk_board_of(code):
    # 港交所：8xxxxx 为创业板(GEM)，其余(0xxxxx 等)为主板
    num = re.sub(r'^hk', '', code)
    return "创业板" if num.startswith("8") else "主板"

def build_hk():
    near = [r for r in load(RAW_HK) if _has_fields(r, "Week52Low", "ClosePrice", "ChangePCT", "TotalMV")]
    rows = []
    for r in near:
        low = float(r["Week52Low"]); cp = float(r["ClosePrice"])
        if low <= 0:
            continue
        dist = round((cp - low) / low * 100, 2)
        if dist > 5:
            continue
        rows.append({
            "code": strip_code(r["code"]), "name": r["name"],
            "cp": cp, "chg": float(r["ChangePCT"]),
            "low": low, "dist": dist, "mcap": mcap_of(float(r["TotalMV"])),
            "market": "港股", "board": hk_board_of(r["code"]),
        })
    rows.sort(key=lambda x: x["chg"])
    cnt = lambda p: sum(1 for r in rows if p(r))
    stats = {
        "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "near_n": len(rows),
        "mcap_unit": "亿港元",
        "sub": "收盘价距52周最低≤5%即计入（已排除数据缺失）。板块按港交所划分。",
        "cards": [
            {"v": len(rows), "k": "全部", "f": "", "v2": ""},
            {"v": cnt(lambda r: r["board"] == "主板"), "k": "主板", "f": "board", "v2": "主板"},
            {"v": cnt(lambda r: r["board"] == "创业板"), "k": "创业板", "f": "board", "v2": "创业板"},
        ],
    }
    return rows, stats

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>股价创52周新低 公司列表（A股 / 美股）</title>
<style>
  :root {
    --bg:#f5f7fa; --card:#fff; --line:#e6e9ef; --text:#1f2733; --muted:#7a869a;
    --red:#d8262c; --green:#15915a; --accent:#2f6fed; --brand:#c0392b;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif;
         background:var(--bg); color:var(--text); font-size:14px; }
  .wrap { max-width:1120px; margin:0 auto; padding:24px 18px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; line-height:1.6; margin-bottom:10px; }
  .tabs { display:flex; gap:8px; margin:4px 0 14px; }
  .tab { background:var(--card); border:1px solid var(--line); border-radius:9px;
         padding:8px 22px; font-size:14px; font-weight:600; cursor:pointer;
         color:var(--muted); transition:.15s; user-select:none; }
  .tab:hover { border-color:var(--accent); color:var(--accent); }
  .tab.active { background:var(--accent); border-color:var(--accent); color:#fff; }
  .toolbar { display:flex; align-items:center; gap:10px; margin:2px 0 12px; }
  .scan-btn { background:var(--accent); color:#fff; border:none; border-radius:9px;
             padding:8px 16px; font-size:13px; font-weight:600; cursor:pointer;
             display:inline-flex; align-items:center; gap:7px; transition:.15s; }
  .scan-btn:hover { filter:brightness(1.06); }
  .scan-btn:disabled { opacity:.65; cursor:default; }
  .scan-btn .spin { width:13px; height:13px; border:2px solid rgba(255,255,255,.45);
                    border-top-color:#fff; border-radius:50%; display:none;
                    animation:spin .7s linear infinite; }
  .scan-btn.busy .spin { display:inline-block; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .scan-prog { color:var(--muted); font-size:12px; }
  .scan-bar { flex:1; height:4px; background:var(--line); border-radius:3px; overflow:hidden; display:none; min-width:60px; }
  .scan-bar.on { display:block; }
  .scan-bar > i { display:block; height:100%; width:0; background:var(--accent); transition:width .2s; }
  .cards { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:4px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:9px;
           padding:8px 14px; min-width:92px; flex:1; cursor:pointer; transition:.15s; user-select:none; }
  .card:hover { border-color:var(--accent); transform:translateY(-1px); }
  .card.active { background:var(--accent); border-color:var(--accent); }
  .card.active .v, .card.active .k { color:#fff; }
  .card .v { font-size:19px; font-weight:700; }
  .card .k { color:var(--muted); font-size:11px; margin-top:2px; }
  .hint { color:var(--muted); font-size:12px; margin:6px 2px 10px; min-height:16px; }
  .table-scroll { overflow-x:auto; -webkit-overflow-scrolling:touch; margin-bottom:14px;
                  border:1px solid var(--line); border-radius:12px; background:var(--card); }
  table { width:100%; min-width:780px; border-collapse:collapse; background:var(--card); }
  th,td { padding:10px 12px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { background:#fafbfc; color:var(--muted); font-weight:600; cursor:pointer; user-select:none; position:sticky; top:0; z-index:1; }
  td.idx, th.idx { position:sticky; left:0; z-index:2; background:var(--card); }
  td.name, th.name { position:sticky; left:40px; z-index:2; background:var(--card); }
  #t.us-mode th.name, #t.us-mode td.name { max-width:8ch; overflow:hidden; text-overflow:ellipsis; }
  th.idx, th.name { background:#fafbfc; z-index:3; }
  tbody tr:hover td.idx, tbody tr:hover td.name { background:#f3f7ff; }
  th:hover { color:var(--accent); }
  tbody tr:hover { background:#f3f7ff; }
  .idx { color:var(--muted); width:40px; }
  .code { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; color:var(--muted); }
  .name { font-weight:600; }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  .up { color:var(--red); }
  .down { color:var(--green); }
  .note { color:var(--muted); font-size:12px; margin-top:16px; line-height:1.8; }
  .tag { display:inline-block; background:#eef3ff; color:var(--accent); border-radius:6px;
          padding:1px 7px; font-size:12px; margin-right:4px; }
  th.sort-asc::after { content:" ▲"; font-size:10px; color:var(--brand); }
  th.sort-desc::after { content:" ▼"; font-size:10px; color:var(--brand); }
  #empty { display:none; padding:34px 12px; text-align:center; color:var(--muted); font-size:14px; line-height:1.9; }
  @media (max-width:640px) {
    .wrap { padding:14px 11px 48px; }
    h1 { font-size:18px; margin-bottom:2px; }
    .sub { font-size:12px; line-height:1.4; margin-bottom:8px; }
    .tabs { gap:6px; } .tab { flex:1; text-align:center; padding:8px 6px; }
    .toolbar { flex-wrap:wrap; gap:8px; } .scan-bar { order:3; flex-basis:100%; min-width:0; }
    .cards { display:grid; grid-template-columns:repeat(5,1fr); gap:4px; margin-bottom:4px; }
    .card { min-width:0; flex:none; padding:6px 2px; border-radius:8px; text-align:center; }
    .card .v { font-size:15px; }
    .card .k { font-size:9px; margin-top:1px; line-height:1.2; }
    .hint { margin:6px 2px 10px; font-size:12px; }
    .table-scroll { border-radius:10px; }
    th, td { padding:9px 10px; font-size:13px; }
    .note { font-size:12px; line-height:1.7; }
  }
</style>
<!-- Vercel Web Analytics -->
<script>
  window.va = window.va || function () { (window.vaq = window.vaq || []).push(arguments); };
</script>
<script defer src="/_vercel/insights/script.js"></script>
</head>
<body>
<div class="wrap">
  <h1>股价创52周新低 · 公司列表</h1>
  <div class="sub" id="sub"></div>

  <div class="tabs">
    <div class="tab active" data-tab="a" onclick="loadTab('a')">A股</div>
    <div class="tab" data-tab="hk" onclick="loadTab('hk')">港股</div>
    <div class="tab" data-tab="us" onclick="loadTab('us')">美股</div>
  </div>

  <div class="toolbar">
    <button id="scanBtn" class="scan-btn" onclick="startScan()">
      <span class="spin"></span><span id="scanLabel">扫描最新数据</span>
    </button>
    <span id="scanProg" class="scan-prog"></span>
    <div class="scan-bar" id="scanBar"><i id="scanBarFill"></i></div>
  </div>

  <div class="cards" id="cards"></div>
  <div class="hint" id="hint"></div>

  <div class="table-scroll">
  <table id="t">
    <thead><tr>
      <th onclick="sortCol(0,false)">序号</th>
      <th onclick="sortCol(1,true)">代码</th>
      <th onclick="sortCol(2,true)">名称</th>
      <th class="num" onclick="sortCol(3,false)">最新价</th>
      <th class="num" onclick="sortCol(4,false)">涨跌幅</th>
      <th class="num" onclick="sortCol(5,false)">52周最低</th>
      <th class="num" onclick="sortCol(6,false)">距52周低点</th>
      <th class="num" id="mcapHead" onclick="sortCol(7,false)">总市值(亿)</th>
      <th onclick="sortCol(8,true)">板块</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  </div>
  <div id="empty">当前没有符合条件的股票。</div>

  <div class="note">
    <span class="tag">口径</span>
    “收盘价距52周最低 ≤ 5%”：收盘收在历史最低上方 5% 以内（含等于或低于）即计入，当前共 <b id="noteNear">__NEAR__</b> 只。<br>
    <span class="tag">说明</span>
    "距52周低点" = (最新价 − 52周最低) / 52周最低；"总市值"单位为<span id="mcapUnit">亿</span>。点击模块卡片可一键筛选；点击任意表头可按该列升降序排序。<br>
    本报告仅作客观数据筛选与展示，不构成任何投资建议。市场有风险，决策需谨慎。
  </div>
</div>

<script>
var TABS = {
  a:  { label:"A股", data: __DATA_A__,  stats: __STATS_A__ },
  us: { label:"美股", data: __DATA_US__, stats: __STATS_US__ },
  hk: { label:"港股", data: __DATA_HK__, stats: __STATS_HK__ }
};
// 全量 universe 代码（来自每周同步的 data/us_codes.json / hk_codes.json，gtimg 格式 usXXX / hkXXXXX）。
// 扫描美股/港股时优先用全量 universe 重扫，从而捕捉「新跌入52周低点」的标的（不再冻结当前池）；
// 若 universe 未同步（空数组），则回退为刷新当前表。
var US_UNIVERSE = __UNIVERSE_US__;
var HK_UNIVERSE = __UNIVERSE_HK__;
// 消费端英文名基金黑名单（对应 Python _US_STOCK_RE）：universe 清单 name 为中文无法命中，
// 故对 gtimg 返回的英文名再过滤 ETF/ETN/Fund/Index/Portfolio/Bond/Note/Wts/Right/Unit/Preferred/Series/Proshares（保留 Trust/REIT）
var FUND_RE = /\bETF\b|\bETN\b|\bFund\b|\bFunds\b|\bIndex\b|Portfolio|Income|Yield|\bBond\b|Treasury|\bNote\b|\bNotes\b|Warrant|\bWt\b|\bWts\b|Right|Unit|Preferred|Pfd|Series [A-Z]|Proshares/i;
var CUR = "a";
var DATA, STATS, moduleField = "", moduleVal = "";

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function fmtMcap(v){
  // 大市值取整；小市值保留小数；微型股(如 OTC 股 0.0012 亿)保留4位，避免显示为 0
  if(v >= 100) return String(Math.round(v));
  if(v >= 1) return v.toFixed(1);
  if(v >= 0.01) return v.toFixed(2);
  return v.toFixed(4);
}

function loadTab(id){
  CUR = id;
  var t = TABS[id];
  DATA = t.data; STATS = t.stats;
  moduleField = ""; moduleVal = "";
  document.getElementById('sub').textContent = STATS.sub + "更新：" + STATS.now + " · 腾讯自选股（仅供参考）";
  document.getElementById('noteNear').textContent = STATS.near_n;
  document.getElementById('mcapHead').textContent = "总市值(" + STATS.mcap_unit + ")";
  document.getElementById('mcapUnit').textContent = STATS.mcap_unit;
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active', b.dataset.tab===id));
  document.getElementById('t').classList.toggle('us-mode', id==='us');
  buildCards(); buildRows(); render();
}

function buildCards(){
  var cards = STATS.cards;
  document.getElementById('cards').innerHTML = cards.map(function(c){
    return '<div class="card" data-f="'+c.f+'" data-v="'+c.v2+'" onclick="pickModule(this)">'+
           '<div class="v">'+c.v+'</div><div class="k">'+c.k+'</div></div>';
  }).join('');
  setActiveCard();
}
function setActiveCard(){
  document.querySelectorAll('.card').forEach(c=>c.classList.remove('active'));
  var sel = moduleField
    ? document.querySelector('.card[data-f="'+moduleField+'"][data-v="'+moduleVal+'"]')
    : document.querySelector('.card[data-f=""]');
  if(sel) sel.classList.add('active');
}
function pickModule(el){
  moduleField = el.dataset.f; moduleVal = el.dataset.v;
  setActiveCard(); render();
}
function buildRows(){
  var tb = document.getElementById('tbody');
  tb.innerHTML = DATA.map(function(r,i){
    var chg=r.chg, cls=chg>=0?"up":"down", sign=chg>=0?"+":"";
    return '<tr data-market="'+r.market+'" data-board="'+r.board+'">'+
      '<td class="idx">'+(i+1)+'</td>'+
      '<td class="code">'+esc(r.code)+'</td>'+
      '<td class="name">'+esc(r.name)+'</td>'+
      '<td class="num">'+r.cp.toFixed(2)+'</td>'+
      '<td class="num '+cls+'">'+sign+chg.toFixed(2)+'%</td>'+
      '<td class="num">'+r.low.toFixed(2)+'</td>'+
      '<td class="num">'+r.dist.toFixed(2)+'%</td>'+
      '<td class="num">'+fmtMcap(r.mcap)+'</td>'+
      '<td>'+esc(r.board)+'</td></tr>';
  }).join('');
  lastCol=-1; lastDir='desc';
  document.querySelectorAll('#t th').forEach(th=>th.classList.remove('sort-asc','sort-desc'));
}
function render(){
  var vis = 0;
  document.querySelectorAll('#t tbody tr').forEach(tr=>{
    var show = true;
    if(moduleField && tr.dataset[moduleField] !== moduleVal) show=false;
    tr.style.display = show ? "" : "none";
    if(show) vis++;
  });
  document.getElementById('empty').style.display = vis===0 ? "block" : "none";
  var k=0;
  document.querySelectorAll('#t tbody tr').forEach(tr=>{ if(tr.style.display!=="none") tr.children[0].innerText=++k; });
  var label = moduleField ? document.querySelector('.card.active .k').innerText : "全部";
  document.getElementById('hint').innerText = "当前显示：" + label + "（" + vis + " 只）";
}
var numGet = (tr,i)=>parseFloat(tr.children[i].innerText.replace(/[%,]/g,''))||0;
var strGet = (tr,i)=>tr.children[i].innerText;
var lastCol=-1, lastDir='desc';
function sortCol(i,isStr){
  var dir = (lastCol===i && lastDir==='desc') ? 'asc' : 'desc';
  doSort(i,isStr,dir);
}
function doSort(i,isStr,dir){
  var tb=document.getElementById('t').tBodies[0];
  var trs=Array.from(tb.querySelectorAll('tr'));
  trs.sort((a,b)=>{
    var r = isStr ? strGet(a,i).localeCompare(strGet(b,i),'zh') : numGet(a,i)-numGet(b,i);
    return dir==='asc' ? r : -r;
  });
  trs.forEach(tr=>tb.appendChild(tr));
  var k=0;
  trs.forEach(tr=>{ if(tr.style.display!=="none") tr.children[0].innerText=++k; });
  lastCol=i; lastDir=dir;
  document.querySelectorAll('#t th').forEach(th=>th.classList.remove('sort-asc','sort-desc'));
  var thEl=document.querySelectorAll('#t th')[i];
  thEl.classList.toggle('sort-asc', dir==='asc');
  thEl.classList.toggle('sort-desc', dir==='desc');
}
// ---- 实时扫描（纯前端，直接调腾讯 gtimg 行情接口，无需服务器） ----
function genACodes(){
  var codes = [];
  for (var n=600000; n<=605999; n++) codes.push("sh"+("000000"+n).slice(-6));
  for (var n=688000; n<=689999; n++) codes.push("sh"+("000000"+n).slice(-6));
  for (var n=1; n<=3999; n++) codes.push("sz"+("000000"+n).slice(-6));
  for (var n=300001; n<=301999; n++) codes.push("sz"+("000000"+n).slice(-6));
  return codes;
}
function boardOfA(code){
  var c = code.slice(2);
  if (code.indexOf("sh688")===0) return "科创板";
  if (code.indexOf("sh")===0) return "沪市主板";
  if (c.indexOf("30")===0) return "创业板";
  if (c.indexOf("00")===0 || c.indexOf("001")===0 || c.indexOf("002")===0 || c.indexOf("003")===0) return "深市主板";
  return "其他";
}
function marketOfA(code){
  if (code.indexOf("sh")===0) return "上海";
  if (code.indexOf("sz")===0) return "深圳";
  return "其他";
}
function usBoardOfSuffix(suffix){
  // 与生成器 us_board_of_suffix 一致：.OQ/.O=纳斯达克；.N/.A=纽交所；其余=其他
  if (suffix==="OQ" || suffix==="O") return "纳斯达克";
  if (suffix==="N" || suffix==="A") return "纽交所";
  return "其他";
}
function hkBoardOfCode(displayCode){
  // 港交所：8xxxxx 为创业板(GEM)，其余(0xxxxx 等)为主板（与生成器 hk_board_of 一致）
  return displayCode.charAt(0)==="8" ? "创业板" : "主板";
}
function mcapOf(mv){
  // 与生成器一致：值≥1e6 视为「元」需÷1e8，否则视为「亿」直接使用
  return mv >= 1e6 ? mv/1e8 : mv;
}
function gbkDecode(buf){
  if (typeof buf === "string") return buf;
  try { return new TextDecoder("gbk").decode(buf); } catch(e){ return new TextDecoder("utf-8").decode(buf); }
}
// 解析一段 GBK 文本（多行 v_xxx="...";），对每条有效记录回调 onRow
function parseGtimg(str, market, onRow){
  var mvIdx = (market==="us") ? 45 : 44;
  var wlIdx = (market==="hk") ? 49 : ((market==="us") ? 49 : 68);
  var lines = str.split("\n");
  for (var i=0;i<lines.length;i++){
    var line = lines[i].trim();
    if (line.indexOf("v_")!==0) continue;
    var eq = line.indexOf("="); if (eq<0) continue;
    var code = line.slice(2, eq);
    var q1 = line.indexOf('"'); if (q1<0) continue;
    var payload = line.slice(q1+1, line.lastIndexOf('"'));
    var f = payload.split("~");
    if (f.length <= Math.max(wlIdx, mvIdx, 32, 3)) continue;
    var name = f[1];
    var cp = parseFloat(f[3]);
    var wl = parseFloat(f[wlIdx]);
    var chg = parseFloat(f[32]) || 0;
    var mv = parseFloat(f[mvIdx]) || 0;
    var exch = f[2] || "";
    if (!isFinite(cp) || !isFinite(wl) || cp<=0 || wl<=0) continue;
    onRow({code:code, name:name, cp:cp, chg:chg, low:wl, mcap:mv, exch:exch});
  }
}
// 并发批量拉取实时行情（gtimg 支持 CORS *），每批 ~80 个代码，单主机并发 8
function fetchQuotes(codes, market, batchSize, onBatch){
  var results = [];
  var total = Math.ceil(codes.length / batchSize);
  var done = 0, active = 0, idx = 0, MAXCON = 6;
  return new Promise(function(resolve){
    function pump(){
      while (active < MAXCON && idx < total){
        var b = idx++; active++;
        var chunk = codes.slice(b*batchSize, b*batchSize+batchSize);
        var url = "https://qt.gtimg.cn/q=" + chunk.join(",");
        fetch(url, {headers:{Referer:"https://gu.qq.com/"}})
          .then(function(r){ return r.arrayBuffer(); })
          .then(function(buf){ parseGtimg(gbkDecode(buf), market, function(row){ results.push(row); }); })
          .catch(function(){ })
          .then(function(){ active--; done++; onBatch(done, total);
            if (idx < total) pump(); else if (active===0) resolve(results); });
      }
      if (total===0) resolve(results);
    }
    pump();
  });
}
function nowStr(){
  var d = new Date(), p = function(x){ return (x<10?"0":"")+x; };
  return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())+" "+p(d.getHours())+":"+p(d.getMinutes());
}
function buildStatsA(rows){
  function cnt(p){ return rows.filter(p).length; }
  return { now: nowStr(), near_n: rows.length, mcap_unit: "亿",
    sub: "实时扫描(全市场)；收盘价距52周最低≤5%即计入（已排除新股及数据缺失）。",
    cards: [
      {v: rows.length, k:"全部", f:"", v2:""},
      {v: cnt(function(r){return r.board==="沪市主板";}), k:"沪市主板", f:"board", v2:"沪市主板"},
      {v: cnt(function(r){return r.board==="深市主板";}), k:"深市主板", f:"board", v2:"深市主板"},
      {v: cnt(function(r){return r.board==="科创板";}), k:"科创板", f:"board", v2:"科创板"},
      {v: cnt(function(r){return r.board==="创业板";}), k:"创业板", f:"board", v2:"创业板"}
    ] };
}
function deriveCardsHKUS(rows, id){
  function cnt(p){ return rows.filter(p).length; }
  if (id==="hk"){
    return [ {v:rows.length,k:"全部",f:"",v2:""},
      {v:cnt(function(r){return r.board==="主板";}),k:"主板",f:"board",v2:"主板"},
      {v:cnt(function(r){return r.board==="创业板";}),k:"创业板",f:"board",v2:"创业板"} ];
  }
  return [ {v:rows.length,k:"全部",f:"",v2:""},
    {v:cnt(function(r){return r.board==="纽交所";}),k:"纽交所",f:"board",v2:"纽交所"},
    {v:cnt(function(r){return r.board==="纳斯达克";}),k:"纳斯达克",f:"board",v2:"纳斯达克"},
    {v:cnt(function(r){return r.board==="其他";}),k:"其他",f:"board",v2:"其他"} ];
}
var SCAN_LABELS = {a:"A股(全市场重扫)", hk:"港股(全量universe)", us:"美股(全量universe)"};
function startScan(){
  var id = CUR;
  var btn = document.getElementById("scanBtn");
  var prog = document.getElementById("scanProg");
  var bar = document.getElementById("scanBar");
  var fill = document.getElementById("scanBarFill");
  btn.disabled = true; btn.classList.add("busy"); bar.classList.add("on");
  prog.textContent = "准备扫描 " + SCAN_LABELS[id] + " …";
  var codes, market, unit, sub;
  if (id==="a"){
    codes = genACodes(); market="a"; unit="亿";
    sub = "实时扫描(全市场)；收盘价距52周最低≤5%即计入（已排除新股及数据缺失）。";
  } else if (id==="hk"){
    if (HK_UNIVERSE && HK_UNIVERSE.length){
      codes = HK_UNIVERSE.slice(); market="hk"; unit="亿";
      sub = "实时扫描(全量港股 universe)；收盘价距52周最低≤5%即计入（已排除新股及数据缺失）。";
    } else {
      codes = DATA.map(function(r){ return "hk"+r.code; }); market="hk"; unit="亿";
      sub = "实时刷新(当前港股池；universe 未同步，仅刷新当前表)。";
    }
  } else {
    if (US_UNIVERSE && US_UNIVERSE.length){
      codes = US_UNIVERSE.slice(); market="us"; unit="亿美元";
      sub = "实时扫描(全量美股 universe)；收盘价距52周最低≤5%即计入（已排除新股及数据缺失）。";
    } else {
      codes = DATA.map(function(r){ return "us"+r.code; }); market="us"; unit="亿美元";
      sub = "实时刷新(当前美股池；universe 未同步，仅刷新当前表)。";
    }
  }
  fetchQuotes(codes, market, 80, function(done, total){
    prog.textContent = "扫描中 "+SCAN_LABELS[id]+"：" + done + "/" + total + " 批";
    fill.style.width = (total ? done/total*100 : 100) + "%";
  }).then(function(rowsRaw){
    var rows = [];
    for (var i=0;i<rowsRaw.length;i++){
      var r = rowsRaw[i];
      var cp = r.cp, wl = r.low;
      if (r.name && r.name.charAt(0)==="N") continue;          // 跳过新股
      var dist = Math.round((cp - wl)/wl*100*100)/100;
      if (dist > 5) continue;
      if (id!=="a" && FUND_RE.test(r.name)) continue;   // 美股/港股英文名再过滤基金/ETF（universe 中文名无法命中）
      var sc = r.code;
      var displayCode = sc.slice(2);                            // 去掉 sh/sz/hk/us 前缀
      var board, mkt;
      if (id==="a"){ board = boardOfA(sc); mkt = marketOfA(sc); }
      else if (id==="hk"){ board = hkBoardOfCode(displayCode); mkt = "港股"; }
      else {
        // 美股交易所从 gtimg 代码后缀判定（f[2]，如 AZO.N -> N -> 纽交所）
        var suffix = (r.exch && r.exch.indexOf(".")>=0) ? r.exch.split(".").pop().toUpperCase() : "";
        board = usBoardOfSuffix(suffix); mkt = "美股";
      }
      rows.push({ code: displayCode, name: r.name, cp: cp, chg: r.chg,
        low: wl, dist: dist, mcap: Math.round(mcapOf(r.mcap)*10)/10, market: mkt, board: board });
    }
    rows.sort(function(x,y){ return x.chg - y.chg; });
    var stats = (id==="a") ? buildStatsA(rows)
      : { now: nowStr(), near_n: rows.length, mcap_unit: unit, sub: sub, cards: deriveCardsHKUS(rows, id) };
    TABS[id] = { label: TABS[id].label, data: rows, stats: stats };
    moduleField=""; moduleVal="";
    document.getElementById("sub").textContent = "实时扫描更新：" + stats.now + " · 腾讯自选股（仅供参考）";
    document.getElementById("mcapHead").textContent = "总市值(" + stats.mcap_unit + ")";
    document.getElementById("mcapUnit").textContent = stats.mcap_unit;
    document.getElementById("noteNear").textContent = stats.near_n;
    buildCards(); buildRows(); render();
    fill.style.width = "100%";
    prog.textContent = "完成：" + SCAN_LABELS[id] + " 共 " + rows.length + " 只（距52周低点≤5%）";
    btn.disabled = false; btn.classList.remove("busy");
    setTimeout(function(){ bar.classList.remove("on"); }, 900);
  });
}
// 初始化
loadTab('a');
</script>
</body>
</html>
"""

def render_html(a_rows, a_stats, us_rows, us_stats, hk_rows, hk_stats):
    a_data = json.dumps(a_rows, ensure_ascii=False)
    a_stats_j = json.dumps(a_stats, ensure_ascii=False)
    us_data = json.dumps(us_rows, ensure_ascii=False)
    us_stats_j = json.dumps(us_stats, ensure_ascii=False)
    hk_data = json.dumps(hk_rows, ensure_ascii=False)
    hk_stats_j = json.dumps(hk_stats, ensure_ascii=False)
    # 全量 universe 代码（扫描美股/港股时用于全量重扫）。缺失则回退空数组（扫描回退刷新当前池）。
    try:
        us_uni = json.load(open(US_CODES, encoding="utf-8"))
    except Exception:
        us_uni = []
    try:
        hk_uni = json.load(open(HK_CODES, encoding="utf-8"))
    except Exception:
        hk_uni = []
    us_uni_j = json.dumps(us_uni, ensure_ascii=False, separators=(",", ":"))
    hk_uni_j = json.dumps(hk_uni, ensure_ascii=False, separators=(",", ":"))
    return (TEMPLATE
            .replace("__DATA_A__", a_data)
            .replace("__STATS_A__", a_stats_j)
            .replace("__DATA_US__", us_data)
            .replace("__STATS_US__", us_stats_j)
            .replace("__DATA_HK__", hk_data)
            .replace("__STATS_HK__", hk_stats_j)
            .replace("__UNIVERSE_US__", us_uni_j)
            .replace("__UNIVERSE_HK__", hk_uni_j))

def write_html(a_rows, a_stats, us_rows, us_stats, hk_rows, hk_stats):
    os.makedirs(HERE, exist_ok=True)
    html = render_html(a_rows, a_stats, us_rows, us_stats, hk_rows, hk_stats)
    with open(OUT_INDEX, "w", encoding="utf-8") as f:
        f.write(html)
    with open(OUT_CN, "w", encoding="utf-8") as f:
        f.write(html)
    with open(SNAP, "w", encoding="utf-8") as f:
        json.dump({"a": {"rows": a_rows, "stats": a_stats},
                   "us": {"rows": us_rows, "stats": us_stats},
                   "hk": {"rows": hk_rows, "stats": hk_stats}}, f, ensure_ascii=False)

def main(weekly=False):
    fetch_data(force_sync=weekly)
    a_rows, a_stats = build_a()
    us_rows, us_stats = build_us()
    hk_rows, hk_stats = build_hk()
    write_html(a_rows, a_stats, us_rows, us_stats, hk_rows, hk_stats)
    print("WROTE", OUT_INDEX)
    print("WROTE", OUT_CN)
    print("A股 主口径(收盘距52周最低≤5%)=", a_stats["near_n"],
          "| 上海=", sum(1 for r in a_rows if r["market"]=="上海"),
          "深圳=", sum(1 for r in a_rows if r["market"]=="深圳"),
          "科创=", sum(1 for r in a_rows if r["board"]=="科创板"),
          "创业=", sum(1 for r in a_rows if r["board"]=="创业板"))
    print("美股 主口径(收盘距52周最低≤5%)=", us_stats["near_n"],
          "| 纽交所=", sum(1 for r in us_rows if r["board"]=="纽交所"),
          "纳斯达克=", sum(1 for r in us_rows if r["board"]=="纳斯达克"),
          "其他=", sum(1 for r in us_rows if r["board"]=="其他"))
    print("港股 主口径(收盘距52周最低≤5%)=", hk_stats["near_n"],
          "| 主板=", sum(1 for r in hk_rows if r["board"]=="主板"),
          "创业板=", sum(1 for r in hk_rows if r["board"]=="创业板"))

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="生成股价创52周新低 HTML 日报")
    ap.add_argument("--weekly", action="store_true",
                    help="强制同步 universe（每周运行）；默认仅刷新行情（复用已同步的 codes 文件）")
    args = ap.parse_args()
    main(weekly=args.weekly)
