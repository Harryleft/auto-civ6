"""图 4–6：动作生命周期、回合推进、端到端时序。"""

from __future__ import annotations

from vizkit import PALETTE, INK, MUTED, FAINT, LINE, BG, BG_SOFT, Svg, tw, wrap


# ------------------------------------------------------------------ 图 4 动作生命周期
def action_lifecycle() -> str:
    s = Svg(1120)

    def state(x, y, w, h, tag, name, subs, color, size=13.4, note=None):
        p = PALETTE[color]
        s.add('<g class="n">')
        s.rect(x, y, w, h, p["soft"], p["edge"], 12, 1.5)
        s.rect(x + 12, y, w - 24, 3.2, p["c"], None, 2, 1)
        s.text(x + w / 2, y + 24, tag, 9.8, p["c"], weight=700, family="var(--mono)")
        s.text(x + w / 2, y + 42, name, size, p["deep"], weight=600)
        rows = []
        for sub in subs:
            rows += wrap(sub, 9.7, w - 24)
        s.lines(x + w / 2, y + 60, rows, 9.7, MUTED, "middle", lh=12.6)
        if note:
            s.text(x + w / 2, y + h - 6, note, 9.2, FAINT, "middle")
        s.add("</g>")

    state(40, 60, 176, 92, "S0", "无未决",
          ["ActionLifecycle.pending = None"], "slate")
    state(252, 60, 186, 92, "S1", "scope 已准备",
          ["civ / seed / turn / owner", "四字段绑定原世界"], "blue")
    state(474, 56, 220, 104, "S2", "已提交 · 待确认",
          ["pending 先赋值，再执行唯一一次提交", "只读 evidence 循环确认"], "amber")
    state(730, 60, 168, 92, "S3", "已确认 / 已退役",
          ["证据命中即清理 pending"], "teal")

    state(252, 216, 186, 88, "S4", "精确 pre-entry 拒绝",
          ["首行显式拒绝，未进入写入", "唯一允许的提交前退役"], "green")
    state(474, 216, 220, 96, "S5", "结果未知 · 保留",
          ["丢回应 / 超时 / 读失败", "读旧状态不追认、不重发"], "amber")
    state(730, 210, 210, 100, "S6", "scope 失效 · 锁存",
          ["任一字段有效不匹配即置位", "返回原值也不能追认"], "clay")

    s.arrow([(216, 106), (250, 106)], "blue")
    s.arrow([(438, 106), (472, 106)], "amber")
    s.arrow([(694, 106), (728, 106)], "teal")
    for cx, no, key in ((233, "1", "blue"), (455, "2", "amber"), (711, "3", "teal")):
        s.circle(cx, 106, 9.5, "#ffffff", PALETTE[key]["c"], 1.4)
        s.text(cx, 110, no, 9.6, PALETTE[key]["c"], weight=700)

    s.arrow([(330, 152), (330, 214)], "green")
    s.arrow_label(338, 188, "首行拒绝", "green", size=9.2, anchor="start")
    s.arrow([(584, 160), (584, 214)], "amber")
    s.arrow_label(592, 190, "应答未知", "amber", size=9.2, anchor="start")
    s.arrow([(694, 152), (720, 152), (720, 208)], "clay")
    s.arrow_label(726, 182, "scope 变化", "clay", size=9.2, anchor="start")

    # 自环：同意图重入只确认
    s.arrow([(694, 80), (712, 80), (712, 40), (584, 40), (584, 54)], "teal", dash="5 4", sw=1.5)
    s.text(648, 32, "同意图重入 → 只确认旧请求，永不双提交", 9.4, PALETTE["teal"]["deep"], weight=600)

    # S6 唯一退役
    s.arrow([(940, 260), (972, 260), (972, 292)], "clay", dash="5 5", sw=1.4)
    s.text(982, 256, "只有显式会话替换", 9.4, PALETTE["clay"]["deep"], "start", 600)
    s.text(982, 272, "reset_session 能退役", 9.4, PALETTE["clay"]["deep"], "start")
    s.text(982, 288, "（并保留替换门禁）", 9.4, MUTED, "start")

    # 锁存规则
    s.panel(40, 350, 560, 168, "scope 失效锁存：未知不遮蔽，原值不追认", "slate", fill="#ffffff")
    for i, (t, d) in enumerate([("civilization", "CIVILIZATION_[A-Z0-9_]+"),
                                ("seed", "原生世界种子"),
                                ("turn", "回合 >= 0"),
                                ("owner", "本地玩家 0..63")]):
        bx = 56 + i * 134
        s.rect(bx, 386, 124, 40, PALETTE["slate"]["soft"], PALETTE["slate"]["edge"], 8, 1.1)
        s.text(bx + 62, 402, t, 9.8, PALETTE["slate"]["deep"], weight=600, family="var(--mono)")
        s.text(bx + 62, 416, d, 8.8, MUTED)
    s.lines(56, 450, ["· 字段为 None 时跳过：未知字段不能遮蔽其他字段的真实变化。",
                      "· 校验失败只记 known=False，不置失效；有效且不等才锁存。",
                      "· 锁存后没有任何代码路径清除；普通身份刷新只记录观察结果。",
                      "· 移动另有原生事件 MOVE_SCOPE_INVALID 可直接置位。"], 9.8, MUTED, "start", lh=15)

    # 不重发五处
    s.panel(630, 350, 450, 168, "「不重发」由五处共同保证", "amber", fill="#ffffff")
    items = [
        ("① submit", "pending 先赋值，执行异常也保留所有者"),
        ("② confirm", "只执行 pending.query 的只读读取"),
        ("③ 并发再入", "已有 pending 时只确认或返回 unknown"),
        ("④ connection", "丢回应不自动重放，异常即刻断连"),
        ("⑤ MCP", "捕获错误只返回文本，不重启不重试"),
    ]
    for i, (a, b) in enumerate(items):
        yy = 386 + i * 26
        s.text(646, yy, a, 9.6, PALETTE["amber"]["deep"], "start", 600, family="var(--mono)")
        s.text(712, yy, b, 9.6, MUTED, "start")

    s.legend(40, 552, [("blue", "提交前"), ("amber", "已提交 / 未决"), ("teal", "已确认"), ("green", "明确拒绝"),
                       ("clay", "锁存失效")], gap=18)
    s.note(40, 566, "① prepare_scope：要求恰好 1 行 5 字段的 ACTION_SCOPE；② submit()：唯一一次提交，pending 先赋值；"
                    "③ 只读 evidence 命中即退役。", 9.6, FAINT)
    s.note(40, 586, "每个动作保留自身原生证据：单位移动的 MOVE_WITNESS、攻击的 pre_hp/fingerprint、建城的 position + 查询串；"
                    "确认只用有界只读查询。", 9.6, FAINT)
    return s.render(606)


# ------------------------------------------------------------------ 图 5 回合推进
def turn_loop() -> str:
    s = Svg(1120)

    def st(x, y, w, h, name, subs, color, tag=None):
        p = PALETTE[color]
        s.add('<g class="n">')
        s.rect(x, y, w, h, p["soft"], p["edge"], 11, 1.35)
        s.rect(x + 10, y, w - 20, 3, p["c"], None, 2, 1)
        s.text(x + w / 2, y + 22, name, 12.2, p["deep"], weight=600)
        rows = []
        for sub in subs:
            rows += wrap(sub, 9.2, w - 20)
        s.lines(x + w / 2, y + 38, rows, 9.2, MUTED, "middle", lh=11.8)
        if tag:
            s.text(x + w / 2, y + h - 7, tag, 8.8, FAINT, "middle")
        s.add("</g>")

    # --- TurnStatus.phase 观察面
    s.text(40, 44, "观测面：TurnStatus.phase（get_turn_status 与 end_turn 共用同一套原生阶段解释）", 12.4, INK, "start", 600)
    st(40, 62, 178, 76, "WAITING_FOR_PLAYERS", ["本地回合不活动：别人在行动"], "slate", "不是死锁")
    st(238, 62, 178, 76, "LOCAL_TURN_STARTING", ["活动但 complete=False"], "slate", "回合开始处理")
    st(436, 62, 178, 76, "UI_PROCESSING", ["活动 + complete + 仍在处理 UI 消息"], "slate", "消息未处理完")
    st(634, 62, 196, 76, "LOCAL_ACTION_REQUIRED", ["就绪原因是阻塞 / 队列移动 / 单位 / 城市射击"], "amber", "需模型显式决定")
    st(850, 62, 170, 76, "LOCAL_READY", ["active + complete + ui=False", "readiness=NONE"], "teal", "可提交")
    st(40, 168, 178, 62, "UNKNOWN", ["其他组合 / 读取失败"], "clay", "保留诊断")
    s.arrow([(129, 138), (129, 166)], "clay", sw=1.5)
    s.note(230, 196, "任一字段缺失或读失败 → UNKNOWN；报告不承担完成判断", 9.4, FAINT)
    s.arrow([(218, 100), (236, 100)], "slate")
    s.arrow([(416, 100), (434, 100)], "slate")
    s.arrow([(614, 100), (632, 100)], "amber")
    s.arrow([(830, 100), (848, 100)], "teal")
    s.arrow([(732, 138), (732, 160), (660, 160), (660, 140)], "amber", dash="4 4", sw=1.3)
    s.note(748, 158, "处理完强制选择后回到就绪判断", 9, FAINT)

    # --- TurnProgress 状态机
    s.text(40, 268, "请求面：TurnProgress（唯一持有未决推进、原世界身份、起始回合与差分基线）", 12.4, INK, "start", 600)
    st(40, 286, 190, 96, "T0 idle", ["pending=False", "world / from_turn / baseline = None"], "slate")
    st(300, 282, 250, 104, "T1 已提交 · 轮询确认",
       ["提交前：终局探针 → 读阶段 → 查阻塞 → 快照",
        "提交后：≤180s，每 2s 一次只读阶段读取"], "amber")
    st(622, 286, 190, 96, "T2 已确认", ["turn_after > from_turn", "且 ready 且 world 匹配", "先 reset() 再报告"], "teal")
    st(300, 430, 250, 88, "REOPENED_LOCAL", ["同回合 + 需本地动作 + 收到原生重开证据", "以退役本次尝试（不重发）"], "green")
    st(590, 430, 220, 88, "暂停 · 保留请求", ["外交会话 / 议会阶段 / 信息弹窗", "返回处理指引，请求继续挂起"], "amber")
    st(846, 430, 174, 88, "UNKNOWN · 保留", ["超时 ≠ 读失败", "world 变化 → 锁存失效"], "clay")

    s.arrow([(230, 334), (298, 334)], "amber")
    s.arrow_label(264, 324, "提交 ×1", "amber", size=9)
    s.arrow([(550, 334), (620, 334)], "teal")
    s.arrow_label(585, 324, "三条件", "teal", size=9)
    s.arrow([(425, 386), (425, 428)], "green")
    s.note(434, 406, "本地被重开", 9, FAINT)
    s.arrow([(550, 362), (610, 362), (610, 428)], "amber")
    s.arrow([(720, 382), (900, 382), (900, 428)], "clay", dash="5 4", sw=1.3)
    s.note(760, 374, "读失败 / 超时 / 身份未知", 9, FAINT)

    # --- 阻塞出口
    s.panel(40, 540, 1040, 178, "推进前必须由模型显式解决的阻塞（工具不自动选择）", "slate", fill="#ffffff")
    blocks = [("外交 / 交易", "get_pending_diplomacy → respond_to_diplomacy", "amber"),
              ("世界议会", "get_world_congress → open_world_congress / queue_wc_votes", "purple"),
              ("原生阻塞通知", "set_city_production · set_research · set_policies · unit_action", "blue"),
              ("强制选择", "choose_dedication · appoint_governor · set_policies", "blue"),
              ("单位 / 城市就绪", "显式 unit_action / city_action（不自动选命令）", "teal"),
              ("信息弹窗", "dismiss_popup", "slate"),
              ("原生终局", "GAME OVER：reset() 并返回", "clay")]
    for i, (t, d, c) in enumerate(blocks):
        bx = 56 + (i % 3) * 340
        by = 584 + (i // 3) * 42
        s.pill(bx, by - 12, t, c, size=9.6)
        s.text(bx, by + 12, d, 9.2, MUTED, "start")

    s.note(40, 748, "确认三条件：回合增长 + ready（active 且 complete 且 ui=False）+ 原世界身份匹配；缺一即保留未决。", 9.8, FAINT)
    return s.render(766)


# ------------------------------------------------------------------ 图 6 端到端时序
def sequence() -> str:
    s = Svg(1120)
    lanes = [
        ("Agent + civ6 CLI", 100, "blue", "执行命名命令"),
        ("CLI 会话 / server.py", 288, "blue", "内部 stdio · 工具面 · 锁"),
        ("GameState + owner", 500, "teal", "ActionLifecycle"),
        ("lua/", 704, "purple", "构造 / 解析"),
        ("connection · FireTuner", 900, "slate", "TCP 4318"),
        ("Civilization VI", 1058, "clay", "引擎裁决"),
    ]
    top, bottom = 44, 700

    # 阶段底色先画，避免遮住生命线
    s.rect(24, 122, 1072, 210, "#fdf7ea", None, 14, 0)
    s.rect(24, 350, 1072, 262, "#eef6f2", None, 14, 0)
    for i, ch in enumerate("①单次提交"):
        s.text(34, 176 + i * 13, ch, 9.4, PALETTE["amber"]["deep"], weight=600)
    for i, ch in enumerate("②有界只读确认"):
        s.text(34, 404 + i * 13, ch, 9.4, PALETTE["teal"]["deep"], weight=600)

    for name, x, color, sub in lanes:
        p = PALETTE[color]
        w = 156
        s.add('<g class="n">')
        s.rect(x - w / 2, top, w, 56, p["soft"], p["edge"], 10, 1.3)
        s.text(x, top + 24, name, 11.4, p["deep"], weight=600)
        s.text(x, top + 42, sub, 9.2, MUTED)
        s.add("</g>")
        s.path(f"M {x} {top + 58} L {x} {bottom}", stroke=LINE, sw=1.2, dash="4 5")

    def msg(y, a, b, text, color="slate", dash=None):
        x1, x2 = lanes[a][1], lanes[b][1]
        s.arrow([(x1, y), (x2, y)], color, sw=1.7, dash=dash)
        s.text((x1 + x2) / 2, y - 7, text, 9.8,
               PALETTE[color]["deep"] if color in PALETTE else MUTED, weight=600)

    def self_msg(y, lane, text, color="teal", w=190):
        x = lanes[lane][1]
        s.arrow([(x, y), (x + w, y), (x + w, y + 24), (x, y + 24)], color, sw=1.5)
        s.text(x + 8, y + 40, text, 9.4, PALETTE[color]["deep"], "start", 500)

    msg(142, 0, 1, "unit move / city attack / turn end（stdin 命令）", "blue")
    msg(174, 1, 2, "内部 MCP：工具锁 → 未决门禁 → 分派领域准备", "blue")
    self_msg(196, 2, "prepare_scope：civ / seed / turn / owner 绑定原世界", "teal", 168)
    msg(268, 2, 3, "构造提交语句 + witness token", "purple")
    msg(300, 3, 4, "CMD 帧（uuid 前缀包络）", "slate")
    msg(332, 4, 5, "原生 RequestAction / 查询", "clay")

    msg(384, 5, 2, "唯一回执：OK / MOVING_TO|… / ERR:", "clay")
    self_msg(406, 2, "pending 先赋值；解析证据 → 已接受 / 拒绝 / 未知", "amber", 200)

    msg(474, 2, 3, "只读确认查询（0.15s × N，≤5s）", "teal")
    msg(506, 3, 4, "ACTION_IDENTITY + 位置 / 见证查询", "slate")
    msg(538, 4, 5, "只读 read", "clay")
    msg(570, 5, 2, "POS / MOVE_WITNESS / ACTION_IDENTITY 行", "clay")
    self_msg(592, 2, "命中证据 → pending=None；未命中 → 保留 unknown", "teal", 208)

    s.arrow([(500, 654), (288, 654)], "teal", sw=1.7)
    s.text(394, 647, "工具文本：事实 + 回读结果 + 下一步指引", 9.8, PALETTE["teal"]["deep"], weight=600)
    s.arrow([(288, 682), (100, 682)], "blue", sw=1.7)
    s.text(194, 675, "模型据此更新判断，进入下一步", 9.8, PALETTE["blue"]["deep"], weight=600)

    # 未知分支
    s.rect(700, 762, 386, 86, PALETTE["amber"]["soft"], PALETTE["amber"]["edge"], 11, 1.3, dash="5 4")
    s.text(716, 786, "结果未知时：保留原请求，不重发", 11.4, PALETTE["amber"]["deep"], "start", 600)
    s.lines(716, 804, ["· 丢回应 / 超时 / 读失败 → unknown，所有者在位；",
                       "· 后续同意图调用只确认旧请求，不提交第二次；",
                       "· 只有确认、精确 pre-entry 拒绝或显式会话替换能清除。"], 9.4, MUTED, "start", lh=13)
    s.arrow([(708, 604), (758, 604), (758, 748), (758, 760)], "amber", dash="5 4", sw=1.4)

    s.note(24, 890, "本图以命名命令 unit move 为例；命令经 CLI 的内部 stdio MCP 进入 Runtime。turn end 的确认循环更长（≤180s / 2s 间隔），但同样是「一次提交 + 只读回读」。", 9.6, FAINT)
    return s.render(908)
