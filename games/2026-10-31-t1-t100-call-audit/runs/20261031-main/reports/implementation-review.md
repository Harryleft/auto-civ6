# 调用改进的实施与审查

当前源 tip 为 `ab02f53a8b41200954357556d0f334023e8382c5`，已经部署到同一原生 T92 现场继续主局；129 个 source／knowledge 文件与隔离受测树逐一 SHA 相同。最终源离线 `civ6 test debug` 为 434 passed／52.60 秒，`verify knowledge` PASS；两路独立 Standards／Spec 审查没有剩余实质问题。T100 主局、本轮检查点后验和原 T100 恢复已完成并安全关闭。原生 PASS 仅覆盖本页列出的代表性场景；未支持的预估模式、真实小数场景、隐藏状态及总体策略结论仍明确受限。

主局在 `c6fc5e0` 推进 T1→T49，在 `4bbf6d7` 推进 T49→T92，在 `ab02f53` 推进 T92→T100；四段 CLI 均已关闭，原生回执链恰为 99 次，没有 end100。两次集成都保存、清空 pending 并正常关闭原 CLI，再换源接续同一现场，没有重启或读档重跑。详见 [部署与 SHA 清单](../evidence/t092-fixed-deployment.json)、[T92 闭合源冻结](../evidence/source-freeze-t092-closed.json) 和 [调用分析](call-analysis.md)。修复在 managed worktree `codex/call-audit-fixes` 实施后已合入主树；本轮未 push；隔离证据已保全；app 的 `archive_worktree` 对准确 attached identityKey 两次均拒绝 `Another active task still uses this worktree.`。清理受 app 占用保护阻塞，未强删或接管其他任务，worktree 与证据保留。

## 变更与依据

| 提交 | 改动与必要边界 | 已取得证据 |
| --- | --- | --- |
| `bf679af` | 计时接受严格 ready decision 身份和五项 checks 为锚点，仍只以原生 end 回执记 completed；CLI search 默认 brief、API evidence 兼容；move 复用原 POS 的普通／有效 MP 与 ZOC | 计时修前 5 fail，修后 29 pass；T49→T92 实测 43 个 monotonic completed；两次搜索实际 `13,802→2,180`／`14,252→2,626`；T50 移动普通 MP 独立对照 |
| `57fdaed` | UI `SESSION_CLOSED` 但原 trade pending 时提示 player／phase／模式及保持原参仅确认；不增 native 调用、不自动清理或放开门禁 | 交易集合 152 pass；T73 原参数逐字段相同，单次付 14，收尾后整数余额仍 93、通行权 active、pending 清空 |
| `9db4c91` | map 保留原生小数与负非零值；每份增加 207 字节来源／零／未知说明，不增加查询 | 小数原 `4.75/2.5/-0.75→4/2/0` 缺陷离线回归 16 pass，地图契约 54 pass；T94 原生有新来源说明。实机整数差异根因仍未证 |
| `44ad971` | 商人同次确认 query 增可选 post-MP，严格绑定原 token／kind／完整 ID；坏扩展只让 MP unknown，不撤销已成立主确认；正 MP 不等于路线资格 | 174 pass；固定新旧均 3 write／0 read、1 submit、0 chooser 修改；新 getter MP／ID／owner／type 各 1 加 scope，无新 RPC。T88 主局 ACK MP=0；后验 transfer96／unit99 同回合 MP=0／2 独立一致 |
| `1ff8263` | 只将全行显式存在、类型合适且省字节的 `ModifierArguments.Extra/SecondExtra/Type` 移入既有 `common_facts`，行级例外覆盖；missing 不等于 null | 三领袖真实 CLI 合计省 41,534 字节／14.68%；1,857 卡离线双向还原、指纹相同且无卡增大；知识 58 pass、验证 PASS |
| `f3aceb2`、`4bbf6d7` | `unit spread-religion ID [X Y]` 目标贯穿原 intent；当前及六邻格完整可见且唯一城市，坐标约束预期城市，不伪称 SDK 任意坐标定向；稀疏数组和 pending 不匹配先拒绝 | 审查前 5 fail；修后 509／184／39 集合通过，彼此有重叠；T49 邻格传教原生压力增加、次数减少、actor 留原格、pending 清空 |
| `a24b265` | 政府总开关与当前执行窗口分开，补逐目标资格／拒因、anarchy 等原条件；unknown 保留；keep-current ACK 与新政体选择分开，不混政策现金条件 | 105 pass；768 组布尔组合验证原写侧读序／短路／Request／ACK 相同；T93 general/current/window=true 与 keep-current ACK 原生覆盖；T57 后验总开关 true、当前 false，所有非当前目标明确无免费换政窗口 |
| `93270b0`、`7ece823` | 新只读 `unit combat-preview FULL_ID X Y`，绑定公开物理目标、单位／世界／回合、原生强度与伤害；2 个 InGame reads、1 次模拟，不攻击或处理 pending；不支持 AIR／神学 | 审查 11 个反例修复；272 native/parser 与 317 state/server 为 589 项定向通过，preview 105／stdio 2 为子集；6 默认 payload 字节不变；T94 宗教 actor unknown 是边界证据；T57 后验勇士动态强度15对25、预估伤害44／20，前后可观察状态不变且无攻击 |
| `37eebcd` | 来约用原生 subtype 名与 DiplomaticActions 说明协议，保留 3 旧别名；缺失显示 Unknown Agreement 及 raw 值；不改金额、期限或响应 | 本机静态 Types 精确映射 `547027585→DIPLOACTION_MAKE_PEACE`；10 red→239 pass，固定 `333→336` 字节，working-deal read 仍 1、mutation 0；改后T63原生来约明确显示和解9回合；单次接受实收6金币、关系WAR→UNFRIENDLY，收尾后原63→64确认 |
| `07f062f`、`f73c0d1` | 晋升改读原生 UI 候选与当前资格／候选，完整身份前后绑定；不按 XP 删除免费候选，不改写入口；严格单报告与可选一个末尾 SENTINEL，冲突帧 unknown | 混入 ERR／旧 UNIT 的 13 反例修复，相关 483 pass；T92 先资格 no／集合 unknown，后只有 92／95／96 可选，Proselytizer 单次成功，不含 Debater |
| `875e083` | 原同次 InGame 城市查询增加多数宗教 ID／原生名称，复用 CITYFACTS full-ID／owner／observer／seed；`-1=none`、`0=有效`、`None=unknown`，坏扩展不改旧字段 | 490 pass，含 stdio city／decision；T92 默认包自然显示本方 4／4 道教；T80 后验四城 [11,8,11,-1] 与独立完整 religion scan 逐项一致 |
| `29ef3b6` | preview 的资格来源收窄为 `current_turn_preflight_passed/refused`，说明 Runtime 组合 guard 可早于原生资格，不等于引擎 false；不改 Lua 或 guard | 1 red；310 native/parser、237 CLI/server 通过，集合重叠；ZOC 例证明 CanStart 0、submit 0、simulation 0 |
| `ab02f53` | 升级 hint 复用既有 CanStartCommand 第二返回值中的 `UnitCommandResults.UNIT_TYPE`，替静态首跳；不合法则 unknown，原生 index 0 有效；价格／资源／writer 不改 | 13 red→167 pass／1.59 秒；0 新 getter／RPC，固定 `123→125` 字节。T88 后验 hint Man-at-Arms／165g，单次替换单位类型独立核对一致、精确金币实扣165；资源 quote13／net-change−11 保留为未定因 |

补缺不是统一压缩：政府单目标夹具正文 `344→1,145`（+801），晋升 9 类／3 原生候选夹具 `475→789`，三城视图 `722→878`（+156）、native 正文 `725→934`（+209）。政府仍 1 RPC，但新增 7 个全局 getter、每个已解锁目标的 anarchy 读取及至多一次 previousEnd；三城两宗教固定场景新增 9 个 getter、查询仍 1。晋升固定 getter 由 CanPromote×9／HasPromotion×18 变为 CanStart×2／HasPromotion×3／GetPromotions×1，不是实机 CPU 结论。

知识压缩保留完整展开、逐卡规则和证据；34 个知识文件仅 encoder 与 manifest 对应 support hash 改变，原始快照／规则／profile／其他 support 指纹不变。全卡审计复用一次静态加载，不能称 1,857 次真实 stdio。指定三领袖的实际 CLI 字节分别为 Laurier `109,645→93,731`、Kristina `89,587→76,617`、Pedro `83,651→71,001`，rule／evidence 逐字节相同；不是整局降幅。

## 审查中修掉的边界

两路只读审查固定提交，不连接游戏。Standards 核对项目约束、范围与接口兼容，Spec 构造反例并核对原生 SDK。曾发现并修复的实质问题包括：邻城传教稀疏数组漏冲突；更改 pending intent 后夹带新动作；战斗拒绝缺末次绑定、成功／拒绝混帧、城市与驻军栈优先级、过期 HP；晋升 JSON 后混入 ERR 或旧 UNIT 帧。最终相应反例复验通过；「无剩余实质问题」仅指对应固定源码和审查范围，不代替 T100 或未取得的原生场景。

`d2410fa` 只校准旧测试断言：abilities 原默认 brief、缺 POS 可选值时的三个 unknown 后缀，在冻结 `4bbf6d7` 已相同；分别 27／6 pass。`1a3cb9a` 只更新新 combat-help 契约。`d5cb53e` 只纠正宗教分布 scope 文案：主要文明中本方或已揭示城市，不含城邦／压力，新鲜度未证；生成 Lua 逐字节相同。本机原生视图存在 revealed 展示路径，没有仅凭 IsRevealed 认定越界。

## 初始化失败及最终离线门槛

一次 `f73c0d1` debug 集合为 432 passed／1 failed，原临时目录证明失败发生在 initialize，submissions=0，业务未知动作场景尚未执行。确定性 SDK 诊断复现了极短初始化超时后晚回复触及关闭接收端，BrokenResourceError 掩盖原超时；不能说未知业务 intent 被清除。

`8549772` 只调测试 helper 的初始化／发现预算至 30 秒，业务 RPC 保持 2 秒。受控 3 秒启动原红后绿，随后两次业务调用仍实测短超时，首次 unknown、单次提交、pending、BLOCKED／context_lost 断言保持；没有重试或生产 Runtime／SDK／依赖改动。SDK 极短初始化预算下的关闭现象未做生产修复。原失败、确定性诊断、单项及完整重跑都保留。

| 固定源 tip | 离线门槛 | 解释 |
| --- | --- | --- |
| `9db4c91` | debug 430 passed／41.49 秒 | 中间集成，原件保留 |
| `4bbf6d7` | debug 433 passed／43.03 秒 | T49 部署版本 |
| `29ef3b6` | debug 434 passed／51.66 秒，knowledge PASS | 中间集成，非最终 tip |
| `ab02f53` | debug 434 passed／52.60 秒，knowledge PASS | 最终受测树干净，CLI／pytest 退出 0；与部署 129 文件 SHA 相同 |

各定向集合与 debug 集合重叠，不相加为独立测试总数。离线验证没有连接游戏；完整 debug PASS 也不表示所有原生场景已覆盖。

## 原生验证结果与限制

| 范围 | 原生结果 | 证据与限制 |
| --- | --- | --- |
| T49 邻城传教、T49→50 计时、T50 移动 | 已覆盖 | [固定原案](../evidence/t049-fixed-native-cases.json)：非斯压力 `3.83203125→206.32421875`，actor 仍 `(28,27)`、次数 2→1；49→50 仅 decision 锚得到 monotonic completed，wall 63.838028／CLI 7.654041／RPC 7.550915 秒；T50 独立核对位置／普通 MP=0，不独立证明 effective／ZOC |
| T73 交易尾部 | 已覆盖 | [原参数及回执](../evidence/t073-trade-tail-native.json)：107→93→93 为整数余额；EXIT 提示、原参确认、pending 清空，不再付 14。额外末次读单列 verification |
| T88 商人 | 已独立对照 | 主局 transfer2694 原生 MP=0；后验 transfer96／unit99 同回合 0／2，未推进。独立对照单列额外验收成本 |
| T92 晋升及默认城市 | 已覆盖所列状态 | [固定截点](../evidence/t092-promotion-native-cases.json)：7 资格 no／集合 unknown；28 原生候选 92／95／96；40 Proselytizer 单次成功。期间状态变化不证明初次拒绝唯一原因；默认 4／4 道教，不代替混合／none 对照 |
| T93 政府、T94 来源／preview | 部分覆盖 | [固定截点](../evidence/t094-native-boundaries.json)：general/current/civic-window=true，keep-current ACK 后原推进；地图来源说明出现。宗教 actor preview 为 unknown，不记军事数值 PASS |
| T57 政府拒绝窗口／军事数值 | 已覆盖固定现场 | result32 无免费换政窗口；result37 可见物理近战动态强度15／25、预估自身44／对手20。前后单位／精确余额／队列／pending 一致，未攻击；不保证 RNG 或未公开状态不变 |
| T80 混合／none 城市 | 已独立对照 | city61／religion64 四城 ID→majority `[65536:11,131073:8,196610:11,262147:-1]` 完全一致；玩家 majority=-1／全局stat4不代替本方道教2城 |
| T88 升级 hint／金币 | 已对照目标和精确扣款 | hint81／99 Man-at-Arms／165g，单次102替换1835017，105原生类型／位置／MP0一致；89→108金币266.6484375→101.6484375。resource_quote13／net_change−11保留，未解释资源时序 |
| 来约新名称 | 已原生复验 | result328明确和解9回合／6金币；339单次接受，331→342金币225.34765625→231.34765625，334→348关系WAR→UNFRIENDLY，353关闭；议会收尾后370确认原63→64 |
| T100 主局停止与保存 | 已完成 | 原生 99→100 result462；decision465／empire468／status471／save476／pending478／closing480 单列 closing，pending 全 false、unresolved=false、exit0／MCP 正常关闭；没有 end100 |
| 后验后的原 T100 恢复 | 已完成 | 382恢复原检查点，官方273→273／完整分类不变，385decision／388精确帝国／391队列读回；396另存verified855146B，398pending全false／transport null，400安全退出；未end100 |

T78 宗教攻击被 Runtime ZOC guard 提前拒绝，没有到原生 CanStart；T94 物理 preview 对宗教 actor 返回 unknown 也不能反推神学资格。没有放松 guard。全局宗教统计与逐城多数宗教冲突、map／workforce 实机整数差异仍按来源和时点保留，不编造缓存根因。单局存在超射程、耗尽后攻击、危险前出及黑暗时代等模型失误，不能把测试通过写成总体策略质量不退化。

原 T100 检查点 `callaudit_20261031_t100_final` 为 855,101 字节，SHA-256 `6bd873cca4f18ece147f1dff22fc4a0c36056e9660f7e1c7d3e83661c2c9b779`；[原件注册与关闭证据](../evidence/t100-original-checkpoint.json) 和 [最终主局调用统计](../evidence/t100-main-closed-call-analysis.json) 均保留。正常 play 为 2,456 命令／2,074,190 正文字节／1,040.359359 秒 RPC，额外后验不计入此数。T100 本方 4 城／29 人口、4 城道教，仍黑暗时代且科技落后；不推断胜率或整体策略质量。

## 检查点后验的证据范围

后验 [verification-ab02f53-pty/debug.jsonl](../evidence/verification-ab02f53-pty/debug.jsonl) 已完整关闭；CLI `db09f482116a4560b27904c211d59f6a`／runtime `60d8253b51bb435ca125829b42760f6a`，[summary](../evidence/verification-ab02f53-pty/summary.json) 为PASS／exit0、97工具调用、MCP正常关闭、context_lost=false／unresolved=false。最初sequence≤119的固定场景保留如下。T57 前 decision23 的单位／empire26／status29 与后 units40／empire43／status46、pending50 对照一致，忽略观测时长；没有攻击，不能扩大为随机数或所有隐藏状态不变。T80 同回合 map67／workforce70 的 9 个本方地块仍有整数差异，例如 (41,26) F4P0 对 F5P2、(40,25) P0 对 P1；真实小数本例未覆盖，来源与修正新鲜度限制继续保留。

T88 后验 pending110 全 false、transport null；另存 result119 `callaudit_20261031_t088_verification` 827,239 字节，原 T88 与 T100 未覆盖。验证中的多级 help、pending show、save capture 用法错误属于额外验证操作成本，主局统计不变。首个非 PTY 验证进程仅在 0 工具调用时 EOF，原件 `evidence/verification-ab02f53/` 保留；不是游戏测试或第二个原生控制器。和平来约有限支线与原 T100 恢复已完成，后续结果如下。

T60有限支线按当时事实逐回合决策，选择粮仓、伤兵heal、弓箭手显式续原队列／驻扎、学徒／封建、莫克夏到埃利都和南马都尔使者等，没有通用驾驶脚本。只有178／221／293／370的4个推进回执，组成60→64，未超过5次预算；其他end只是确认原请求。议会359当前两个选择、364stage finished、367NONE，再370确认原63→64；不把本地投票计划当最终胜负。pending372清空，另存377 `callaudit_20261031_t064_peace_verification` 765407B。

后验用法错误（包括citystate拼写）与T61 skip-remaining被CANNOT_OPERATE拒绝如实保留，均为额外成本；后者在读当前战车393219 ready后改fortify，没有重复skip。最终恢复原T100后另存 `callaudit_20261031_t100_verified` 855146B，原T100未覆盖，普通2097162移动队列不算Runtime pending；没有end100。公开读回和分数相同不证明隐藏状态或RNG完全一致。真实小数收益场景未覆盖、神学／AIR预估当前不支持、资源时序差异与整体质量／胜率未知。

[最终机器核对](../evidence/verification-native-final.json) 证明129个source／knowledge文件仍与ab02一致。原主局decision465与恢复385的identity／checks和units、cities、threats、victory、notifications、blockers、outcome七段文本逐字相同，精确empire468=388，队列与外交／议会／信息行相同（忽略计时时长）；overview仅同数量兵种枚举顺序变化，兵种／数量排序后全同。没有将完整decision或binary world判为相同。T57 preview前后公开单位全文、精确empire、队列特殊行一致断言通过。

[后验独立调用统计](../evidence/verification-closed-call-analysis.json) 为151命令／126436B／173.946831秒RPC，4次额外推进、4条错误、0条明确unknown。该输入未另划phase，分析器保留unclassified，全部不算正常主局调用。最终主局timing在T92～T99有8个monotonic completed、T100 partial；前段T49～T91有43个completed、T92 partial，主动停止的partial不等于未推进。

## 原始产物与复核入口

[offline-fixes](../evidence/offline-fixes/) 按提交保存原始输出、失败、定向测试、审查和复制 SHA 清单。主要目录为 `implementation-bf679af/`、`trade-close-57fdaed/`、`map-yield-9db4c91/`、`trader-post-moves-44ad971/`、`knowledge-defaults-1ff8263/`、`spread-target-f3aceb2/`、`spread-boundaries-4bbf6d7/`、`government-facts-a24b265/`、`combat-preview-93270b0/`、`combat-boundaries-7ece823/`、`incoming-names-37eebcd/`、`promotion-candidates-07f062f/`、`promotion-frames-f73c0d1/`、`stdio-startup-diagnosis/`、`fixture-budget-8549772/`、`city-majority-875e083/`、`preflight-source-29ef3b6/`、`upgrade-target-ab02f53/`、`integration-ab02f53/` 和 `knowledge-ab02f53/`。早期失败和中间版本结果未覆盖；这些原件位于忽略证据目录，不等同远程已发布产物。

隔离树完整证据保全于 [worktree-preserved](../evidence/worktree-preserved/)，[镜像manifest](../evidence/worktree-preserved/manifest-20261031T083200264070Z.json) 逐文件核对3693文件／47419396B、SHA／大小一致，缺失0／冲突0／源变化0；512个内部current链接改为对应镜像相对目标，原始link文本及SHA保留。失败诊断未删除。隔离证据已保全；app 的 `archive_worktree` 对准确 attached identityKey 两次均拒绝 `Another active task still uses this worktree.`。清理受 app 占用保护阻塞，未强删或接管其他任务，worktree 与证据保留。

[最终检查点清单](checkpoints.json) 已核对 42 份：主局 39 份／28,290,321 字节，额外 QA 3 份／2,447,792 字节，共 30,738,113 字节；42 条原生保存回执对应文件／注册／身份／SHA，缺漏和冲突均为 0。8 条保存前置拒绝均未提交，不计成功检查点；原 T100 与另存 verified 分开，存档二进制不加入 Git。
