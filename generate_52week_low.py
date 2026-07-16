#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 端到端生成「A股股价创52周新低」HTML 日报：
#   1) 调用 westock-tool 拉取最新收盘数据（仅主口径：收盘价距 52周最低 ≤ 5%）
#   2) 生成可交互 HTML 报告到本脚本同目录
# 用法：python3 generate_52week_low.py
import json, datetime, os, subprocess, sys, re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW_NEAR = os.path.join(DATA, "near_raw.json")   # 收盘接近≤5%：ClosePrice <= Week52Low*1.05
SNAP     = os.path.join(DATA, "snapshot.json")   # 给本地服务用的 JSON 快照
OUT = os.path.join(HERE, "股价创52周新低_A股列表.html")
OUT_INDEX = os.path.join(HERE, "index.html")  # 供 Vercel / 静态托管使用的默认入口页

# 托管运行时路径（隔离、稳定）
NODE = "/Users/green/.workbuddy/binaries/node/versions/22.22.2/bin/node"
WSTOOL_DIR = "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/builtin-skills/westock-tool/scripts"

def fetch_data():
    os.makedirs(DATA, exist_ok=True)
    exprs = {
        RAW_NEAR: "intersect([LowPrice > 0, ClosePrice <= Week52Low * 1.05, TotalMV > 0])",
    }
    for outp, expr in exprs.items():
        # index.js 用相对路径 require，必须 cd 到脚本目录再执行
        cmd = 'cd "%s" && "%s" index.js filter \'%s\' --raw --limit 5000' % (WSTOOL_DIR, NODE, expr)
        print("FETCH:", expr)
        with open(outp, "w") as f:
            r = subprocess.run(cmd, shell=True, stdout=f, stderr=subprocess.PIPE, text=True)
        if r.returncode != 0:
            sys.stderr.write(r.stderr)
            sys.exit("filter failed: " + expr)

def load(p):
    d = json.load(open(p, encoding="utf-8"))
    rows = d if isinstance(d, list) else (d.get("data") or d.get("list") or d.get("rows") or [])
    return [r for r in rows if not r.get("name", "").startswith("N")]   # 排除首日新股

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

def build_dataset():
    fetch_data()
    near = load(RAW_NEAR)

    rows = []
    for r in near:
        market = market_of(r["code"]); board = board_of(r["code"])
        low = float(r["Week52Low"]); cp = float(r["ClosePrice"])
        dist = round((cp - low) / low * 100, 2) if low else 0.0
        # TotalMV 单位可能随数据源变化：值≥1e6 视为「元」需÷1e8，否则视为「亿元」直接使用
        _mv = float(r["TotalMV"])
        mcap = round(_mv / 1e8, 1) if _mv >= 1e6 else round(_mv, 1)
        rows.append({
            "code": re.sub(r'^[a-zA-Z]+', '', r["code"]), "name": r["name"],
            "cp": cp, "chg": float(r["ChangePCT"]),
            "low": low, "dist": dist, "mcap": mcap,
            "market": market, "board": board,
        })
    rows.sort(key=lambda x: x["chg"])

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    def cnt(pred): return sum(1 for r in rows if pred(r))
    stats = {
        "near_n": len(rows),
        "sh": cnt(lambda r: r["market"] == "上海"),
        "sz": cnt(lambda r: r["market"] == "深圳"),
        "kc": cnt(lambda r: r["board"] == "科创板"),
        "cy": cnt(lambda r: r["board"] == "创业板"),
        "now": now,
    }
    return rows, stats

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>A股股价创52周新低 公司列表</title>
<style>
  :root {
    --bg:#f5f7fa; --card:#fff; --line:#e6e9ef; --text:#1f2733; --muted:#7a869a;
    --red:#d8262c; --green:#15915a; --accent:#2f6fed;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif;
         background:var(--bg); color:var(--text); font-size:14px; }
  .wrap { max-width:1120px; margin:0 auto; padding:24px 18px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; line-height:1.6; margin-bottom:10px; }
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
  th.sorted::after { content:" ↕"; color:var(--accent); }
  #empty { display:none; padding:34px 12px; text-align:center; color:var(--muted); font-size:14px; line-height:1.9; }
  @media (max-width:640px) {
    .wrap { padding:14px 11px 48px; }
    h1 { font-size:18px; margin-bottom:2px; }
    .sub { font-size:12px; line-height:1.4; margin-bottom:8px; }
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
</head>
<body>
<div class="wrap">
  <h1>A股股价创52周新低 · 公司列表</h1>
  <div class="sub">收盘价距52周最低≤5%即计入（已排除数据缺失及新股）。更新：<span id="genTime">__NOW__</span> · 腾讯自选股（仅供参考）</div>

  <div class="cards" id="cards"></div>
  <div class="hint" id="hint"></div>

  <div class="table-scroll">
  <table id="t">
    <thead><tr>
      <th onclick="sortCol(0,false)">#</th>
      <th onclick="sortCol(1,true)">代码</th>
      <th onclick="sortCol(2,true)">名称</th>
      <th class="num" onclick="sortCol(3,false)">最新价</th>
      <th class="num" onclick="sortCol(4,false)">涨跌幅</th>
      <th class="num" onclick="sortCol(5,false)">52周最低</th>
      <th class="num" onclick="sortCol(6,false)">距52周低点</th>
      <th class="num" onclick="sortCol(7,false)">总市值(亿)</th>
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
    "距52周低点" = (最新价 − 52周最低) / 52周最低；"总市值"单位为亿元。点击模块卡片可一键筛选；点击任意表头可按该列升降序排序。<br>
    本报告仅作客观数据筛选与展示，不构成任何投资建议。市场有风险，决策需谨慎。
  </div>
</div>

<script>
var DATA = __DATA__;
var STATS = __STATS__;
var moduleField = "", moduleVal = "";

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function buildCards(){
  var cards = [
    {v:STATS.near_n, k:"全部", f:"", v2:""},
    {v:STATS.sh, k:"上海市场", f:"market", v2:"上海"},
    {v:STATS.sz, k:"深圳市场", f:"market", v2:"深圳"},
    {v:STATS.kc, k:"科创板", f:"board", v2:"科创板"},
    {v:STATS.cy, k:"创业板", f:"board", v2:"创业板"},
  ];
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
      '<td class="num">'+Math.round(r.mcap)+'</td>'+
      '<td>'+esc(r.board)+'</td></tr>';
  }).join('');
  lastCol=-1; lastDir='desc';
  document.querySelectorAll('#t th').forEach(th=>th.classList.remove('sorted'));
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
  document.querySelectorAll('#t th').forEach(th=>th.classList.remove('sorted'));
  document.querySelectorAll('#t th')[i].classList.add('sorted');
}
// 初始化
document.getElementById('genTime').textContent=STATS.now;
document.getElementById('noteNear').textContent=STATS.near_n;
buildCards(); buildRows(); render();
</script>
</body>
</html>
"""

def render_html(rows, stats):
    data_json = json.dumps(rows, ensure_ascii=False)
    stats_json = json.dumps(stats, ensure_ascii=False)
    return (TEMPLATE
            .replace("__DATA__", data_json)
            .replace("__STATS__", stats_json)
            .replace("__NOW__", stats["now"])
            .replace("__NEAR__", str(stats["near_n"])))

def write_html(rows, stats):
    os.makedirs(HERE, exist_ok=True)
    html = render_html(rows, stats)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    with open(OUT_INDEX, "w", encoding="utf-8") as f:
        f.write(html)
    with open(SNAP, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "stats": stats}, f, ensure_ascii=False)

def main():
    rows, stats = build_dataset()
    write_html(rows, stats)
    print("WROTE", OUT)
    print("WROTE", OUT_INDEX)
    print("主口径(收盘距52周最低≤5%)=", stats["near_n"],
          "| 上海=", stats["sh"], "深圳=", stats["sz"],
          "科创=", stats["kc"], "创业=", stats["cy"])

if __name__ == "__main__":
    main()
