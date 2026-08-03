#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 端到端生成「股价创52周新低」HTML 日报（A股 + 美股 双 tab）：
#   1) 调用 westock-tool 拉取 A股 / 美股 最新收盘数据（主口径：收盘价距 52周最低 ≤ 5%）
#   2) 生成带 A股/美股 切换 tab 的可交互 HTML 报告到本脚本同目录
# 用法：python3 generate_52week_low.py
import json, datetime, os, subprocess, sys, re, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW_A  = os.path.join(DATA, "near_a_raw.json")   # A股：收盘接近≤5%
RAW_US = os.path.join(DATA, "near_us_raw.json")  # 美股：收盘接近≤5%
RAW_HK = os.path.join(DATA, "near_hk_raw.json")  # 港股：收盘接近≤5%
SNAP   = os.path.join(DATA, "snapshot.json")     # 给本地服务用的 JSON 快照
OUT_INDEX = os.path.join(HERE, "index.html")     # 供 Vercel / 静态托管使用的默认入口页
OUT_CN = os.path.join(HERE, "股价创52周新低.html")  # 中文名入口（中性，含双市场）

# 托管运行时路径（隔离、稳定）
NODE = "/Users/green/.workbuddy/binaries/node/versions/22.22.2/bin/node"
WSTOOL_DIR = "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/builtin-skills/westock-tool/scripts"

# 主口径：收盘价距 52周最低 ≤ 5%
EXPR = "intersect([LowPrice > 0, ClosePrice <= Week52Low * 1.05, TotalMV > 0])"

def fetch_one(outp, market):
    cmd = 'cd "%s" && "%s" index.js filter \'%s\' --raw --limit 6000' % (WSTOOL_DIR, NODE, EXPR)
    if market:
        cmd += ' --market %s' % market
    label = market or "A股"
    print("FETCH", label, ":", EXPR)
    # westock-tool 偶发限流会导致返回空数组，故加重试（间隔退避）
    for attempt in range(1, 4):
        with open(outp, "w") as f:
            r = subprocess.run(cmd, shell=True, stdout=f, stderr=subprocess.PIPE, text=True)
        if r.returncode != 0:
            sys.stderr.write(r.stderr)
            sys.exit("filter failed: " + label)
        try:
            with open(outp, encoding="utf-8") as f:
                if json.load(f):
                    return  # 非空即成功
        except Exception:
            pass
        print("  (空结果，重试 %d/3) " % attempt)
        time.sleep(3 * attempt)
    sys.exit("filter returned empty after retries: " + label)

def fetch_data():
    os.makedirs(DATA, exist_ok=True)
    fetch_one(RAW_A, None); time.sleep(1)
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
    near = [r for r in load(RAW_A)
            if not r.get("name", "").startswith("N")
            and _has_fields(r, "Week52Low", "ClosePrice", "ChangePCT", "TotalMV")]
    rows = []
    for r in near:
        low = float(r["Week52Low"]); cp = float(r["ClosePrice"])
        dist = round((cp - low) / low * 100, 2) if low else 0.0
        rows.append({
            "code": strip_code(r["code"]), "name": r["name"],
            "cp": cp, "chg": float(r["ChangePCT"]),
            "low": low, "dist": dist, "mcap": mcap_of(float(r["TotalMV"])),
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
            {"v": cnt(lambda r: r["market"] == "上海"), "k": "上海市场", "f": "market", "v2": "上海"},
            {"v": cnt(lambda r: r["market"] == "深圳"), "k": "深圳市场", "f": "market", "v2": "深圳"},
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
                # 主用 field[45]（已是亿美元）
                if len(f) > 45 and f[45] not in ("", "-"):
                    try:
                        mcap = round(float(f[45]), 2)
                    except Exception:
                        mcap = None
                # 兜底：field[36]（千美元 -> 亿美元）；仅当 field[45] 缺失/为 0 时
                if (mcap is None or mcap == 0) and len(f) > 36 and f[36] not in ("", "-"):
                    try:
                        mcap = round(float(f[36]) / 1e5, 2)
                    except Exception:
                        mcap = None
                meta[code] = {"board": board, "mcap": mcap}
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
        dist = round((cp - low) / low * 100, 2) if low else 0.0
        m = meta.get(r["code"], {})
        g_mcap = m.get("mcap")
        # 优先用腾讯 gtimg 市值（单位稳定）；仅当 gtimg 缺失/为0 时才回退 westock
        mcap = g_mcap if (g_mcap is not None and g_mcap > 0) else mcap_of(float(r["TotalMV"]))
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
        dist = round((cp - low) / low * 100, 2) if low else 0.0
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
var CUR = "a";
var DATA, STATS, moduleField = "", moduleVal = "";

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function fmtMcap(v){
  // 大市值取整；小市值(美股微型股)保留小数，避免显示为 0
  if(v >= 100) return String(Math.round(v));
  if(v >= 1) return v.toFixed(1);
  return v.toFixed(2);
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
    return (TEMPLATE
            .replace("__DATA_A__", a_data)
            .replace("__STATS_A__", a_stats_j)
            .replace("__DATA_US__", us_data)
            .replace("__STATS_US__", us_stats_j)
            .replace("__DATA_HK__", hk_data)
            .replace("__STATS_HK__", hk_stats_j))

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

def main():
    fetch_data()
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
    main()
