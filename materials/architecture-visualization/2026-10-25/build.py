"""生成 auto-civ6 架构可视化页面（单文件 index.html）。

用法：python3 build.py
依赖：仅标准库 + 本目录模块；图形为手写 SVG，不引用任何外部资源。
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess

import diagrams_a
import diagrams_b
import diagrams_c
from shell import page

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]


def fig(svg: str, cap: str, src: str) -> str:
    return (f'<figure class="figure">{svg}'
            f'<div class="figcap"><span>{cap}</span><span class="src">{src}</span></div></figure>')


def notes(items) -> str:
    cards = "".join(f'<div class="note-card"><h4>{t}</h4><p>{d}</p></div>' for t, d in items)
    return f'<div class="notes">{cards}</div>'


def section(no: str, sid: str, kicker: str, title: str, lead: str, body: str) -> dict:
    return {"no": no, "id": sid, "kicker": kicker, "title": title, "lead": lead, "body": body}


# ---------------------------------------------------------------- 章节内容
S1 = section("01", "overview", "System Panorama", "系统全景：CLI 是唯一操作面，MCP 是内部实现",
             "一次操作的真实链路是：<b>Agent 宿主 → civ6 CLI（唯一操作面）→ 内部 stdio MCP → 运行层 → FireTuner → 游戏引擎</b>，"
             "再把回读事实送回模型。客户端不注册 <code>civ6</code> MCP，也不直连内部服务；运行层仍是唯一持有未决状态的地方，"
             "知识包、基准存档、对局证据与离线测试只在旁边提供依据与验证。",
             fig(diagrams_a.overview(),
                 "图 1 · AI 只发现并执行 CLI；CLI 自行拉起内部 MCP。提交向下、事实向上，引擎执行规则、运行层负责确认。",
                 "AGENTS.md#调用入口 · docs/architecture.md#调用关系")
             + notes([
                 ("唯一操作面",
                  "AI 只发现并执行 <code>civ6</code> 命名命令；<code>.mcp.json</code> 默认没有服务注册，"
                  "Codex / Claude / Pi 都只是终端客户端。MCP 是 CLI 的内部通信实现。"),
                 ("单进程 = 单所有者",
                  "一个 <code>civ6 debug</code> 会话只拉起一个内部 MCP 进程、一次 initialize、一份 GameState；"
                  "后续每条命令复用同一进程，终端命令不会重建状态。"),
                 ("内部协议不外露",
                  "<code>call</code> 与 <code>tools</code> 只作同会话协议兼容诊断；任意 Lua 仅 <code>debug --developer</code>；"
                  "热座实验仅 <code>debug --hotseat-seat/--hotseat-run</code>。"),
                 ("离线资产不参与决策",
                  "知识按需只读查询，基准与对局证据只读保留；离线测试覆盖运行层，但“离线通过”不等于“实机通过”。"),
             ]))

S2 = section("02", "layers", "Runtime Layers", "运行时分层：七层单向栈，每层一个主职责",
             "从调用面到引擎，每一层只做一类事：CLI 面把命令翻译成内部调用，工具层管参数与串行，状态层管领域准备与阻塞判断，"
             "确认层拥有未决请求，Lua 层只构造与解析，传输层只负责一次请求一次响应。"
             "<b>lua/、narrate、snapshot 没有向上依赖</b>，因此整张依赖图无环。",
             fig(diagrams_a.layers(),
                 "图 2 · 七层栈与每层的唯一职责；右侧是三条硬约束与各层验证方式。",
                 "src/civ_mcp/{server,game_state,actions,end_turn}.py · lua/ · connection.py")
             + notes([
                 ("CLI 只翻译，工具面不做业务",
                  "CLI 的 142 个命名命令是纯翻译层；<code>server.py</code> 的 90 个工具只负责参数、工具锁、门禁顺序与结果文本，"
                  "领域准备下沉到 <code>game_state.py</code>，避免同一规则出现两份实现。"),
                 ("确认所有权唯一",
                  "ActionLifecycle / TurnProgress / DiplomacyProgress / CongressProgress 各持有一类未决；"
                  "兼容私有属性只代理同一份状态，不保留副本。"),
                 ("失败不追认",
                  "丢回应、超时、身份未知一律保留未决；读取旧状态不触发自动重发，展示文案不承担完成判断。"),
             ]))

S3 = section("03", "modules", "Module Graph", "模块图：依赖边来自真实 import",
             "这张图不是按目录画的，而是按 <code>src/civ_mcp</code> 里的真实 import 关系画的："
             "<code>server.py</code> 只直接依赖 <code>connection / game_state / logger / snapshot</code>；"
             "外交、议会、检查点、进程控制都在 GameState 的对应分支里懒加载，只在需要时进入；"
             "客户端只出现在最左侧的终端层，不进入依赖图。",
             fig(diagrams_a.modules(),
                 "图 3 · 模块依赖与规模：蓝色为入口与接口，绿色为状态，琥珀为确认所有者，紫色为 Lua 构造。",
                 "src/civ_mcp/*.py · 行数来自当前工作树 wc -l")
             + notes([
                 ("入口薄、状态厚",
                  "<code>cli.py</code> 与 <code>devtools/</code> 合计约 2k 行，只做参数翻译与证据落盘；"
                  "约 4.9k 行的 server + game_state 才是行为主体。"),
                 ("Lua 是最大的一块",
                  "<code>lua/</code> 48 个模块、18,694 行，几乎与其余 Python 等量：每个游戏领域都有一份构造器与解析器。"),
                 ("脚本另起进程",
                  "<code>scripts/</code> 下的 relay、热座与验收驱动都不进入运行时 import 图；"
                  "它们通过 CLI 或 stdio MCP 从外部驱动同一套工具。"),
             ]))

S4 = section("04", "action", "Action Lifecycle", "动作生命周期：单次提交，有界只读确认",
             "所有普通动作都走同一个 <code>ActionLifecycle</code>：提交前用 <code>prepare_scope</code> 绑定 "
             "<b>civ / seed / turn / owner</b> 四字段；提交时只发一次，并在 Lua 内做二次 scope 守卫；"
             "之后只用只读查询回读证据。证据命中才退役，丢回应则保留原请求。",
             fig(diagrams_b.action_lifecycle(),
                 "图 4 · 六态状态机（含“失效锁存”这个只进不出的终态）与 scope 锁存规则。",
                 "src/civ_mcp/actions.py:33-400 · docs/acceptance/2026-10-19-mutation-lifecycle.md")
             + notes([
                 ("为什么先赋值 pending",
                  "提交在写操作前就把 pending 挂到 owner 上：即使执行抛错，也一定有人对这次尝试负责，"
                  "避免“可能已执行但没人知道”的空档。"),
                 ("未知不遮蔽，原值不追认",
                  "scope 四个字段独立判断：某字段未知就跳过，不能遮蔽其他字段的真实变化；"
                  "一旦有效值不匹配就永久置位失效，返回原值也不能解除。"),
                 ("同一动作的唯一出口",
                  "确认、精确 pre-entry 拒绝、显式会话替换是仅有的三条清除路径；其余一律保留未决。"),
                 ("证据因动作而异",
                  "移动保留 MOVE_WITNESS，攻击保留 pre_hp / fingerprint，建城保留 position + 查询串；"
                  "确认只读，绝不调用提交语句。"),
             ]))

S5 = section("05", "turn", "Turn Progress", "回合推进：观测面与请求面共用一套阶段解释",
             "回合推进不是“点一下 Next Turn”：<code>get_turn_status</code> 与 <code>end_turn</code> 共用"
             " <code>read_turn_status</code>、外交/议会/弹窗探针，但只有 <code>end_turn</code> 会建立 pending。"
             "确认三条件是：<b>回合增长 + ready + 原世界身份匹配</b>，缺一即保留未决。",
             fig(diagrams_b.turn_loop(),
                 "图 5 · 观测面的 6 个原生阶段，请求面的提交/轮询/确认，以及必须由模型显式解决的阻塞出口。",
                 "src/civ_mcp/end_turn.py · lua/{notifications,turn_readiness}.py")
             + notes([
                 ("三条件确认",
                  "turn_after &gt; from_turn、ready（active 且 complete 且 ui=False）、world 匹配，三者同时成立才算推进；"
                  "任一读失败只影响诊断，不改写已确认事实。"),
                 ("预算不是死锁判据",
                  "180s 轮询上限只是观察预算；超时返回 WAITING 并保留请求，与真实读取失败分开统计。"),
                 ("阻塞要模型自己解决",
                  "生产、科研、政策、单位、议会、外交、弹窗等阻塞都映射到具体工具，推进工具只报告不代选。"),
                 ("本地被重开是有效结局",
                  "收到原生 TURN_REOPENED 证据时按 REOPENED_LOCAL 处理并退役本次尝试，而不是继续等待或重发。"),
             ]))

S6 = section("06", "seq", "Wire Sequence", "端到端时序：一次动作在六个进程角色之间的往返",
             "以命名命令 <code>unit move</code> 为例：模型执行 CLI 命令，CLI 经内部 stdio MCP 把请求送进工具锁、门禁、领域准备、"
             "Lua 构造与 FireTuner；回执按 <code>uuid</code> 前缀匹配；确认阶段是另一串只读往返。整条链上只有一个地方能“再次提交”——"
             "而代码里没有这样的地方。",
             fig(diagrams_b.sequence(),
                 "图 6 · 单次提交（①）与有界只读确认（②）；右侧是结果未知时的保留分支。",
                 "src/civ_mcp/{server,game_state,actions,connection}.py · lua/units.py")
             + notes([
                 ("消息包络",
                  "每个请求被包上唯一 uuid，只接收带本请求前缀的行，以 <code>CIVMCP|&lt;uuid&gt;|END</code> 作为完成标记。"),
                 ("确认不提交",
                  "确认循环只执行 pending 里预先准备好的只读查询；这也是“同意图重入只确认”的实现基础。"),
                 ("未知分支显式存在",
                  "结果未知不是异常路径，而是正常状态：请求保留、模型获得指引、后续调用只确认旧请求。"),
             ]))

S7 = section("07", "session", "Session & Checkpoints", "会话替换与检查点：三态判定与两条门禁",
             "换档、读档、冷恢复是三类显式替换，共用同一套加载确认。GameState 是唯一替换所有者；"
             "<code>replacement_pending</code> / <code>save_pending</code> 在证据链走完前一直保持——"
             "而“只有调用前可证明未调用，才允许 not_submitted”是这套状态机的铁律。",
             fig(diagrams_c.session(),
                 "图 7 · 三个入口汇聚到同一加载确认；三态判定铁律与检查点记录字段。",
                 "src/civ_mcp/{game_state,game_lifecycle,game_launcher,checkpoints}.py")
             + notes([
                 ("进调用后只能 unknown",
                  "一旦原生调用已进入，任何异常、取消、断线都只能是 unknown 并保持门禁；"
                  "not_submitted 只用于调用前就能证明未发生的路径。"),
                 ("保存的完成证据",
                  "原生 SaveComplete + 指纹变化且 size&gt;0 + 复检指纹仍一致，三者都要满足才算保存完成。"),
                 ("检查点绑定轮次",
                  "名称、sha256、文明、种子、回合、run_id 组合成检查点记录；只有加载原始基准才轮换新的测试轮次。"),
                 ("遗留模块",
                  "<code>autosave.py</code> 已无调用方，自动存档猜测被显式禁用——这条也写进了规格。"),
             ]))

S8 = section("08", "domains", "Domain Machines", "领域状态机：外交交易与议会，各一套专用所有权",
             "外交与交易各有专用 Progress（<code>DiplomacyProgress</code> / <code>IncomingAttempt</code> / "
             "<code>TradeAttempt</code>），却共用同一套 observer 与 nonce；世界议会则把"
             "<b>原生阶段</b>与<b>本地请求状态</b>拆成两个正交维度，再用三态解释统一查询、打开与投票。",
             fig(diagrams_c.domains(),
                 "图 8 · 交易的双向状态机与世界议会的双层阶段机；底部是 REGISTERED 的 end_turn 特例。",
                 "src/civ_mcp/{diplomacy,congress}.py · lua/{trade,congress}.py")
             + notes([
                 ("身份仲裁是三值逻辑",
                  "身份完整且不同 → scope_invalid 永久失效；身份缺失 → 仅本次不能确认；"
                  "返回原身份不能解除已锁存的失效。"),
                 ("零提交也要回执",
                  "来约的 ZERO 回执必须与 finish(zero=True) 一起 CONFIRMED，才能报告“未提交”；"
                  "仅凭界面没变化不算。"),
                 ("议会三态同源",
                  "CHOICES / EMPTY / UNKNOWN 由同一个 HasChoices 复算：无提案时不走开始路径，"
                  "以免原生 UI 自动提交与显式票计划重复。"),
                 ("REGISTERED 的破例",
                  "已登记的票单只能由显式 end_turn 放行；confirm 每次先把本地 state 重置为 UNKNOWN，"
                  "不照搬普通动作的同回合严格规则。"),
             ]))

# ---------------------------------------------------------------- CLI 命令面（HTML）
CLI_TOP = [
    ("doctor", "解释器、依赖、知识包完整性、内部 stdio MCP 初始化与工具发现", "离线 · 会起内部子进程但不碰游戏", "0 / 1 / 2"),
    ("verify knowledge", "CLI 与真实 MCP 逐字段对比知识查询，核对知识文件未变", "离线", "0 / 1 / 2"),
    ("verify state", "有界只读实机验收：pending → overview → snapshot → 身份/pending 复核", "实机 · 只读", "0 / 1 / 2"),
    ("test", "运行 7 个已登记域中某一域的离线测试；debug 域现含 CLI-only 契约", "离线", "0 / 1 / 2"),
    ("debug", "唯一操作会话：一个内部 MCP 进程 + 一份 GameState，stdin 行 → JSONL", "实机 · 首次真正调用时", "0 / 2 / 130"),
    ("logs", "只读既有动作日志：list / show / timeline / turns", "离线", "0 / 2"),
]
CLI_GROUPS = [
    ("unit", 39), ("city", 11), ("research", 2), ("turn", 2), ("save", 6), ("knowledge", 2),
    ("map", 5), ("victory", 1), ("policy", 2), ("city-state", 2), ("governor", 4), ("congress", 3),
    ("notifications", 1), ("government", 3), ("religion", 6), ("great-person", 6), ("empire", 4),
    ("era", 3), ("ranking", 1), ("civilization", 3), ("technology", 3), ("civic", 3),
    ("climate", 2), ("history", 1), ("resources", 1), ("session", 1), ("game", 4),
    ("diplomacy", 7), ("trade", 7), ("spy", 2), ("popup", 1), ("developer", 1), ("hotseat", 3),
]
CLI_SESSION = ["help", "tools [NAME]", "pending", "state overview|units|cities|turn|snapshot",
               "verify state", "call [--dry-run] TOOL JSON（协议兼容）",
               "developer lua（仅 --developer）", "hotseat begin|end|actions（仅热座模式）",
               "quit [--abandon]"]

cli_cards = "".join(
    f'<div class="tile"><div class="t">{name}</div><div class="d">{desc}</div>'
    f'<div class="tb">{game} · 退出码 {code}</div></div>'
    for name, desc, game, code in CLI_TOP)
group_chips = "".join(f'<span class="chip">{g} <b>{n}</b></span>' for g, n in CLI_GROUPS)
session_chips = "".join(f'<span class="chip core">{c}</span>' for c in CLI_SESSION)

S9 = section("09", "cli", "Command Surface", "CLI 命令面：5 个顶层命令 + 33 组 / 142 个命名子命令",
             "<code>civ6</code> 是 AI 唯一操作入口，不是游戏服务：顶层 5 个命令 + 前台 <code>debug</code> 会话 + 33 组 / 142 个命名子命令。"
             "子命令是纯翻译层：<b>子命令 → Intent(tool, arguments, dry_run)</b>，不持有第二份状态；"
             "90 个基础工具全部有命名入口，<code>call</code> 只作协议兼容诊断。",
             f'''<div class="grid-3">{cli_cards}</div>
<div class="grid-2">
  <div class="cmd-group">
    <h4>debug 会话内命令</h4>
    <div class="gh">一个会话 = 一个内部 MCP 进程 = 一份 GameState</div>
    <div class="chips">{session_chips}</div>
    <p class="d" style="margin-top:12px">发送前四道门禁：schema 预校验 → dry-run → offline 白名单（4 个工具）→ transport/identity 未知时只许只读；
    待决时会话拒绝正常退出（<code>PENDING_ON_EXIT</code>）。普通会话 <code>tools</code> 不公开 <code>run_lua</code>，任意 Lua 需 <code>--developer</code>。</p>
  </div>
  <div class="cmd-group">
    <h4>输出契约</h4>
    <div class="gh">stdout 业务 JSON / JSONL · stderr 提示 · 证据目录永不覆盖</div>
    <div class="chips">
      <span class="chip">schema_version=1</span><span class="chip">run_id</span><span class="chip">command</span>
      <span class="chip">mode</span><span class="chip">game_verification</span><span class="chip">status</span>
      <span class="chip">exit_code</span><span class="chip">git commit/dirty</span><span class="chip">data</span><span class="chip">errors</span>
    </div>
    <p class="d" style="margin-top:12px">退出码：<b>0 PASS · 1 FAIL · 2 BLOCKED · 130 中断</b>。默认证据目录
    <code>.scratch/civ6/&lt;run_id&gt;/</code>，含 <code>summary.json</code>、<code>context.json</code>（源码 SHA-256 + git 状态）与逐命令回执。</p>
  </div>
</div>
<div class="cmd-group" style="margin-top:16px">
  <h4>33 组 / 142 个命名子命令</h4>
  <div class="gh">90 个基础工具全部有命名入口；普通会话可见 89 个（run_lua 仅 --developer）；另有 3 个热座实验命令</div>
  <div class="chips">{group_chips}</div>
</div>
<div class="grid-3" style="margin-top:16px">
  <div class="tile"><div class="t">普通会话（默认）</div><div class="d">命名命令 + <code>state</code>/<code>pending</code> 诊断；<code>run_lua</code> 不在工具列表，<code>call run_lua</code> 发送前拒绝；适合所有正常对局。</div><div class="tb">civ6 debug</div></div>
  <div class="tile"><div class="t">开发诊断</div><div class="d">唯一开放任意 Lua 的模式；<code>developer lua</code> 仍需 <code>--dry-run</code> 预演，正式执行仍受 Runtime 未决门禁约束。</div><div class="tb">civ6 debug --developer</div></div>
  <div class="tile"><div class="t">热座实验</div><div class="d">复用原双人控制器，使用 <code>hotseat begin/end/actions</code>；不与 <code>--offline/--developer</code> 合用，普通 <code>turn end</code> 不可交接。</div><div class="tb">civ6 debug --hotseat-seat N --hotseat-run DIR</div></div>
</div>
<div class="grid-2" style="margin-top:16px">
  <div class="tile">
    <div class="t">一次 debug 会话的证据目录（新建且永不覆盖）</div>
    <div class="d" style="font-family:var(--mono); font-size:11.6px; line-height:1.9; white-space:pre">.scratch/civ6/&lt;run_id&gt;/
  summary.json          与 stdout 单 JSON 一致
  context.json          解释器 + 源码 SHA-256 + git 状态
  debug.jsonl           事件流：command / tool_started / tool_result
  debug-initialize.json 内部 MCP initialize 回执
  runtime-status.json   会话身份与六类 pending 事实
  turn-timing.json      每回合耗时（只由已确认 turn end 划界）</div>
  </div>
  <div class="tile">
    <div class="t">relay：单会话、多客户端的包装层</div>
    <div class="d"><code>scripts/civ6_cli_relay.py</code> 不替代 CLI：它持有唯一的 <code>civ6 debug</code> 子进程 stdin/stdout，
    再用本机 socket 把请求转发给多个客户端。一次请求对应一条 CLI 命令，request_id 防重放；结果未知时不替换会话上下文。
    热座实验走固定席位入口，不使用 relay。</div>
    <div class="tb">入口：AGENTS.md → docs/cli.md → civ6 debug · 客户端不注册 MCP</div>
  </div>
</div>''')

# ---------------------------------------------------------------- 验证体系（HTML）
PYR = [
    ("l1", "1 运行层单元与契约", "2,146 个测试函数 / 132 个文件", "FakeConn、内存 FireTuner 流、fake_debug_server（真 MCP dispatcher，只换引擎边界）；断言可观察行为，不复刻状态机。"),
    ("l2", "2 CLI 契约与 Lua 执行", "test_cli_only · lupa · 真 MCP schema", "命名命令逐一对照真实工具 schema；在 Lua 5.1 里执行真实的构造器，验证生成的语句与解析器，而不是断言源码字符串。"),
    ("l3", "3 只读实机验收", "civ6 verify state · get_game_snapshot", "9 项检查全通过才给 ready；任何 pending 在 I/O 前拒绝，不推进、不重发、不自动恢复。"),
    ("l4", "4 实机推进与热座", "integration_turns · debug --hotseat-seat", "固定基准 + 显式检查点；热座仅复用既有双人实验；结果未知、挂起或预算耗尽即退出，不自动恢复／重发。"),
]
pyr_html = "".join(f'<div class="lv {c}"><div class="n">{t}<small>{m}</small></div><div class="d">{d}</div></div>'
                   for c, t, m, d in PYR)
DOUBLES = [
    ("fake_tuner.py", "只替换 5 个传输入口：让真实 GameState / ActionLifecycle 跑在脚本化回放上。"),
    ("fake_debug_server.py", "用真实 MCP dispatcher 加假引擎边界，验证 CLI 会话协议与门禁顺序。"),
    ("action_scope_support.py", "构造 scope / 身份 / 见证回执，覆盖锁存与“不追认”路径。"),
    ("conftest.py", "两个 opt-in fixture：已提交知识快照复用、显式缩短的动作确认等待。"),
]
double_cards = "".join(f'<div class="tile"><div class="t mono">{n}</div><div class="d">{d}</div></div>' for n, d in DOUBLES)

S10 = section("10", "validation", "Verification", "验证体系：离线通过、实机通过、未验证要分开写",
              "项目把验证分成四层：离线单元与契约、Lua 真实执行、只读实机验收、实机推进与热座实验。"
              "每层都有明确的入口与证据目录；<b>旧通过的记录只对当时的受测版本有效</b>，不能替代下一次实测。",
              f'''<div class="pyr">{pyr_html}</div>
<div class="grid-2" style="margin-top:22px">{double_cards}</div>
<div class="grid-2">
  <div class="tile">
    <div class="t">证据链：从规格到对局</div>
    <div class="d"><b>docs/specs</b>（19 篇规格 + 4 组专题）定义行为契约 → <b>docs/acceptance</b>（24 篇，含
    <a href="../../docs/acceptance/2026-10-25-cli-only.md">CLI 唯一入口验收</a>）记录离线验收与覆盖口径 →
    <b>fixtures/saves</b>（3 个实名基准 + SHA-256 / 种子）提供受信起点 →
    <b>games/&lt;对局&gt;/runs/&lt;轮次&gt;/</b> 保存回合记录、检查点、报告与本地证据。</div>
    <div class="tb">同一文明与种子 ≠ 同一测试轮次 · 旧通过记录只对当时版本有效</div>
  </div>
  <div class="tile">
    <div class="t">知识包校验</div>
    <div class="d">知识不是自由文本：<code>query.py validate</code> 做重建比对 + counts + coverage + 指纹；
    <code>civ6 verify knowledge</code> 用同一知识包分别走 CLI 与真实 MCP，逐字段对比并核对输入文件未变。
    注意 <code>jsonschema</code> 在仓库里只校验 MCP 工具入参，不用于知识数据。</div>
    <div class="tb">civ6_knowledge/ · 34 个文件 · 验收记录 400 项通过</div>
  </div>
</div>''')

# ---------------------------------------------------------------- 目录地图（HTML）
TREE = [
    ("<span class='d'>src/civ_mcp/</span>", "<span class='c'>运行时：server · game_state · 确认所有者 · 传输</span>"),
    ("  ├─ <span class='hi'>lua/</span>", "<span class='c'>48 个领域 Lua 构造器与解析器（18,694 行）</span>"),
    ("  ├─ <span class='hi'>devtools/</span>", "<span class='c'>CLI 命令翻译、debug 会话、证据与耗时统计</span>"),
    ("  └─ connection.py · tuner_client.py", "<span class='c'>FireTuner 传输：TCP 127.0.0.1:4318</span>"),
    ("<span class='d'>civ6_knowledge/</span>", "<span class='c'>离线知识包：规则 / 文明 / 策略 + 校验脚本</span>"),
    ("<span class='d'>docs/</span>", "<span class='c'>指南 · 架构 · CLI 手册 · specs/ · acceptance/</span>"),
    ("<span class='d'>games/</span>", "<span class='c'>每局 runs/&lt;轮次&gt;/：reports · changes · evidence · checkpoints</span>"),
    ("<span class='d'>fixtures/saves/</span>", "<span class='c'>3 个受信原始基准 + 身份与指纹记录</span>"),
    ("<span class='d'>scripts/</span>", "<span class='c'>relay · 热座双 AI · 验收驱动（另起进程）</span>"),
    ("<span class='d'>tests/</span>", "<span class='c'>132 个测试文件 / 2,146 个测试函数 + 测试替身</span>"),
    ("<span class='d'>.mcp.json · .gemini/settings.json</span>", "<span class='c'>空的 mcpServers：客户端不注册 civ6 MCP</span>"),
    ("<span class='warn'>materials/</span>", "<span class='c'>演示、分享与交付资料（含本页）</span>"),
]
DIRS = [
    ("src/civ_mcp/server.py", "90 个 MCP 工具、整体工具锁、门禁顺序、结果文本", "2,336"),
    ("src/civ_mcp/game_state.py", "领域 interface、观察缓存、完整存档替换、阻塞判断", "2,573"),
    ("src/civ_mcp/actions.py", "ActionLifecycle：单次提交与只读确认", "795"),
    ("src/civ_mcp/end_turn.py", "TurnProgress：推进请求、基线、轮询确认", "717"),
    ("src/civ_mcp/diplomacy.py · congress.py", "外交交易与议会两套专用 Progress", "626 · 162"),
    ("src/civ_mcp/lua/", "按游戏领域构造与解析 Lua", "18,694"),
    ("src/civ_mcp/devtools/", "CLI 命令面、debug 会话、证据与 timing", "1,768"),
    ("scripts/", "relay、热座控制器、验收驱动（不进入运行时）", "—"),
    ("tests/", "离线测试与替身", "39,741"),
]
dir_rows = "".join(f'<tr><td class="mono">{p}</td><td class="dim">{d}</td><td class="mono">{n}</td></tr>'
                   for p, d, n in DIRS)
tree_html = "".join(f'<div class="row">{a}{"  " if b else ""}{b}</div>' for a, b in TREE)

S11 = section("11", "repo", "Repository Map", "目录地图：代码、知识与证据各自归位",
              "仓库按“可复用的机制 / 跨对局的规格 / 单局的证据”分层：<code>docs/</code> 放跨对局复用的指南与契约，"
              "<code>games/</code> 放单局过程与实机证据，<code>materials/</code> 放演示与交付资料。",
              f'''<div class="tree">{tree_html}</div>
<table class="matrix">
<tr><th>路径</th><th>职责</th><th>行数</th></tr>{dir_rows}
</table>''')

# ---------------------------------------------------------------- 原则（HTML）
PRINCIPLES = [
    ("先读真实状态", "回合、坐标与实体 ID 必须来自真实游戏状态；不可见、未知与不存在要分开对待。",
     "AGENTS.md · 必读约束"),
    ("单次提交", "一个动作只提交一次；提交前绑定世界身份，提交后只用只读查询确认。", "docs/architecture.md · 动作确认"),
    ("未决即门禁", "保存/加载、外交、推进、议会与普通动作彼此阻塞；身份查询与缓存刷新不能清 pending，只有 reset_session 退役。", "AGENTS.md · 必读约束"),
    ("不重发", "丢回应、超时、身份未知一律保留未决；读取旧状态不触发自动重发。", "connection.py · execute_write"),
    ("身份绑定与锁存", "世界身份不匹配即永久失效；未知字段不遮蔽，返回原值不追认。", "actions.py · _ActionScope"),
    ("CLI 唯一操作面", "AI 只发现并执行 civ6 命名命令；客户端不注册 MCP，call 仅协议兼容诊断，任意 Lua 仅 --developer。", "AGENTS.md · 调用入口"),
    ("展示不承担判断", "原生事实与展示文案分离：ProcessResult / NativeTransition 才是完成判据。", "docs/architecture.md · 会话替换"),
    ("离线与实机分开", "离线检查不碰游戏；只有任务授权才操作实机，并从受信基准开始。", "docs/development.md"),
]
principle_cards = "".join(
    f'<div class="principle"><div class="pno">{i:02d} / 08</div><h4>{t}</h4>'
    f'<p>{d}</p><div class="q">{q}</div></div>'
    for i, (t, d, q) in enumerate(PRINCIPLES, 1))

S12 = section("12", "principles", "Design Rules", "八条设计规则：这套系统为什么长这样",
              "把上面所有机制压缩成八条规则。它们不是口号：每一条都能在代码里找到对应的实现位置，"
              "也都能在 <code>docs/</code> 里找到对应的验收记录。",
              f'<div class="principles">{principle_cards}</div>'
              '<div class="closing"><div class="q">“游戏引擎执行规则；模型负责策略；'
              '运行层负责真实查询、<em>单次提交</em>、<em>结果确认</em>和显式生命周期控制。”</div>'
              '<div class="a">— AGENTS.md · 项目定位（本页所有图都在这句话的约束下展开）</div></div>')

SECTIONS = [S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12]


def render() -> str:
    nav = "".join(f'<a href="#{s["id"]}"><span class="num">{s["no"]}</span>{s["title"].split("：")[0]}</a>'
                  for s in SECTIONS)
    blocks = []
    for s in SECTIONS:
        blocks.append(f'''<section id="{s['id']}">
  <div class="sec-head">
    <div class="sec-no">{s['no']}</div>
    <div><div class="sec-kicker">{s['kicker']}</div><h2>{s['title']}</h2></div>
  </div>
  <p class="lead">{s['lead']}</p>
  {s['body']}
</section>''')
    stats = "".join(f'<div class="stat"><b>{v}<small>{u}</small></b><span>{k}</span></div>'
                    for v, u, k in [("90", "", "基础工具（全部有命名入口）"),
                                    ("142", "", "命名子命令（33 组，含热座）"),
                                    ("6", "", "类未决门禁（各自独立所有者）"),
                                    ("2,146", "", "离线测试函数（132 个文件）")])
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "unknown"
    date = datetime.date.today().isoformat()
    return page(
        title="auto-civ6 架构全景（CLI 唯一入口）· 2026-10-25",
        desc="AI 只通过 civ6 CLI 操作《文明 VI》：查询、决策、单次提交与确认的工程架构可视化。",
        version="1.1.11", commit=commit, date=date, nav=nav, stats=stats,
        sections="\n".join(blocks),
    )


if __name__ == "__main__":
    out = HERE / "index.html"
    out.write_text(render(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KB)")


# ---------------------------------------------------------------- QA 辅助（单章预览）
def qa_pages() -> None:
    """为视觉检查生成“只含一章”的临时页面，写入 .scratch（不进入交付物）。"""
    import shell
    out = REPO / ".scratch/arch-viz-20261025/qa"
    out.mkdir(parents=True, exist_ok=True)
    for s in SECTIONS:
        html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>{shell.CSS}</style></head><body>
<div class="shell"><aside class="side">
<div class="brand"><div class="brand-mark">VI</div><div><div class="brand-name">auto-civ6</div>
<div class="brand-sub">qa fragment</div></div></div></aside>
<main><section id="{s['id']}">
  <div class="sec-head"><div class="sec-no">{s['no']}</div>
  <div><div class="sec-kicker">{s['kicker']}</div><h2>{s['title']}</h2></div></div>
  <p class="lead">{s['lead']}</p>{s['body']}</section></main></div>
</body></html>"""
        (out / f"{s['id']}.html").write_text(html, encoding="utf-8")
    print(f"wrote {len(SECTIONS)} qa pages to {out}")
