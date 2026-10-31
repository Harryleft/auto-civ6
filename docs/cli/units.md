# 单位

[返回 CLI 索引](../cli.md)

命令参数和短边界直接读 `help unit COMMAND`。`unit list` 可能顺带确认原未决动作；读取已返回原动作确认或 pending 已清除时，不再为「确认」重复变更命令。只有原请求仍未决时，才沿相同单位、目标与参数确认；旧请求完成后的再次变更属于新意图。

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `unit list` | `get_units` | 只读 |
| `unit move ID X Y` | `unit_action`，action=move | 变更 |
| `unit rebase ID X Y` | `unit_action`，action=rebase | 变更：飞机转场，原生核对基地／航母的范围、准入与容量 |
| `unit action ID ACTION [--x X --y Y] [--improvement TYPE]` | `unit_action` | 变更 |
| `unit attack ID X Y` | `unit_action`，action=attack | 变更 |
| `unit combat-preview ID X Y` | `get_combat_preview` | 只读 |
| `unit skip ID` | `unit_action`，action=skip | 变更 |
| `unit fortify ID` | `unit_action`，action=fortify | 变更 |
| `unit heal ID` | `unit_action`，action=heal | 变更 |
| `unit improve ID IMPROVEMENT_TYPE` | `unit_action`，action=improve | 变更 |
| `unit skip-remaining` | `skip_remaining_units` | 变更 |
| `unit promotions ID` | `get_unit_promotions` | 只读 |
| `unit promote ID PROMOTION_TYPE` | `promote_unit` | 变更 |
| `unit upgrade ID` | `upgrade_unit` | 变更 |
| `unit path ID X Y` | `get_pathing_estimate` | 只读 |
| `unit tasks` | `get_builder_tasks` | 只读 |
| `unit alert ID` | `unit_action`，action=alert | 变更：警戒，敌人进入视野时自动唤醒 |
| `unit sleep ID` | `unit_action`，action=sleep | 变更：休眠 |
| `unit delete ID` | `unit_action`，action=delete | 变更：永久解散单位 |
| `unit cancel-move ID` | `unit_action`，action=cancel_move | 变更：取消非商路移动队列 |
| `unit pillage ID` | `unit_action`，action=pillage | 变更：掠夺当前地块一层设施 |
| `unit found-city ID` | `unit_action`，action=found_city | 变更：建城，消耗开拓者 |
| `unit formations ID` | `get_unit_formations` | 只读：当前护卫／军事编制及候选目标 |
| `unit link ID TARGET_ID` | `set_unit_formation`，action=link | 变更：组成护卫编队，保留两个单位 |
| `unit unlink ID` | `set_unit_formation`，action=unlink | 变更：解除护卫编队 |
| `unit corps ID TARGET_ID` | `set_unit_formation`，action=corps | 变更：合并军团／舰队，消耗一个单位 |
| `unit army ID TARGET_ID` | `set_unit_formation`，action=army | 变更：合并集团军／无敌舰队，消耗一个单位 |

`ID` 是本次查询返回的完整原生单位 ID；坐标、晋升和改良类型取自当前事实。`heal` 是驻扎直至恢复、满血自动唤醒；`fortify` 是驻扎防御。`upgrade` 更换单位兵种，消耗原生要求的金币、资源及行动力；`corps`／`army` 合并军事编制，目标 ID 来自刚读取的 `unit formations ID`。军团资格由引擎检查兵种、当前编制、位置和市政等条件，不按经验等级相同推断；不把“两支同级单位”当作所有合并的通用规则。护卫编队与军事合并是两种不同操作。

`unit promotions ID` 在同次 InGame 查询中分别读取原生 UI 候选和当前执行检查，展示 `UI candidate`、`current candidate` 与逐项 `selectable now`，不把晋升类别或静态前置树当成本次候选。集合缺失、异常或无法解析时显示 unknown，与已知空集合分开；MP／XP 仅作参考，不能单独解释拒绝原因或过滤原生免费晋升。选择使用当前可选项，执行仍重新核对原生资格并受 Runtime 未决门禁约束。

`unit list` 的升级目标取自同次原生命令返回的 `UNIT_TYPE`，与本次原生升级价格配对；目标无法核实时显示 `target unknown`，不使用静态升级树首跳替代。实际 `unit upgrade` 仍重新检查资格、预算、资源和替换结果；提示未列出不能单独区分不可升级与读取未知。

攻城使用 `unit attack ID X Y`。可见目标格存在城市时，攻击绑定城市中心区域，优先于城内驻军。原生资格、攻击方式和路径通过核验后，只提交一次；`UNIT_CITY_ATTACK_CONFIRMED` 同时要求匹配的战斗参与者事件和城防／单位伤害，或近战单位进入已归本方的目标城。返回城墙、驻军、归属和攻击单位生命变化。普通 `unit move` 进入已识别的敌城会返回 `ENEMY_CITY_MOVE`，不提交移动。结果未知时保留原请求，以完全相同的攻击命令确认。

需要判断交战代价时，可先用 `unit combat-preview ID X Y` 读取原生动态强度、双方估计伤害和 Runtime 当前回合前置检查。该检查结合原生事实与客户端约束，可能在调用 `CanStartOperation` 前拒绝；`current_turn_preflight_refused` 不能单独证明引擎禁止攻击。物理近战、远程和轰击使用各自原生模拟；当前不支持神学战斗或空袭。不支持或缺失时保留 unknown，不用基础战斗力推算。查询绑定当前世界、回合、原单位与公开目标，不提交攻击、关闭界面或确认／释放 pending；存在未决操作时先处理原请求。预估不保证实际伤害、存活或下一回合状态，`--dry-run` 仍只做参数校验。

移动确认回执复用原生 POS 观察，在 `now_at` 后保留普通剩余移动力 `moves_remaining`、有效移动力 `movement_moves_remaining` 和 `zoc`，缺失值显示 unknown。普通移动力大于零不单独证明可以继续行动；到达、耗尽、控制区和未决确认的判断仍依据原有原生见证。

飞机使用 `unit rebase ID X Y` 转场，不使用地面 `move`。命令先核对当前原生可转场目标，再提交一次 `REBASE`；只有原飞机到达目标且消耗移动力才确认。结果未知时，以同一单位和目标确认原请求，不重复转场。

其他已有动作也有命名入口：`unit repair/remove-improvement/remove-feature/build-route/automate ID`、`unit activate/sacrifice-charges/spread-religion/launch-inquisition/condemn-heretic/remove-heresy ID`；带目标坐标的入口为 `unit trade-route/teleport/religious-attack ID X Y`。斜线表示分别可用的子命令，不能原样输入。完整参数用 `unit COMMAND --help` 查询；原有 `unit action` 保留。

商人迁移／建路确认回执另带原商人的 `moves_remaining` 观测，来自同一确认请求；读取失败、缺失或绑定不符保留 `unknown`，不默认零，也不撤销已成立的原动作确认。`route_eligibility:unchecked` 表示尚未核验下一条商路的完整资格，正移动力不保证可建路，迁移成功也不保证能立即建路。世界／回合范围守卫及原请求仅确认规则保持不变。

`unit spread-religion ID [X Y]` 可成对提供期望城市坐标；坐标用于绑定确认，不是 SDK 任意定向参数。显式路径仅接受当前与六个相邻地块均存在且可见、仅有一个城市并且坐标相符的情形；原生目标读数冲突、歧义、范围不完整或远目标均明确拒绝且不提交。采用“该唯一本地城市约束原生自动目标”的受限前提，不能据此推广模组或更远目标规则；成功仍须原操作、原单位次数及该 owner／完整 ID 城市的压力或本方 Core 证据，原生资格通过和几何唯一性本身不证明成功。无坐标保留当前城市／唯一原生目标的兼容路径。未决时必须以原目标确认，不能改目标借用旧回执。

所有变更支持 `--dry-run`，只核对 schema，不验证当前资源和目标资格。`delete` 永久删除单位，`corps`／`army` 会消耗一个原单位，执行前先预演并读取当前状态。`skip-remaining` 只处理当前就绪单位，保留既有站立指令。编队／合并结果未知时，保留同一会话与完整参数，通过原命令确认；不能再次选择别的单位绕过门禁。只读 `unit formations` 不提交或退役请求。合并后使用回执给出的存活单位 ID，不假设发起单位一定保留。

本轮编队能力已完成隔离原生 Lua／MCP 与离线 CLI 验证，未在真实对局执行合并；实际原生事件顺序、解锁与合并结果仍需具备条件的受信存档验收。实现与证据见 [单位 CLI 说明](../specs/2026-10-25-unit-cli.md)。

