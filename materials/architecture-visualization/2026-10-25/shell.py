"""HTML 外壳：CSS、交互脚本与页面模板。"""

from __future__ import annotations

CSS = r"""
:root{
  --ink:#131c27; --ink-2:#26313f; --muted:#5a6879; --faint:#8794a5;
  --line:#e1e7ef; --line-soft:#eef2f7;
  --bg:#f5f7fa; --card:#ffffff;
  --blue:#2a62a8; --teal:#177f68; --amber:#a9761c; --purple:#674a9b;
  --clay:#9a5340; --slate:#54637a; --green:#2f7d4f;
  --radius:16px;
  --mono:'SF Mono',Menlo,Consolas,'Roboto Mono',monospace;
  --sans:'Noto Sans SC','PingFang SC','Hiragino Sans GB','Microsoft YaHei',system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--bg); color:var(--ink); font-family:var(--sans);
  font-size:15px; line-height:1.75; -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}
a{color:var(--blue); text-decoration:none}
a:hover{text-decoration:underline}
code,.mono{font-family:var(--mono); font-size:.88em}

/* ---------- 布局 ---------- */
.shell{display:grid; grid-template-columns:250px minmax(0,1fr); gap:0; max-width:1560px; margin:0 auto}
.side{
  position:sticky; top:0; align-self:start; height:100vh; overflow-y:auto;
  padding:30px 22px 40px 30px; border-right:1px solid var(--line);
  background:linear-gradient(180deg,#fbfcfe, #f5f7fa 60%);
}
.side::-webkit-scrollbar{width:6px}
.side::-webkit-scrollbar-thumb{background:#d9e0ea;border-radius:3px}
.brand{display:flex; align-items:center; gap:10px; margin-bottom:6px}
.brand-mark{
  width:34px;height:34px;border-radius:10px; flex:none;
  background:linear-gradient(145deg,#2a62a8,#177f68);
  display:grid;place-items:center;color:#fff;font-weight:700;font-size:15px;
  box-shadow:0 4px 12px rgba(42,98,168,.28);
}
.brand-name{font-weight:700;font-size:15.5px;letter-spacing:.2px;line-height:1.25}
.brand-sub{font-size:11px;color:var(--faint);letter-spacing:.14em;text-transform:uppercase;margin-top:2px}
.side-meta{font-size:11.5px;color:var(--faint);margin:14px 0 22px; line-height:1.6}
.side-meta b{color:var(--muted);font-weight:600}
.nav{display:flex;flex-direction:column;gap:1px;margin-bottom:24px}
.nav-label{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);margin:0 0 8px 10px;font-weight:600}
.nav a{
  display:flex; align-items:baseline; gap:9px; padding:6px 10px; border-radius:8px;
  color:var(--ink-2); font-size:13.4px; line-height:1.45; border-left:2px solid transparent;
  text-decoration:none;
}
.nav a:hover{background:#eef2f8; text-decoration:none}
.nav a .num{font-family:var(--mono); font-size:10.5px; color:var(--faint); min-width:16px}
.nav a.active{background:#fff; color:var(--blue); border-left-color:var(--blue); box-shadow:0 1px 2px rgba(20,30,50,.05)}
.nav a.active .num{color:var(--blue)}
.legend{font-size:11.5px;color:var(--muted);line-height:1.9; padding-top:16px; border-top:1px solid var(--line)}
.legend .k{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:7px;vertical-align:1px}

/* ---------- 主体 ---------- */
main{padding:0 clamp(20px,4vw,64px) 90px; min-width:0}
.hero{
  position:relative; margin:0 auto; max-width:1180px; padding:54px 46px 40px;
  border-radius:22px; overflow:hidden; color:#eaf0f8;
  background:
    radial-gradient(120% 140% at 92% -20%, rgba(23,127,104,.45), transparent 55%),
    radial-gradient(100% 120% at -10% 120%, rgba(42,98,168,.5), transparent 60%),
    linear-gradient(160deg,#101822 10%, #182430 60%, #101a24 100%);
  box-shadow:0 22px 50px -24px rgba(16,26,38,.55);
}
.hero::after{
  content:""; position:absolute; inset:0; pointer-events:none; opacity:.5;
  background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);
  background-size:34px 34px;
  mask-image:radial-gradient(80% 80% at 70% 20%, #000 20%, transparent 100%);
}
.hero>*{position:relative; z-index:1}
.hero-kicker{font-family:var(--mono); font-size:11px; letter-spacing:.22em; text-transform:uppercase; color:#8fb6d8}
.hero h1{margin:14px 0 10px; font-size:clamp(28px,3.4vw,42px); line-height:1.24; letter-spacing:.6px; font-weight:700}
.hero h1 .grad{background:linear-gradient(92deg,#8fd0c0,#a8c8f2 70%);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero-lead{max-width:760px; color:#c3cfdd; font-size:15.5px; line-height:1.85}
.hero-lead code{background:rgba(255,255,255,.09); color:#dbe7f5; padding:1px 5px; border-radius:5px}
.hero-tags{display:flex; flex-wrap:wrap; gap:8px; margin-top:22px}
.hero-tags span{
  font-size:11.5px; color:#c6d4e3; border:1px solid rgba(255,255,255,.16);
  background:rgba(255,255,255,.05); padding:3px 10px; border-radius:999px; letter-spacing:.02em;
}
.stats{display:grid; grid-template-columns:repeat(4,1fr); gap:1px; margin-top:32px;
  border:1px solid rgba(255,255,255,.12); border-radius:14px; overflow:hidden; background:rgba(255,255,255,.1)}
.stat{background:rgba(9,17,26,.45); padding:15px 16px 13px}
.stat b{display:block; font-size:25px; line-height:1.15; font-family:var(--mono); color:#fff; font-weight:600; letter-spacing:-.5px}
.stat b small{font-size:12px; color:#8fa9c2; margin-left:4px; font-family:var(--sans)}
.stat span{display:block; font-size:11.5px; color:#93a8bd; margin-top:5px; letter-spacing:.02em}
.hero-foot{display:flex; flex-wrap:wrap; gap:18px 30px; margin-top:22px; font-size:11.5px; color:#8fa2b6; font-family:var(--mono)}
.hero-foot b{color:#cfe0ef; font-weight:500}

/* ---------- 章节 ---------- */
section{max-width:1180px; margin:0 auto; padding-top:64px; scroll-margin-top:16px}
.sec-head{display:flex; align-items:flex-start; gap:16px; margin-bottom:8px}
.sec-no{
  flex:none; width:40px; height:40px; border-radius:12px; display:grid; place-items:center;
  font-family:var(--mono); font-size:13px; color:var(--blue); background:#eaf1fa;
  border:1px solid #d3e2f4; font-weight:600; margin-top:2px;
}
.sec-kicker{font-family:var(--mono); font-size:10.5px; letter-spacing:.2em; text-transform:uppercase; color:var(--faint)}
h2{font-size:clamp(21px,2.3vw,26px); margin:3px 0 0; letter-spacing:.3px; line-height:1.35; font-weight:700}
.lead{color:var(--muted); font-size:14.6px; line-height:1.9; margin:12px 0 0; max-width:900px}
.lead b{color:var(--ink-2); font-weight:600}
.figure{
  margin:26px 0 0; background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  padding:24px 26px 18px; box-shadow:0 10px 28px -22px rgba(20,32,48,.5);
}
.figure svg{display:block; width:100%; height:auto}
.figcap{display:flex; gap:12px; justify-content:space-between; flex-wrap:wrap; margin-top:14px;
  padding-top:12px; border-top:1px dashed var(--line); font-size:11.5px; color:var(--faint)}
.figcap .src{font-family:var(--mono); font-size:10.8px}
.notes{display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:14px; margin-top:18px}
.note-card{background:var(--card); border:1px solid var(--line); border-radius:12px; padding:15px 17px}
.note-card h4{margin:0 0 6px; font-size:13.2px; color:var(--ink-2); letter-spacing:.02em}
.note-card h4::before{content:"▸"; color:var(--blue); margin-right:7px; font-size:11px}
.note-card p{margin:0; font-size:12.9px; color:var(--muted); line-height:1.78}
.note-card code{color:var(--ink-2); background:#f1f4f9; padding:0 4px; border-radius:4px}
.bullets{margin:16px 0 0; padding:0; list-style:none; display:grid; gap:8px}
.bullets li{position:relative; padding-left:20px; font-size:13.6px; color:var(--muted); line-height:1.85}
.bullets li::before{content:""; position:absolute; left:3px; top:10px; width:6px; height:6px; border-radius:2px;
  background:linear-gradient(135deg,var(--blue),var(--teal))}
.bullets b{color:var(--ink-2); font-weight:600}

/* ---------- 通用小组件 ---------- */
.grid-2{display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:22px}
.grid-3{display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-top:22px}
.tile{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px}
.tile .t{font-size:13.5px; font-weight:600; color:var(--ink-2); margin-bottom:6px}
.tile .d{font-size:12.9px; color:var(--muted); line-height:1.8}
.tile .tb{font-family:var(--mono); font-size:10.5px; color:var(--faint); margin-top:9px; letter-spacing:.02em}
table.matrix{width:100%; border-collapse:collapse; margin-top:20px; background:var(--card);
  border:1px solid var(--line); border-radius:14px; overflow:hidden; font-size:12.8px}
table.matrix th{text-align:left; padding:10px 14px; background:#f7f9fc; color:var(--muted);
  font-weight:600; font-size:11.6px; letter-spacing:.05em; border-bottom:1px solid var(--line)}
table.matrix td{padding:9px 14px; border-bottom:1px solid var(--line-soft); vertical-align:top; color:var(--ink-2)}
table.matrix tr:last-child td{border-bottom:none}
table.matrix td.mono{font-family:var(--mono); font-size:11.6px; color:var(--slate); white-space:nowrap}
table.matrix td.dim{color:var(--muted)}
.tag{display:inline-block; font-family:var(--mono); font-size:10.3px; padding:1px 7px; border-radius:6px;
  background:#eef2f8; color:var(--slate); border:1px solid #dfe6f0; letter-spacing:.02em}
.tag.blue{background:#e9f1fb;color:var(--blue);border-color:#cfe0f5}
.tag.teal{background:#e6f4ef;color:var(--teal);border-color:#c3e3d8}
.tag.amber{background:#fdf4e3;color:var(--amber);border-color:#eed9b0}
.tag.purple{background:#efeafa;color:var(--purple);border-color:#dbcdec}

/* CLI 命令面 */
.cmd-groups{display:grid; grid-template-columns:repeat(2,1fr); gap:16px; margin-top:24px}
.cmd-group{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px}
.cmd-group h4{margin:0 0 2px; font-size:13.6px; color:var(--ink-2)}
.cmd-group .gh{font-family:var(--mono); font-size:11px; color:var(--faint); margin-bottom:10px}
.chips{display:flex; flex-wrap:wrap; gap:6px}
.chip{font-family:var(--mono); font-size:11px; padding:2.5px 8px; border-radius:6px;
  background:#f4f7fb; border:1px solid #e3e9f2; color:var(--slate)}
.chip.core{background:#e9f1fb; border-color:#cfdff4; color:var(--blue); font-weight:600}

/* 验证层级 */
.pyr{display:flex;flex-direction:column;gap:9px;margin-top:24px}
.pyr .lv{width:100%; border-radius:12px; border:1px solid var(--line); background:var(--card);
  display:grid; grid-template-columns:190px 1fr; align-items:center; gap:20px; padding:14px 22px;
  border-left:3px solid var(--blue)}
.pyr .lv.l2{border-left-color:var(--teal)}
.pyr .lv.l3{border-left-color:var(--amber)}
.pyr .lv.l4{border-left-color:var(--clay)}
.pyr .lv .n{font-size:13px; font-weight:600; color:var(--ink-2); line-height:1.5}
.pyr .lv .n small{display:block; font-family:var(--mono); font-size:10.4px; color:var(--faint); font-weight:400; margin-top:3px}
.pyr .lv .d{font-size:12.7px; color:var(--muted); line-height:1.8}
.chip b{color:var(--blue); font-weight:600; margin-left:2px}
.cmd-group p.d{font-size:12.8px; color:var(--muted); line-height:1.8; margin:12px 0 0}
.closing{margin-top:26px; padding:30px 34px; border-radius:18px; color:#dfe9f3;
  background:linear-gradient(140deg,#101a24,#1a2a36 65%,#12212b); border:1px solid #223242}
.closing .q{font-size:clamp(17px,2vw,22px); line-height:1.75; font-weight:600; letter-spacing:.3px}
.closing .a{margin-top:12px; font-size:12.5px; color:#8ea6bc; font-family:var(--mono); letter-spacing:.04em}
.closing .q em{font-style:normal; color:#8fd0c0}
/* 原则 */
.principles{display:grid; grid-template-columns:repeat(2,1fr); gap:16px; margin-top:24px}
.principle{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px; position:relative; overflow:hidden}
.principle::before{content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:linear-gradient(180deg,var(--blue),var(--teal))}
.principle .pno{font-family:var(--mono); font-size:10.5px; color:var(--faint); letter-spacing:.1em}
.principle h4{margin:2px 0 6px; font-size:13.6px; color:var(--ink-2)}
.principle p{margin:0; font-size:12.8px; color:var(--muted); line-height:1.8}
.principle .q{margin-top:8px; font-size:11.8px; color:var(--faint); border-left:2px solid var(--line); padding-left:10px; line-height:1.7}

/* 目录地图 */
.tree{font-family:var(--mono); font-size:12.2px; line-height:2.0; background:#0f1822; color:#c6d5e4;
  border-radius:14px; padding:22px 26px; margin-top:22px; overflow-x:auto; border:1px solid #1d2b3a}
.tree .row{white-space:pre}
.tree .d{color:#8fb6d8; font-weight:600}
.tree .c{color:#7c8ea0}
.tree .hi{color:#8fd0c0}
.tree .warn{color:#e0b978}

/* 结尾 */
.foot{max-width:1180px; margin:70px auto 0; padding:26px 0 0; border-top:1px solid var(--line);
  font-size:12.2px; color:var(--faint); line-height:1.9}
.foot b{color:var(--muted); font-weight:600}
.foot .mono{color:var(--muted)}
.top{position:fixed; right:26px; bottom:26px; width:40px; height:40px; border-radius:12px; display:grid;
  place-items:center; background:var(--card); border:1px solid var(--line); color:var(--blue);
  box-shadow:0 8px 20px -12px rgba(20,32,48,.4); opacity:0; pointer-events:none; transition:.25s; font-size:16px}
.top.on{opacity:1; pointer-events:auto}

/* SVG 交互 */
.figure svg .n{transition:opacity .18s ease}
.figure svg.dim .n{opacity:.28}
.figure svg.dim .n.hot{opacity:1}
.figure svg .n.hot{filter:drop-shadow(0 4px 10px rgba(30,50,80,.16))}

@media (max-width:1080px){
  .shell{grid-template-columns:minmax(0,1fr)}
  .figure{overflow-x:auto}
  .figure svg{min-width:680px}
  .figure .figcap{min-width:680px}
  table.matrix td.mono{white-space:normal}
  .side{position:static; height:auto; border-right:none; border-bottom:1px solid var(--line); padding:20px}
  .side .nav{flex-direction:row; flex-wrap:wrap; gap:4px}
  .nav-label,.legend,.side-meta{display:none}
  .nav a{border-left:none; border-bottom:2px solid transparent; padding:4px 8px}
  .nav a.active{border-left:none; border-bottom-color:var(--blue); box-shadow:none; background:transparent}
  main{padding:0 16px 60px}
  .hero{padding:34px 24px 28px; border-radius:16px}
  .stats{grid-template-columns:1fr 1fr}
  .grid-2,.grid-3,.cmd-groups,.principles{grid-template-columns:1fr}
  .pyr .lv{grid-template-columns:1fr; gap:4px}
  .pyr .lv.l2,.pyr .lv.l3,.pyr .lv.l4{width:100%}
}
"""

JS = r"""
(function(){
  const links=[...document.querySelectorAll('.nav a')];
  const secs=links.map(a=>document.querySelector(a.getAttribute('href'))).filter(Boolean);
  const obs=new IntersectionObserver(es=>{
    es.forEach(e=>{
      if(e.isIntersecting){
        links.forEach(a=>a.classList.toggle('active', a.getAttribute('href')==='#'+e.target.id));
      }
    });
  },{rootMargin:'-12% 0px -70% 0px', threshold:[0]});
  secs.forEach(s=>obs.observe(s));

  const top=document.querySelector('.top');
  addEventListener('scroll',()=>{top.classList.toggle('on', scrollY>900)},{passive:true});
  top.addEventListener('click',()=>scrollTo({top:0,behavior:'smooth'}));

  document.querySelectorAll('.figure svg').forEach(svg=>{
    const nodes=[...svg.querySelectorAll('.n')];
    if(!nodes.length) return;
    svg.addEventListener('mouseover',e=>{
      const g=e.target.closest('.n');
      if(!g) return;
      svg.classList.add('dim');
      nodes.forEach(n=>n.classList.toggle('hot', n===g));
    });
    svg.addEventListener('mouseout',e=>{
      if(e.relatedTarget && svg.contains(e.relatedTarget)) return;
      svg.classList.remove('dim');
      nodes.forEach(n=>n.classList.remove('hot'));
    });
  });

  document.querySelectorAll('[data-copy]').forEach(el=>{
    el.style.cursor='pointer';
    el.title='点击复制';
    el.addEventListener('click',()=>{
      navigator.clipboard && navigator.clipboard.writeText(el.dataset.copy);
    });
  });
})();
"""

TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__DESC__">
<style>__CSS__</style>
</head>
<body>
<div class="shell">
  <aside class="side">
    <div class="brand">
      <div class="brand-mark">VI</div>
      <div>
        <div class="brand-name">auto-civ6</div>
        <div class="brand-sub">architecture map</div>
      </div>
    </div>
    <div class="side-meta">
      版本 <b>__VERSION__</b> · 基线 <b>__COMMIT__</b><br>
      生成于 __DATE__ · 纯离线静态页
    </div>
    <div class="nav-label">阅读路径</div>
    <nav class="nav">__NAV__</nav>
    <div class="legend">
      <span class="k" style="background:#2a62a8"></span>接口与调用面<br>
      <span class="k" style="background:#177f68"></span>状态与确认所有权<br>
      <span class="k" style="background:#a9761c"></span>未决 / 门禁 / 未知<br>
      <span class="k" style="background:#674a9b"></span>领域与 Lua 构造<br>
      <span class="k" style="background:#54637a"></span>传输、进程与证据<br>
      <span class="k" style="background:#9a5340"></span>游戏引擎原生层
    </div>
  </aside>
  <main>
    <header class="hero">
      <div class="hero-kicker">auto-civ6 · architecture visualization · 2026-10-25</div>
      <h1>让模型在真实《文明 VI》里<br><span class="grad">查询、决策、单次提交、确认结果</span></h1>
      <p class="hero-lead">
        <b>AI 只发现并执行 <code>civ6</code> CLI</b>；MCP 降为 CLI 的内部 stdio 通信实现，客户端不注册、不直连。
        游戏引擎执行规则，模型负责策略，运行层负责真实查询、单次提交、结果确认和显式生命周期控制。
        本页从系统全景、运行时分层、动作生命周期、回合推进、会话恢复、领域状态机、CLI 命令面与验证体系展开，
        图中的每条结论都来自当前工作树的源码与文档。
      </p>
      <div class="hero-tags">
        <span>CLI 唯一入口</span><span>内部 stdio MCP</span><span>FireTuner · TCP 4318</span><span>Python 3.12 · uv locked</span>
        <span>Lua 5.1 构造 / 解析</span><span>单次提交 · 有界只读确认</span><span>未决请求不得重发</span>
      </div>
      <div class="stats">__STATS__</div>
      <div class="hero-foot">
        <span>数据来源 <b>src/ · docs/ · tests/</b></span>
        <span>客户端注册 <b>无（CLI 唯一入口）</b></span>
        <span>图形 <b>手写 SVG · 无外部依赖</b></span>
        <span>实机验证 <b>未运行（本页为只读文档产物）</b></span>
      </div>
    </header>
    __SECTIONS__
    <footer class="foot">
      <b>关于本页</b>：为架构可视化一次性生成的静态页面，反映 2026-10-25「CLI 唯一入口」调整后的结构，不参与游戏操作，也不替代
      <span class="mono">docs/architecture.md</span> 的正文契约。图形由 <span class="mono">build.py</span> 生成，
      内容依据当前工作树（基线 <span class="mono">__COMMIT__</span>，含未提交改动）；文中行号指向生成时的源码位置，重构后可能偏移。
      行为边界与验收口径以 <span class="mono">docs/</span> 中的指南、规格和验收记录为准；旧版通过记录只对当时的受测版本有效。
      <br><br>
      <b>配色约定</b>：蓝＝接口与调用面，绿＝状态所有权与已确认路径，琥珀＝未决 / 门禁 / 未知，紫＝领域模块与 Lua 构造，
      灰蓝＝传输、进程与证据，赭＝游戏引擎原生层。
    </footer>
  </main>
</div>
<button class="top" aria-label="回到顶部">↑</button>
<script>__JS__</script>
</body>
</html>
"""


def page(title: str, desc: str, version: str, commit: str, date: str,
         nav: str, stats: str, sections: str) -> str:
    html = TEMPLATE
    for key, value in {
        "__TITLE__": title, "__DESC__": desc, "__VERSION__": version, "__COMMIT__": commit,
        "__DATE__": date, "__CSS__": CSS, "__JS__": JS, "__NAV__": nav,
        "__STATS__": stats, "__SECTIONS__": sections,
    }.items():
        html = html.replace(key, value)
    return html
