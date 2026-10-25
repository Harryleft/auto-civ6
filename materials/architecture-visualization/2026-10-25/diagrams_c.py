"""图 7–8：会话替换 / 检查点，外交交易 / 世界议会状态机。"""

from __future__ import annotations

from vizkit import PALETTE, INK, MUTED, FAINT, LINE, BG, BG_SOFT, Svg, tw, wrap


# ------------------------------------------------------------------ 图 7 会话替换与检查点
def session() -> str:
    s = Svg(1120)

    def box(x, y, w, h, name, subs, color, size=12.6, tag=None):
        p = PALETTE[color]
        s.add('<g class="n">')
        s.rect(x, y, w, h, p["soft"], p["edge"], 11, 1.4)
        s.rect(x + 10, y, w - 20, 3, p["c"], None, 2, 1)
        s.text(x + 16, y + 24, name, size, p["deep"], "start", 600)
        if tag:
            s.text(x + w - 12, y + 23, tag, 9, p["c"], "end", 500, family="var(--mono)")
        rows = []
        for sub in subs:
            rows += wrap(sub, 9.4, w - 30)
        s.lines(x + 16, y + 41, rows, 9.4, MUTED, "start", lh=12.4)
        s.add("</g>")

    # 三个显式入口
    s.text(40, 44, "三个显式入口（普通查询/缓存刷新不会触发任何一条）", 12.2, INK, "start", 600)
    box(40, 58, 226, 66, "load_game_save", ["要求已在原生主菜单"], "blue")
    box(284, 58, 238, 66, "reload_game_save", ["菜单路径换档，进程保留"], "blue")
    box(540, 58, 282, 66, "restart_and_load", ["冷恢复：断连 → stop_game → start_game"], "blue")
    box(840, 58, 240, 66, "两条门禁", ["replacement_pending / save_pending"], "amber")

    # 汇聚
    s.arrow([(153, 124), (153, 138), (700, 138)], "blue", head=False, sw=1.6)
    s.arrow([(403, 124), (403, 138)], "blue", head=False, sw=1.6)
    s.arrow([(681, 124), (681, 138)], "blue", head=False, sw=1.6)
    s.arrow([(700, 138), (700, 164)], "blue", sw=1.6)
    s.text(712, 154, "共用同一套加载确认", 9.4, PALETTE["blue"]["deep"], "start")

    # 状态机
    box(40, 166, 196, 88, "Idle", ["无替换、无保存未决", "普通查询与缓存刷新在此"], "slate")
    box(292, 166, 220, 88, "门禁置位", ["replacement_pending = True", "保存时另置 save_pending"], "amber")
    box(566, 160, 258, 100, "单一所有者：GameState", ["提交 + 确认循环（最多一次提交）",
                                                        "重连 → 读身份 / 回合 / overview",
                                                        "终读一致 + 检查点复核"], "teal")
    box(878, 160, 202, 82, "Confirmed", ["释放门禁", "原始基准名 → 新建测试轮次"], "teal")
    box(878, 286, 202, 88, "Unknown", ["已进入原生调用，副作用不可知", "保持门禁，提示显式恢复"], "clay")
    box(292, 286, 220, 100, "NotSubmitted", ["只有调用前可证明未调用才允许",
                                             "释放门禁，回到 Idle"], "green")

    s.arrow([(236, 210), (290, 210)], "amber")
    s.arrow([(512, 210), (564, 210)], "teal")
    s.arrow([(824, 190), (876, 190)], "teal")
    s.arrow([(824, 232), (846, 232), (846, 286)], "clay")
    s.arrow([(690, 260), (690, 284)], "clay", dash="5 4", sw=1.4)
    s.text(700, 278, "pcall 抛错 / 断连 / 无确认行", 9.2, MUTED, "start")
    s.arrow([(566, 300), (514, 300)], "green")
    s.text(540, 320, "not_submitted", 9.2, PALETTE["green"]["deep"], weight=600)
    s.arrow([(402, 386), (402, 396), (238, 396), (238, 258)], "green", dash="5 4", sw=1.4)
    s.text(252, 384, "释放门禁 → 回到 Idle，不重放已发出的块", 9.2, PALETTE["green"]["deep"], "start")

    # 判定规则
    s.panel(40, 402, 520, 178, "三态判定铁律", "slate", fill="#ffffff")
    rules = [("confirmed", "证据链完整：身份 + 回合 + 就绪 → 终读一致 → 检查点复核", "teal"),
             ("unknown", "调用已进入：异常 / 取消 / 断线 / 终读不一致 → 永远保持门禁", "clay"),
             ("not_submitted", "调用前即可证明未调用：参数、能力、上下文、明确 false", "green")]
    yy = 438
    for name, d, c in rules:
        s.pill(54, yy - 12, name, c, size=9.6)
        s.lines(54, yy + 20, wrap(d, 9.6, 486), 9.6, MUTED, "start", lh=12.6)
        yy += 54

    # 检查点模型
    s.panel(580, 402, 500, 178, "检查点与测试轮次（checkpoints.py）", "slate", fill="#ffffff")
    fields = [("save_name", "精确名称"), ("sha256", "读取前后 stat 一致"), ("civilization", "原生 ID"),
              ("seed", "世界种子"), ("turn", "登记回合"), ("run_id", "本轮标识")]
    for i, (k, v) in enumerate(fields):
        bx = 596 + (i % 3) * 162
        by = 438 + (i // 3) * 46
        s.rect(bx, by - 14, 152, 38, PALETTE["slate"]["soft"], PALETTE["slate"]["edge"], 8, 1.1)
        s.text(bx + 10, by, k, 9.6, PALETTE["slate"]["deep"], "start", 600, family="var(--mono)")
        s.text(bx + 10, by + 14, v, 8.8, MUTED, "start")
    s.lines(596, 528, ["· 原始基准按归档指纹受信，不按文件名；只有加载原始基准才轮换 current-run。",
                       "· autosave.py 当前已无调用方：自动存档猜测被显式禁用。"], 9.4, MUTED, "start", lh=13)

    s.legend(40, 608, [("blue", "显式入口"), ("amber", "门禁"), ("teal", "完成"), ("clay", "未知"),
                       ("green", "可证明未提交")], gap=20)
    return s.render(628)


# ------------------------------------------------------------------ 图 8 外交交易 / 世界议会
def domains() -> str:
    s = Svg(1120)

    def stage(x, y, w, h, name, subs, color, tag=None):
        p = PALETTE[color]
        s.add('<g class="n">')
        s.rect(x, y, w, h, p["soft"], p["edge"], 11, 1.35)
        s.rect(x + 10, y, w - 20, 3, p["c"], None, 2, 1)
        s.text(x + 14, y + 23, name, 11.8, p["deep"], "start", 600)
        if tag:
            s.text(x + w - 12, y + 22, tag, 8.8, p["c"], "end", 500, family="var(--mono)")
        rows = []
        for sub in subs:
            rows += wrap(sub, 9.1, w - 26)
        s.lines(x + 14, y + 40, rows, 9.1, MUTED, "start", lh=12)
        s.add("</g>")

    # ================= 外交与交易
    s.text(40, 42, "外交与交易：两个专用 Progress 共用一套 observer / nonce", 12.4, INK, "start", 600)
    s.add('<g class="n">')
    s.rect(40, 56, 1040, 78, PALETTE["slate"]["soft"], PALETTE["slate"]["edge"], 12, 1.3)
    s.text(56, 80, "DiplomacyProgress · 观察器与 nonce（所有确认的地基）", 11.6, PALETTE["slate"]["deep"], "start", 600)
    s.text(56, 100, "· 每次原生响应必须匹配本请求 nonce；观察器只记录本世界的公开外交事件，不替模型回应。", 9.4, MUTED, "start")
    s.text(56, 118, "· 世界身份三元组 player | civ | seed + 整行 fingerprint：完整但不同 → scope_invalid 永久失效；身份缺失 → 仅本次不能确认。", 9.4, MUTED, "start")
    s.add("</g>")

    s.text(40, 170, "来约（IncomingAttempt）", 11, PALETTE["amber"]["deep"], "start", 600)
    stage(40, 182, 244, 96, "① 打开会话", ["先空条款打开，确认界面就绪", "INSPECT / EQUALIZE + 会话指纹"], "amber", "OPEN")
    stage(304, 182, 244, 96, "② 回执匹配", ["INCOMING_RECEIPT / ZERO / SENT", "绑定世界 · 会话 · 双向条款"], "amber", "RECEIPT")
    stage(568, 182, 244, 96, "③ 结论", ["WILLING / COUNTER_OFFER / REJECTED", "拒绝后的相等条款不能冒充谈妥"], "amber", "VERDICT")
    stage(832, 182, 248, 96, "零提交终结", ["ZERO 回执 + finish(zero=True)", "两者都 CONFIRMED 才可报未提交"], "green", "ZERO")
    s.arrow([(284, 230), (302, 230)], "amber")
    s.arrow([(548, 230), (566, 230)], "amber")
    s.arrow([(812, 230), (830, 230)], "green")

    s.text(40, 314, "我方发约（TradeAttempt · 13 个 phase 压缩）", 11, PALETTE["teal"]["deep"], "start", 600)
    stage(40, 326, 244, 96, "进入条款", ["OPEN_ENTERED → TERMS_ENTERED", "空条款打开后再发送", ], "teal")
    stage(304, 326, 244, 96, "等待回应", ["RESPONSE_WAIT → ACCEPT_READY", "ACCEPT_WAIT 只认原生资格"], "teal")
    stage(568, 326, 244, 96, "对话 / 反提案", ["DIALOGUE / COUNTER", "最终接受需资格 + 双向条款相等"], "teal")
    stage(832, 326, 248, 96, "清理与退役", ["CLEANUP：结果、会话结束、界面", "三者分别确认，丢回应不重发"], "teal")
    s.arrow([(284, 374), (302, 374)], "teal")
    s.arrow([(548, 374), (566, 374)], "teal")
    s.arrow([(812, 374), (830, 374)], "teal")

    s.rect(40, 442, 1040, 54, "#fbfcfe", LINE, 11, 1.1, dash="5 5")
    s.text(56, 463, "未知与取消：只确认原尝试；重新打开会新开会话，不得覆盖原 pending。", 9.6, MUTED, "start")
    s.text(56, 481, "身份未知保留未决；返回原身份不能解除已锁存的失效。", 9.6, MUTED, "start")

    # ================= 世界议会
    s.text(40, 542, "世界议会：原生阶段与本地请求状态是两个正交维度", 12.4, INK, "start", 600)
    s.add('<g class="n">')
    s.rect(40, 556, 1040, 74, PALETTE["purple"]["soft"], PALETTE["purple"]["edge"], 12, 1.3)
    s.text(56, 580, "原生阶段（由引擎给出）", 11.4, PALETTE["purple"]["deep"], "start", 600)
    for i, (a, b) in enumerate([("TURNSEG_WORLDCONGRESS_1", "→ STAGE1"), ("TURNSEG_WORLDCONGRESS_2", "→ STAGE2"),
                                ("TURNSEG_RESOLUTION", "→ RESOLUTION / NONE")]):
        bx = 56 + i * 340
        s.text(bx, 602, a, 9.4, MUTED, "start", family="var(--mono)")
        s.text(bx + 176, 602, b, 9.4, PALETTE["purple"]["c"], "start", 600, family="var(--mono)")
        s.text(bx, 620, ["只有本轮可投", "不重投旧决议", "阶段结束 ≠ 结果"][i], 8.8, FAINT, "start")
    s.add("</g>")

    stage(40, 660, 330, 110, "本地请求状态", ["CHOICES / EMPTY / UNKNOWN 由同一 HasChoices 复算",
                                              "EMPTY 禁开始，只能提交空票计划",
                                              "REQUESTED 先登记，再发原生事件"], "purple")
    stage(390, 660, 330, 110, "打开议会（仅 CHOICES）", ["DequeuePopup(Intro) → ShowWorldCongress",
                                                        "无选择或未知提案不走开始路径",
                                                        "丢应答只核对可见状态，不重复事件"], "purple")
    stage(740, 660, 340, 110, "票计划（queue_wc_votes）", ["每项先预留首票，余量按输入顺序分配",
                                                          "缺票即整体失败；部分完成不重放",
                                                          "分配数只表示请求票单，不代表实付支持"], "purple")
    s.arrow([(370, 715), (388, 715)], "purple")
    s.arrow([(720, 715), (738, 715)], "purple")

    s.add('<g class="n">')
    s.rect(40, 796, 1040, 66, PALETTE["amber"]["soft"], PALETTE["amber"]["edge"], 12, 1.3)
    s.text(56, 820, "REGISTERED 特例：只有显式 end_turn 允许最新可信票单放行；confirm 每次先把 state 重置为 UNKNOWN，",
           10.2, PALETTE["amber"]["deep"], "start", 600)
    s.text(56, 840, "不照搬普通动作的严格同回合规则。WC_STAGE_FINISHED 只表示原阶段结束，不表示议会结果。",
           9.6, MUTED, "start")
    s.add("</g>")
    return s.render(878)
