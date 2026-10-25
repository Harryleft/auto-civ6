"""图 1–2：系统全景、运行时分层。"""

from __future__ import annotations

from vizkit import PALETTE, INK, MUTED, FAINT, LINE, BG, BG_SOFT, Svg, tw, wrap


# ------------------------------------------------------------------ 图 1 系统全景
def overview() -> str:
    s = Svg(1160)
    main_x, main_w = 110, 600
    cx = main_x + main_w / 2

    s.card(main_x, 30, main_w, 68, "人类用户 / 任务授权",
           ["自然语言目标 · 指定存档 · 明确的操作授权边界"], "slate", title_size=15.5)
    s.arrow([(cx, 98), (cx, 132)], "slate")
    s.arrow_label(cx + 12, 120, "任务与授权", "slate", anchor="start")

    p = PALETTE["blue"]
    s.add('<g class="n">')
    s.rect(main_x, 134, main_w, 106, p["soft"], p["edge"], 14, 1.4)
    s.rect(main_x + 14, 134, main_w - 28, 3.5, p["c"], None, 2, 1)
    s.text(main_x + 22, 166, "Agent 宿主", 16, p["deep"], "start", 600)
    s.text(main_x + 116, 166, "Codex · Claude Code · Pi", 11.5, MUTED, "start")
    for i, (t, d) in enumerate([("读文档入口", "AGENTS.md → 按任务取文档"),
                                ("执行 CLI 命令", "civ6 doctor / debug"),
                                ("做策略决策", "引擎执行规则，模型只做选择")]):
        bx = main_x + 22 + i * 186
        s.rect(bx, 182, 176, 44, "#ffffff", p["edge"], 10, 1.2)
        s.text(bx + 12, 200, t, 11.5, p["deep"], "start", 600)
        s.text(bx + 12, 216, d, 9.8, MUTED, "start")
    s.add("</g>")

    s.arrow([(cx, 240), (cx, 264)], "blue")
    s.arrow_label(cx + 12, 256, "执行 CLI 命令 / 读取 JSONL", "blue", anchor="start")

    # 唯一操作面：CLI
    s.add('<g class="n">')
    s.rect(main_x, 266, main_w, 126, p["soft"], p["edge"], 14, 1.6)
    s.rect(main_x + 14, 266, main_w - 28, 3.5, p["c"], None, 2, 1)
    s.text(main_x + 22, 296, "civ6 CLI", 17, p["deep"], "start", 600)
    s.text(main_x + 112, 296, "AI 唯一操作面", 11.5, p["c"], "start", 600)
    s.text(main_x + 22, 316, "顶层 5 命令 · debug 前台会话 · 33 组 / 142 个命名子命令", 10.5, MUTED, "start")
    s.text(main_x + 22, 334, "一个持续会话 = 一个内部 stdio MCP 进程 = 一份 GameState", 10.5, MUTED, "start")
    for i, c in enumerate(["doctor", "verify", "test", "debug", "logs"]):
        bx = main_x + 22 + i * 84
        s.rect(bx, 346, 74, 26, "#ffffff", p["edge"], 8, 1.1)
        s.text(bx + 37, 364, c, 10, p["c"], weight=600, family="var(--mono)")
    s.text(main_x + 470, 364, "客户端不注册 MCP", 10, PALETTE["amber"]["deep"], "start", 600)
    s.add("</g>")

    s.arrow([(cx, 392), (cx, 420)], "blue")
    s.arrow_label(cx + 12, 410, "内部 stdio JSON-RPC（CLI 自行拉起）", "blue", anchor="start")

    # Runtime
    p = PALETTE["teal"]
    s.add('<g class="n">')
    s.rect(main_x, 422, main_w, 160, p["soft"], p["edge"], 14, 1.4)
    s.rect(main_x + 14, 422, main_w - 28, 3.5, p["c"], None, 2, 1)
    s.text(main_x + 22, 452, "Runtime 运行层（内部 stdio MCP · 单进程）", 15, p["deep"], "start", 600)
    s.text(main_x + 22, 470, "MCP 只是 CLI 的内部通信实现；查询、单次提交、确认与门禁都在这里", 10.5, MUTED, "start")
    chips = [("server.py", "工具面 · 90+3 工具"), ("game_state.py", "领域 interface · 缓存"),
             ("ActionLifecycle", "未决动作唯一所有者"), ("TurnProgress", "推进请求与基线"),
             ("Diplomacy / Congress", "多阶段会话与票单"), ("lua/", "按领域构造与解析")]
    for i, (t, d) in enumerate(chips):
        bx = main_x + 18 + (i % 3) * 190
        by = 486 + (i // 3) * 44
        s.rect(bx, by, 180, 38, "#ffffff", p["edge"], 9, 1.1)
        s.text(bx + 10, by + 16, t, 10.6, p["deep"], "start", 600)
        s.text(bx + 10, by + 30, d, 9.5, MUTED, "start")
    s.add("</g>")

    s.arrow([(cx, 582), (cx, 610)], "teal")
    s.arrow_label(cx + 12, 600, "Lua 请求帧 · 一次一提交", "teal", anchor="start")

    s.card(main_x, 612, main_w, 80, "FireTuner 传输 · connection.py / tuner_client.py",
           ["TCP 127.0.0.1:4318 · 单次请求单次响应 · 超时隔离 · 无自动重放"], "slate", title_size=15)
    s.arrow([(cx, 692), (cx, 720)], "slate")
    s.arrow_label(cx + 12, 710, "原生 Lua 调用", "slate", anchor="start")

    q = PALETTE["clay"]
    s.add('<g class="n">')
    s.rect(main_x, 722, main_w, 104, q["soft"], q["edge"], 14, 1.4)
    s.rect(main_x + 14, 722, main_w - 28, 3.5, q["c"], None, 2, 1)
    s.text(main_x + 22, 754, "Civilization VI 游戏引擎", 16, q["deep"], "start", 600)
    s.text(main_x + 22, 772, "规则、合法性与真实世界状态由引擎裁决；任何写入都必须经过它", 10.5, MUTED, "start")
    for i, (t, d) in enumerate([("GameCore", "玩家 / 城市 / 单位 / 科研"), ("InGame", "界面上下文 · 单位与城市"),
                                ("FrontEnd", "主菜单 · 读档入口")]):
        bx = main_x + 22 + i * 186
        s.rect(bx, 784, 176, 30, "#ffffff", q["edge"], 8, 1.1)
        s.text(bx + 10, 804, f"{t} · {d}", 9.5, q["deep"], "start", 500)
    s.add("</g>")

    # 左侧：回读通路
    s.arrow([(main_x, 774), (44, 774), (44, 187), (main_x, 187)], "green", dash="7 6", sw=1.7)
    for i, ch in enumerate("回读事实与证据"):
        s.text(30, 384 + i * 15, ch, 10.4, PALETTE["green"]["deep"], weight=600)

    # 右侧：离线资产与验证证据
    s.panel(742, 134, 380, 688, "离线资产与验证证据", "slate", dash="7 6")
    s.text(758, 176, "运行时不依赖它们做玩法决策；它们界定「依据什么、怎么验证」", 10.2, FAINT, "start")
    assets = [
        ("知识包 civ6_knowledge/", ["规则、文明、科技树、策略与胜利条件", "schema 校验的离线数据 · 按需查询"], "teal"),
        ("受信基准 fixtures/saves/", ["benchmark_turn_1 · gold_tun_1 · gold_v2", "原始基准 + 精确文件指纹，不覆盖"], "slate"),
        ("对局证据 games/", ["回合记录 · 检查点 · 报告 · 终局截图", "每轮独立 runs/<日期-轮次>/"], "amber"),
        ("规格与验收 docs/specs · acceptance", ["行为契约、覆盖基线、审查记录", "旧通过记录只对当时版本有效"], "blue"),
        ("离线测试 tests/", ["132 个测试文件 · FakeConn / 内存 Tuner 流", "Lua 5.1 执行真实构造器 · 不连游戏"], "purple"),
    ]
    for i, (t, subs, color) in enumerate(assets):
        y = 194 + i * 104
        s.card(758, y, 348, 92, t, subs, color, title_size=13.2, sub_size=10.2)
    s.rect(758, 724, 348, 1, LINE, None, 0, 1)
    s.lines(758, 746, ["MCP 是内部协议：客户端不注册 civ6 MCP，也不直连。",
                       "call/tools 只作协议兼容诊断；任意 Lua 仅 debug --developer；",
                       "热座实验仅经 debug --hotseat-seat/--hotseat-run。"], 10.0, FAINT, "start", lh=17)

    s.legend(110, 866, [("blue", "请求 / 调用"), ("teal", "确认中的动作"), ("slate", "传输"),
                        ("clay", "引擎原生层"), ("green", "回读证据")], gap=26)
    s.note(110, 892, "实线＝控制流与提交；虚线＝只读回读（模型只根据回读事实更新判断）", 10, FAINT)
    return s.render(908)


# ------------------------------------------------------------------ 图 2 运行时分层
def layers() -> str:
    s = Svg(1120)
    rows = [
        ("调用面", "blue",
         [("civ6 CLI", "唯一操作面 · devtools/"), ("debug 会话", "同进程 · stdin→JSONL"),
          ("命名子命令", "33 组 / 142 个")],
         "AI 只经由 CLI；CLI 自行启动内部 stdio MCP，客户端不注册"),
        ("工具接口", "blue",
         [("server.py · 2,336 行", "90 个 @mcp.tool + 3 热座 · 参数校验 · 工具锁"),
          ("AppContext", "持有 GameConnection 与 GameState"),
          ("narrate.py · 2,034 行", "领域事实 → 工具返回文本")],
         "许可分类留在调度端，只消费公共未决事实"),
        ("状态与编排", "teal",
         [("GameState · 2,573 行", "领域 interface · 查询 · 观察缓存 · 存档替换"),
          ("snapshot.py", "只读验收快照 ready / blocked / unknown"),
          ("knowledge.py", "知识查询与静态科研目录")],
         "所有领域准备、阻塞判断与身份刷新都从这一个所有者出发"),
        ("确认所有者", "amber",
         [("actions.py", "ActionLifecycle：_PendingAction / _ActionScope"),
          ("end_turn.py", "TurnProgress / TurnStatus / TurnContext"),
          ("diplomacy.py", "DiplomacyProgress / TradeAttempt / IncomingAttempt"),
          ("congress.py", "CongressProgress · 原生票单与阶段门禁")],
         "「单次提交 → 有界只读确认」的唯一实现位置；未决即门禁"),
        ("Lua 构造与解析", "purple",
         [("lua/ · 48 个模块 · 18,694 行", "按游戏领域构造 Lua 语句并解析返回值"),
          ("_helpers.py", "序列化、容错与上下文选择"),
          ("领域分布", "units · cities · trade · congress · map")],
         "纯查询不产生游戏操作；查询与动作共用同一解释"),
        ("传输", "slate",
         [("connection.py", "GameConnection：请求关联、上下文发现、错误分类"),
          ("tuner_client.py", "FireTuner 帧、超时与断线")],
         "单次执行、完整响应、超时隔离、无重放"),
        ("游戏引擎", "clay",
         [("GameCore", "玩家 / 城市 / 单位 / 科研 / 议会的真实状态"),
          ("InGame / FrontEnd", "界面上下文与主菜单读档")],
         "规则裁决与真实状态都在引擎；运行层只读取与提交"),
    ]

    TOTAL_W, GAP = 598, 8

    def plan(chip_list):
        n = len(chip_list)
        cols = 2 if n >= 4 else n
        grid_rows = (n + cols - 1) // cols
        cw = (TOTAL_W - GAP * (cols - 1)) / cols
        metrics = []
        for title, sub in chip_list:
            tr = wrap(title, 10.6, cw - 22, 600)
            sr = wrap(sub, 9.5, cw - 22)
            metrics.append((tr, sr))
        ch = max(18 + len(tr) * 13.2 + len(sr) * 12.2 for tr, sr in metrics)
        return cols, cw, ch, grid_rows, metrics

    def draw_chips(chip_list, x, y, color):
        cols, cw, ch, grid_rows, metrics = plan(chip_list)
        p = PALETTE[color]
        for i, (tr, sr) in enumerate(metrics):
            r, c = divmod(i, cols)
            bx = x + c * (cw + GAP)
            by = y + r * (ch + GAP)
            s.rect(bx, by, cw, ch + 4, "#ffffff", p["edge"], 9, 1.1)
            s.lines(bx + 10, by + 17, tr, 10.6, p["deep"], "start", lh=13.2, weight=600)
            s.lines(bx + 10, by + 19 + len(tr) * 13.2, sr, 9.5, MUTED, "start", lh=12.2)

    y = 34
    for i, (name, color, chip_list, note) in enumerate(rows):
        cols, cw, ch, grid_rows, _ = plan(chip_list)
        h = 38 + grid_rows * ch + (grid_rows - 1) * GAP + 14
        p = PALETTE[color]
        s.add('<g class="n">')
        s.rect(66, y, 634, h, p["soft"], p["edge"], 13, 1.4)
        s.rect(66, y, 4.5, h, p["c"], None, 2)
        s.text(88, y + 24, f"L{i}", 10.5, p["c"], "start", 600, family="var(--mono)")
        s.text(112, y + 24, name, 15, p["deep"], "start", 600)
        draw_chips(chip_list, 84, y + 38, color)
        s.text(716, y + 24, note, 10.2, MUTED, "start")
        s.add("</g>")
        if i < len(rows) - 1:
            s.arrow([(360, y + h), (360, y + h + 20)], color, sw=1.7)
            s.arrow_label(372, y + h + 16,
                          ["工具调用", "async 领域调用", "领域准备", "Lua 构造 / 解析", "请求 / 响应", "原生 API"][i],
                          color, size=10, anchor="start")
        y += h + 20

    # 右侧说明
    s.panel(716, 96, 380, 224, "分层的三个硬约束", "slate", fill="#ffffff")
    items = [
        ("所有权唯一", "每个未决请求只有一个所有者；兼容私有属性只代理同一份状态，不保留副本。"),
        ("门禁共享", "保存/加载、外交、推进、议会与普通动作互相阻塞；纯查询与已确认检查点恢复保留通路。"),
        ("失败不追认", "丢回应、超时、身份未知一律保留未决；读取旧状态不触发自动重发。"),
    ]
    yy = 134
    for t, d in items:
        s.text(736, yy, "· " + t, 12, INK, "start", 600)
        text_rows = wrap(d, 10.8, 330)
        s.lines(736, yy + 18, text_rows, 10.8, MUTED, "start", lh=15)
        yy += 28 + len(text_rows) * 15

    s.panel(716, 360, 380, 160, "代码规模（行）", "slate", fill="#ffffff")
    stats = [("src/civ_mcp/*.py", "12,284"), ("src/civ_mcp/lua/*.py", "18,694"),
             ("src/civ_mcp/devtools/", "1,768"), ("tests/", "39,741")]
    for i, (k, v) in enumerate(stats):
        x = 736 + (i % 2) * 178
        yy = 394 + (i // 2) * 58
        s.text(x, yy, k, 10.2, MUTED, "start", 500, family="var(--mono)")
        s.text(x, yy + 24, v, 20, INK, "start", 600, family="var(--mono)")

    s.panel(716, 560, 380, 180, "每一层怎么被验证", "slate", fill="#ffffff")
    checks = [("L1–L3", "离线 pytest · FakeConn / 内存 Tuner 流"),
              ("L1", "civ6 --help --json · 契约与 dry-run"),
              ("L2", "civ6 verify state：只读快照验收"),
              ("L5–L6", "实机验收：固定基准 + 显式检查点")]
    for i, (k, v) in enumerate(checks):
        yy = 594 + i * 34
        s.text(736, yy, k, 10.2, PALETTE["blue"]["c"], "start", 600, family="var(--mono)")
        s.text(776, yy, v, 10.6, MUTED, "start")

    s.legend(716, 786, [("blue", "接口"), ("teal", "状态"), ("amber", "未决 / 门禁"), ("purple", "Lua"),
                        ("slate", "传输"), ("clay", "引擎")], gap=16)
    s.note(716, 812, "右侧注明每层承担的职责；箭头是调用的主路径。", 10, FAINT)
    return s.render(max(y + 10, 840))


# ------------------------------------------------------------------ 图 3 模块依赖
def modules() -> str:
    s = Svg(1180)

    def mod(x, y, w, h, name, loc, subs, color="slate", fill=None, size=13.2):
        p = PALETTE[color]
        s.add('<g class="n">')
        s.rect(x, y, w, h, fill or p["soft"], p["edge"], 12, 1.35)
        s.rect(x, y, 4, h, p["c"], None, 2)
        s.text(x + 14, y + 21, name, size, p["deep"], "start", 600)
        if loc:
            s.text(x + w - 10, y + 20, loc, 9.6, p["c"], "end", 500, family="var(--mono)")
        rows = []
        for sub in subs:
            rows += wrap(sub, 9.7, w - 26)
        s.lines(x + 14, y + 39, rows, 9.7, MUTED, "start", lh=13)
        s.add("</g>")

    # ---- 工具面 / 入口
    mod(40, 30, 180, 96, "cli.py", "245 行",
        ["5 个顶层命令", "单 JSON 信封 + 退出码 0/1/2/130"], "blue")
    mod(240, 30, 270, 96, "devtools/", "1,768 行",
        ["debug · commands · evidence · live", "knowledge · logs · testing · turn_timing"], "blue")
    mod(530, 30, 300, 96, "scripts/", "独立入口",
        ["civ6_cli_relay · hotseat_* · integration_turns", "经 CLI 或内部控制器驱动，不进运行时 import 图"], "slate")
    mod(860, 30, 280, 96, "客户端接入", "CLI 直跑",
        ["Codex / Claude / Pi 只执行终端命令", "不注册 civ6 MCP；.mcp.json 为空"], "blue")

    # ---- 接口与只读协作者
    mod(40, 170, 180, 130, "narrate.py", "2,034 行",
        ["把领域事实叙述成工具文本", "被 game_state / diplomacy /", "end_turn 调用"], "slate")
    mod(250, 170, 300, 130, "server.py", "2,336 行",
        ["90 个 @mcp.tool + 3 热座工具", "整体工具锁 · 未决闸门 · 日志", "lifespan 构造唯一 GameState"], "blue")

    s.panel(580, 170, 490, 130, "server 直接 import 的只读协作者", "slate", fill="#ffffff")
    for i, (nm, loc, d) in enumerate([("knowledge.py", "139", "知识搜索 / 详情 / 研究目录"),
                                     ("snapshot.py", "58", "只读验收快照"),
                                     ("logger.py", "23", "本地动作日志")]):
        bx = 596 + i * 156
        s.rect(bx, 200, 146, 66, PALETTE["teal"]["soft"], PALETTE["teal"]["edge"], 9, 1.1)
        s.text(bx + 10, 220, nm, 10.8, PALETTE["teal"]["deep"], "start", 600)
        s.text(bx + 136, 219, loc + " 行", 9.2, PALETTE["teal"]["c"], "end", 500, family="var(--mono)")
        s.lines(bx + 10, 238, wrap(d, 9.4, 128), 9.4, MUTED, "start", lh=12.4)
    s.text(596, 288, "knowledge.py 懒加载 research_catalog（129 行）做静态研究目录查询", 9.4, FAINT, "start")

    # ---- 状态与生命周期
    mod(250, 336, 300, 144, "game_state.py", "2,573 行",
        ["领域 interface · 查询 · 观察缓存", "完整存档替换 · 阻塞判断 · 身份刷新",
         "持有 actions / end_turn 并懒加载 diplomacy · congress"], "teal")
    s.panel(580, 336, 490, 144, "会话与生命周期（game_state 懒加载）", "slate", fill="#ffffff")
    life = [("checkpoints.py", "203 行", "检查点 · 基准与测试轮次"), ("game_lifecycle.py", "1,017 行", "原生菜单 · 加载 · 文件事实"),
            ("game_launcher.py", "593 行", "进程启动 / 重启 / kill"), ("autosave · platform_paths", "65 · 50 行", "自动存档 · 平台路径")]
    for i, (nm, loc, d) in enumerate(life):
        bx = 596 + (i % 2) * 236
        by = 368 + (i // 2) * 52
        s.rect(bx, by, 226, 44, PALETTE["amber"]["soft"], PALETTE["amber"]["edge"], 9, 1.1)
        s.text(bx + 10, by + 17, nm, 10.6, PALETTE["amber"]["deep"], "start", 600)
        s.text(bx + 216, by + 16, loc, 9.0, PALETTE["amber"]["c"], "end", 500, family="var(--mono)")
        s.text(bx + 10, by + 33, d, 9.2, MUTED, "start")

    # ---- 确认所有者
    mod(40, 520, 166, 94, "actions.py", "795 行",
        ["ActionLifecycle", "_PendingAction / _ActionScope"], "amber")
    mod(216, 520, 166, 94, "end_turn.py", "717 行",
        ["TurnProgress · TurnStatus", "起始回合与差分基线"], "amber")
    mod(392, 520, 166, 94, "diplomacy.py", "626 行",
        ["DiplomacyProgress", "TradeAttempt / IncomingAttempt"], "amber")
    mod(568, 520, 150, 94, "congress.py", "162 行",
        ["CongressProgress", "原生票单与阶段门禁"], "amber")
    s.rect(740, 524, 400, 86, "#fbfcfe", LINE, 11, 1.1, dash="5 5")
    s.text(756, 546, "普通动作共用一个 ActionLifecycle；", 11.2, INK, "start", 600)
    s.text(756, 563, "外交 / 议会 / 推进各有专用 Progress 所有者", 9.8, MUTED, "start")
    s.lines(756, 585, ["未决即阻塞保存/加载、外交、推进、议会与普通动作；",
                       "纯查询与已确认检查点恢复保留通路。"], 9.5, MUTED, "start", lh=13)

    # ---- Lua
    s.add('<g class="n">')
    p = PALETTE["purple"]
    s.rect(40, 640, 700, 100, p["soft"], p["edge"], 12, 1.35)
    s.rect(40, 640, 4, 100, p["c"], None, 2)
    s.text(54, 662, "lua/", 13.5, p["deep"], "start", 600)
    s.text(736, 661, "48 个模块 · 18,694 行", 9.8, p["c"], "end", 500, family="var(--mono)")
    for i, d in enumerate(["units · cities · map（最大三块）", "trade · diplomacy · congress",
                           "religion · espionage · governance", "models · _helpers · public_reports"]):
        bx = 54 + (i % 2) * 340
        by = 674 + (i // 2) * 26
        s.text(bx, by, "· " + d, 9.8, MUTED, "start")
    s.add("</g>")
    s.text(760, 660, "构造 Lua 语句并解析返回值", 10, MUTED, "start")
    s.text(760, 678, "纯查询不产生游戏操作", 10, MUTED, "start")
    s.text(760, 696, "按领域一一对应原生 API 可用性", 10, MUTED, "start")

    # ---- 传输与引擎
    mod(40, 770, 200, 80, "connection.py", "246 行",
        ["GameConnection：请求关联", "上下文发现 · 错误分类"], "slate")
    mod(270, 770, 190, 80, "tuner_client.py", "208 行",
        ["FireTuner 帧、超时、断线", "无自动重放"], "slate")
    mod(500, 770, 340, 80, "Civilization VI", "TCP 4318",
        ["GameCore / InGame / FrontEnd", "规则与真实状态由引擎裁决"], "clay")

    # ---- 边
    s.arrow([(220, 78), (238, 78)], "blue")
    s.arrow([(375, 126), (375, 168)], "blue")
    s.arrow_label(387, 150, "启动并复用同一内部 MCP 进程", "blue", size=9.5, anchor="start")
    s.arrow([(680, 126), (680, 158), (556, 158), (556, 170)], "slate", dash="6 5", sw=1.5)
    s.text(690, 152, "经 CLI 或内部控制器驱动，不进入运行时 import 图", 9.2, PALETTE["slate"]["deep"], "start", 600)

    s.arrow([(400, 300), (400, 334)], "teal")
    s.arrow_label(412, 322, "请求进入单一所有者", "teal", size=9.5, anchor="start")
    s.arrow([(550, 220), (578, 220)], "teal", dash="6 5", sw=1.4)
    s.arrow([(550, 380), (578, 380)], "teal", dash="6 5", sw=1.4)

    # game_state → 领域 bus
    s.arrow([(400, 480), (400, 506)], "teal", head=False, sw=1.8)
    s.path("M 123 506 L 643 506", stroke=PALETTE["teal"]["c"], sw=1.8)
    for x in (123, 299, 475, 643):
        s.arrow([(x, 506), (x, 518)], "teal", sw=1.8)

    # 领域 → lua bus
    for x in (123, 299, 475, 643):
        s.arrow([(x, 614), (x, 630)], "purple", head=False, sw=1.6)
    s.path("M 123 630 L 400 630", stroke=PALETTE["purple"]["c"], sw=1.6)
    s.arrow([(400, 630), (400, 638)], "purple", sw=1.6)

    s.arrow([(140, 740), (140, 768)], "slate")
    s.arrow_label(152, 760, "Lua 请求帧", "slate", size=9.5, anchor="start")
    s.arrow([(240, 810), (268, 810)], "slate")
    s.arrow([(460, 810), (498, 810)], "slate")

    s.legend(40, 878, [("blue", "接口与入口"), ("teal", "状态与分派"), ("amber", "确认所有者"),
                       ("purple", "Lua 构造"), ("slate", "传输 / 工具"), ("clay", "引擎")], gap=20)
    s.note(40, 904, "实线＝import 与调用；虚线＝跨进程或仅文本协作。LOC 为当前工作树行数（含未提交改动）。", 9.8, FAINT)
    return s.render(918)
