# 本轮问题记录

| 编号 | 观察 | 分类 | 处理 |
| --- | --- | --- | --- |
| C01 | 两次 state overview 的握手缺少 GameCore/InGame，随后重连被拒；tuner status 显示端口 accepting 且无 ESTABLISHED | 状态诊断信息不足 | 同一 CLI 的 save reload 原始基准成功；未重启游戏 |
| C02 | T10 分别填政策槽，首槽成功后其余槽被 POLICIES_ALREADY_CHANGED 拒绝，FILL_CIVIC_SLOT 持续阻挡 | 驾驶器方案错误；CLI 允许会导致阻挡的部分方案 | 恢复本轮 T10 检查点，改为一次提交全部空槽 |
| C03 | T10 检查点加载后官方总分 11→15，正权重分类向量不变 | 原生读数差异，原因未定 | 加载回执明确保留差异；不把分数当作完整状态等价证明 |
| D01 | T11 侦察兵自动探索后回合重开，返回 REOPENED_LOCAL | CLI 已提供明确结果，驾驶器缺少分流 | 处理就绪单位，再单独请求新推进 |
| D02 | 首次 city production 误用 0 而非当前 city list 返回的 65536；查询返回 No production options available | 模型实参错误；返回未明确城市不存在 | 随即使用当前 ID 查询成功；无游戏副作用 |
| C04 | T37 交易返回 ACCEPTED 且开放边界 active；EXIT 后会话列表为空，但 Runtime 的 diplomacy pending 仍为 true | 交易确认链需要额外原入口确认，收尾提示不足 | 用完全相同的 trade propose 参数仅确认原请求，门禁解除；确认前后金币均64，无重复付款 |
| C05 | T1 未建城时 ranking show 显示 original capital lost | 前提不足时展示含义易误读 | 尚未建都，不将该文案解释为首都被占领 |
| D03 | T44 驾驶器在未满足升级条件时尝试战车升级，UNIT_UPGRADE_REFUSED/CANNOT_UPGRADE | 驾驶器缺少资格检查；原生正确拒绝 | 删除无资格依据的自动升级分支；无未决 |
| C06 | T44 概览官方分数42，分类总和46，工具明确标注 inconsistent | 查询数值一致性问题，未定来源 | 分别保留官方值与分类，不据此改写状态或判定回合失败 |
| C07 | T30起多次 congress show 显示 FIRES THIS TURN，但当时无投票阶段，首次实际 STAGE1 在 T63 | 会期倒计时展示不足以代表当前强制交互 | 依 turn status 与 native stage 分流，未提前提交空票单 |
| C08 | T87 send_envoy(9) 提交后持续 INFLUENCE_DELTA_UNCONFIRMED；城邦显示3使者、可派1，原入口确认仍未知，pending action 保留 | 实机 FAIL：使者结果无法确认，阻断后续写入；具体原生差分未知 | 保留原请求与只读证据，按本轮T85检查点显式恢复；恢复后T87仍是强制使者待办，选择当前合法候选香港(7)，成功确认令牌1→0、使者1→2；非斯故障未宣称修复 |
| C09 | T88 票单计划成本12/30，阶段结束时 favor24，T89回读25 | 现有议会回执仅确认阶段、未证明逐项实际票数和费用 | 保留 requested allocation 与 native phase 的范围；不将计划成本或阶段结束当成逐票接受证据 |

| C10 | 最终 snapshot 的 game_speed/game_speed_name 为空、enabled_victories=[]、speed_cost_multiplier=100；基准为快速，ranking 可读启用路线 | 结构化快照配置字段不完整／缺省值含义不清 | 配置以受信基准与独立领域查询核对；不将空列表解释为禁用胜利 |

C02 失败状态没有未决请求；恢复保留同一测试轮次、CLI/MCP 进程，原始基准未变。

证据归属说明：本轮由当前 Codex 会话驱动，早段复用的临时转接客户端将请求 actor 固定写为 deepseek。该标签是历史脚本常量，不代表调用了另一个模型；原始回执保留，后续客户端已改为 codex。CLI 自身 actor 为 cli。
