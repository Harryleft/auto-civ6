# 四叶草连续实战调用分析

主局已从四叶草原始 T1 连续推进到 T100，四段 CLI 均正常关闭，未结束 T100。原生推进链恰为 99 次；原 T100 检查点已保存。必要修复已部署，主局自然覆盖与本轮检查点后验均已完成；额外和平来约支线推进 4 次后已恢复原 T100、另存验证检查点并正常关闭。原生验收覆盖本页列明的场景，不外推未支持能力或总体策略质量。

本轮已证实两类收益：知识返回可以无损减少重复正文；候选、当前资格与确认回执可以补齐决策所需事实。后一类有时增加字节，价值在于减少另找来源、试错或误解资格的成本。单局和不同回合的总量不能证明整体速度、胜率或策略质量没有退化。

## 范围与版本

原始基准为 `fixtures/saves/benchmark_gold_tun_1.Civ6Save`，四叶草、极小、苏美尔／吉尔伽美什、神级、快速速度；原件 SHA-256 为 `0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`。本轮游戏 `run_id=ff463c39d34a49bbb0f86f2472e79b34`，原件与既有结果未覆盖。

| 主局阶段 | 源版本 | 状态与证据目录 |
| --- | --- | --- |
| T1→T6 | `c6fc5e0` | 已关闭，`evidence/baseline/` |
| T6→T49 | `c6fc5e0` | 已关闭，`evidence/play-t006-t100/`；目录名不代表推进到 T100 |
| T49→T92 | `4bbf6d7` | 已关闭，`evidence/play-t049-t100-fixed/` |
| T92→T100 | `ab02f53` | 已关闭，`evidence/play-t092-t100-repaired/`；到达 T100 后只验收／保存，没有 end100 |

两次集成都先保存、核对 pending、正常关闭原 CLI，再启动新 CLI；没有重启游戏或读档重跑主线。T92 的 129 个 source／knowledge 文件与隔离受测树逐一 SHA 相同，见 [部署清单](../evidence/t092-fixed-deployment.json)。原 T49→T92 源冻结及正常关闭见 [冻结清单](../evidence/source-freeze-t092-closed.json)；[T92 检查点截点库存](../evidence/checkpoints/inventory-through-t092-closed.json) 当时核对 36 份存档；[最终检查点清单](checkpoints.json) 已核对 42 份：主局 39 份／28,290,321 字节，额外 QA 3 份／2,447,792 字节，共 30,738,113 字节，文件／注册／原生回执／身份／SHA 无缺漏或冲突。

## 已闭合调用量

[最终主局统计](../evidence/t100-main-closed-call-analysis.json) 的原生 `Turn N -> M` 回执逐项组成连续 T1→T100，共 99 次推进，无缺失或重复，四段均 closed。正常 play 共 2,456 条命令、2,074,190 正文 UTF-8 字节、1,040.359359 秒 CLI 到工具的 RPC 延迟。旧 [T92 截点统计](../evidence/t092-closed-call-analysis.json) 保留。

| 阶段 | 命令 | 正文字节 | RPC 秒 | 确认推进 |
| --- | ---: | ---: | ---: | ---: |
| 正常 play | 2,456 | 2,074,190 | 1,040.359359 | 99 |
| setup | 8 | 831 | 34.219808 | 0 |
| handoff | 15 | 619 | 2.342864 | 0 |
| closing／终点验收 | 7 | 9,850 | 1.345292 | 0 |
| 额外 verification | 2 | 1,823 | 0.174866 | 0 |

正常 play 包含 help 66、本地命令 25、变更／生命周期入口 891、只读 619、schema dry-run 855；变更入口数量不等于实际提交次数。58 条错误命令包含 Runtime 前置拒绝、普通资格拒绝及模型参数错误，不能统称引擎失败。3 条明确 unknown 命令涉及 T7 原移动结果未知、T28 交易未收尾门禁及 T94 不支持宗教 actor 的 preview 查询；不代表 3 次动作结果未知、重复提交或传输故障。

分析器 [analyze_calls.py](../changes/analyze_calls.py) 只读指定证据；[阶段清单](../changes/analysis-phases.json) 显式区分 play、setup、handoff 与 verification。正文计 `content[].text` 的 UTF-8 字节，不是 token 或完整协议包装；RPC 不是引擎 CPU 时间。不同客户端、世界和阶段窗口不拼连续墙钟；gap 包含模型／服务、shell、协调和等待，不能称纯思考时间。额外 T50 单位核对和 T73 余额核对不计入正常 play。

旧计时导出仅记根段 0、续段 13 个 completed，与真实推进不符。严格回执重放能恢复旧段 48 次推进，但原单调 tick 未保存，不能据此重测耗时；7 次 blocked decision 不作为 ready 锚点。修后 T49→T92 的 T49～T91 实际记录 43 个 completed monotonic 回合，T92 保留 partial；最终 T92→T100 的 T92～T99 记录 8 个 completed，T100 因主动停止保留 partial。partial 不等于未推进。高回合查询、未知字段和静态适用性警告均不能补造推进或动作失败。

## 已证实的读取与调用改进

| 发现与实施 | 已验证收益 | 边界 |
| --- | --- | --- |
| 搜索重复携带环境 profile；CLI 默认 brief，精确全文可展开 | 实际两次命名 CLI 正文 `13,802→2,180`、`14,252→2,626`，分别减少 84.21%／81.57% | 保留结果、条件、警告、截断、未知字段与完整指纹；不减少搜索次数 |
| 规则 `ModifierArguments` 重复可空字段；复用现有 `common_facts` | 三领袖实际 CLI `282,883→241,349`，减少 41,534 字节／14.68%；1,857 卡离线双向还原且无卡增大 | rule／evidence 字节及 card／profile SHA 不变；全卡为一次静态加载的审计，不是 1,857 次 stdio |
| 移动确认丢掉已有 MP／effective MP／ZOC | 原确认直接展示，不增加查询；T50 普通 MP=0 有独立单位读取对照，T78／T94 真实出现 ZOC=true | ACK 是该时点观察；T67 后续攻击仍可变成 NO_MOVES，不能当作未来许可 |
| 商人转城确认没有 post-MP | 主局 T88 原生 transfer 回执直接给 MP=0，模型随后没有尝试零 MP 路线；后验同回合独立单位读取为 0／2 | 正 MP 也不保证路线资格；独立对照属于额外后验，不宣称主局已经省掉该读取 |
| 交易 UI 关闭但 Runtime 原请求仍 pending | T73 新提示带 player／phase／send，原参数仅确认后 pending 清空；整数余额 `107→93→93`、通行权 active | 没有自动清理、放开门禁或再支付；末次余额是额外 verification，未暴露 raw 小数余额 |
| 当前候选与规则全集混淆；政府／晋升／城市宗教补原生事实 | T92 晋升只列真实可选 92／95／96，Proselytizer 单次成功；默认城市已有逐城多数宗教；T93 政府当前窗口可读 | 固定夹具政府 `344→1,145`、晋升 `475→789`、城市视图 `722→878` 字节，属于补缺，不能冒称字节下降 |

知识数据与测试原件、实现边界见 [实施与审查](implementation-review.md)。指定案例的降幅不外推为整局降幅，也不以 T1～T49 和 T49～T92 不同局面作受控速度对比。

## 优先复用现有调用

以下成本主要通过已有接口和读取顺序解决，不需要通用决策脚本或每回合强制新增读取。

- 第一次 `state decision` 即按需求 include research，或用 `research show`／`technology status`／`civic status` 补查。初段 result 69／72 的默认八个 sections 相同，后者为补研究又读到 9,117 字节；排名 result 32／38 的 1,323 字节也逐字相同。
- 已有 `unit link` 能让战车与移民一起移动，T32 已原生验证；只在确有同行需求时使用，建编队本身也有成本。
- 已有近战 `unit attack` 可在当前回合移动后攻击，T67 原生回执及独立 HP／MP 已证明；不把所有接敌都拆成移动再攻击，也不把攻击成功等同目标消灭。独立 `combat-preview` 用于不愿提交攻击时补当前数值，增加查询成本，按决策需要使用。
- 生产／研究可用现有 future queue；不因等待完成而重复提交当前项目。模型仍须基于当时公开事实选择队列，不能固化优先级循环。
- `state decision` 的 blocked 状态和 pending 是执行门禁；不反复全量读取以期待自动解除。原参数确认、弹窗或明确待办应按原证据处理；未知结果只读核对，不能重新发动作。
- `threats` 含当前公开军队与邻近风险，包括和平时期军队，不能把未开战解释为没有威胁；仅当当前决策确需地形、路径或宗教压力时补对应窄查询。
- T28 显式三次 `knowledge ... --level summary` 合计 41,086 字节，属于身份用途的显式调用；能力规划已有 brief，不能把这三次调用说成默认路径缺陷。

## 信息缺口、模型失误与未知

| 分类 | 原生观察与处理 | 结论 |
| --- | --- | --- |
| 已修接口缺口 | T49 邻城传教原专用命令不传目标；新显式坐标绑定唯一可见邻城，压力 `3.83203125→206.32421875`、次数 2→1，actor 留在邻格 | 减少无必要进城移动；压力增加不等于该城改宗 |
| 已修接口缺口 | T57 政府总开关 true 后换政被拒；T71／T91 旧晋升列 9 类，当前资格或候选成员拒绝；T77 从贸易目的地意外发现本方城市改宗 | 分别补执行窗口、原生 UI／当前候选和逐城多数宗教，保留 unknown；T57 原拒因不能事后唯一归因 |
| 已修误导展示 | T89 战士 hint 写静态 Swordsman、报价 80，实际升级为 Man-at-Arms；地图小数曾被截断，来约 Agreement 未显示和平类型 | 升级复用原生返回的 UNIT_TYPE，不改 writer；地图整数来源差异仍未知；和平显示补丁已由 T60→T64 有限支线覆盖「和解 9 回合／6 金币」，未泛化为所有协议均已验证 |
| 模型失误 | T65 射手超射程、道路行军耗尽后仍尝试攻击；T70 射手前出受损，后撤救援；T89 黑暗时代 | 不通过隐藏错误或放松 guard 消除；单局策略表现不能记为质量 PASS |
| 原生状态／范围未知 | T67 移动 ACK 尚有 1 MP，后续攻击为 NO_MOVES；T81 InGame／GameCore 宗教统计冲突 | 保留时点与原读数，不编造缓存根因；全局宗教计数不代替本方逐城多数宗教 |
| 仍有能力边界 | T78 宗教攻击由 Runtime ZOC guard 提前拒绝，未到原生 CanStart；T94 物理 preview 用宗教 actor 返回 unknown | 不等于引擎禁止，也不是军事数值预估 PASS；没有放松 guard 或虚构神学预估 |

原案分别见 [T49 固定实测](../evidence/t049-fixed-native-cases.json)、[T70 自然案例](../evidence/t070-natural-cases.json)、[T73 收尾](../evidence/t073-trade-tail-native.json)、[T80 宗教原案](../evidence/t080-owned-religion-original-cases.json)、[T92 晋升](../evidence/t092-promotion-native-cases.json)、[T94 边界](../evidence/t094-native-boundaries.json) 及 [T49 后对局记录](t049-t100.md)。旧 [T25 截点](t025-interim.md) 和原始忽略证据目录保留，不用更新后的汇总代替原件。

## T100 终点与恢复

主局最后 result 462 明确 99→100，decision 465、精确帝国读值 468、turn status 471、save 476、pending 478、closing 480 单列终点验收。原检查点 `callaudit_20261031_t100_final` 为 855,101 字节，SHA-256 `6bd873cca4f18ece147f1dff22fc4a0c36056e9660f7e1c7d3e83661c2c9b779`，[注册与关闭证据](../evidence/t100-original-checkpoint.json) 保留；pending 全 false、transport null、unresolved=false，CLI exit0／MCP 正常关闭。没有 end100。

终点为本方 4 城／29 人口、4 城道教；精确金币 97.46875、信仰 344.6796875、科研 53.89453125、文化 59.80859375。仍为黑暗时代，科技落后，国外宗教转化也未永久保持；这些策略限制不能被到达 T100 或测试通过掩盖。

已完成以下本轮检查点后验、和平来约支线与原 T100 恢复。额外支线只有 4 次推进，不计主局 99 次推进或调用量；原件与原 T100 存档均保留。整体验收判断本轮事实、候选、单次提交和恢复是否可靠，不以一局证明总体胜率或决策质量不退化。

## 检查点后验：已关闭

[原生验证日志](../evidence/verification-ab02f53-pty/debug.jsonl) 已完整关闭，CLI `db09f482116a4560b27904c211d59f6a`／runtime `60d8253b51bb435ca125829b42760f6a`；[关闭回执](../evidence/verification-ab02f53-pty/summary.json) 为 PASS／exit0、97 次工具调用、MCP 正常关闭、context_lost=false、unresolved=false。以下先保留最初 sequence≤119 的三个固定场景，再列后续和平与恢复。T57、T80、T88、T60 均从本轮已确认检查点恢复，原件与原 T100 检查点保留。

| 场景 | 原生结果 | 质量与范围边界 |
| --- | --- | --- |
| T57 政府窗口 | result32 总开关 true、当前窗口 false、civic-completed/free-window=false；非当前目标均给 `NO_FREE_GOVERNMENT_CHANGE_WINDOW` | 当前拒因可在选新政体前读出；没有混为付费政策资金条件，也没有重放拒绝写动作 |
| T57 物理近战预估 | result37 勇士 131073、HP51，(33,19)→可见蛮族 3276801 (34,18)；动态强度 15 对 25，预估自身受伤 44、对方 20，当前预检 passed | 未攻击；前后单位、精确帝国余额、队列和 pending 一致（不比较观测时长）。不证明 RNG／未公开状态不变或实际伤害必然相等 |
| T80 逐城宗教 | city61 与独立 religion64 对四城逐项一致：`65536:11,131073:8,196610:11,262147:-1`，完整扫描 | 道教 2、新教 1、none 1；玩家 majority=-1／全局 stat4 不替代逐城事实。补缺避免为发现本方城市多数宗教另找贸易目的地或压力整包 |
| T80 地图来源 | map67 与 workforce70 同回合对 9 个本方地块比较；(41,26) F4P0 对 F5P2、(40,25) P0 对 P1 等整数差异仍在 | 证明来源边界仍必要；未覆盖真实小数收益，不解释缓存或引擎修正根因 |
| T88 商人与升级 | transfer96 商人 1769484 到尼普尔 (35,20)、post-MP0，独立 unit99 为同回合 0／2；hint81／99 为 Man-at-Arms／165g，单次 upgrade102 替换为 1835017、unit105 确认类型／位置／MP0；金币 `266.6484375→101.6484375`，实扣165 | 无额外推进；升级 `resource_quote:13`／`resource_net_change:-11` 原读数保留，资源时序原因未证，不把金币相等扩大为所有资源一致 |

T88 后验另存 `callaudit_20261031_t088_verification`，result119 确认 827,239 字节；pending110 全 false、transport null。后验中的多级 help、`pending show`、`save capture` 使用错误单列为验证操作成本，不计主局接口收益。首次非 PTY 验证进程在 0 次工具调用时 EOF，原件 `evidence/verification-ab02f53/` 保留，不计游戏测试或另一个原生控制器。

T60 有限支线从 reload122 开始，依据当时公开事实选择粮仓、伤兵治疗、显式续原队列、驻扎、学徒／封建、莫克夏和使者等动作，没有自动策略脚本。只有 result178、221、293、370 的 `60→61→62→63→64` 共 4 次推进；其余 end 调用仅确认原 turn。T63 来约 result328 已明确显示双方 `和解 for 9 turns`、瑞典支付 6 金币，raw subtype547027585 与期限9保留；before331／after342 精确金币 `225.34765625→231.34765625`，339单次接受，外交334 WAR→348 UNFRIENDLY，353关闭。议会359当前两个选择、364阶段完成、367 NONE后才确认原63→64；不把本地投票计划当最终胜负。pending372清空，另存377 `callaudit_20261031_t064_peace_verification` 765,407字节。

恢复 result382 从原 `callaudit_20261031_t100_final` 返回 T100，官方分数273→273且完整分类不变；随后 decision385、精确帝国388、队列391读回原终点身份和公开事实。另存396 `callaudit_20261031_t100_verified` 855,146字节；pending398全false／transport null，quit400无未决、正常关闭。使徒2097162的当前移动队列是普通策略意图，不是Runtime pending；没有 end100。分数相同和公开读回不能证明未公开状态／随机数完全一致。

[后验独立统计](../evidence/verification-closed-call-analysis.json) 为 151 条命令、126,436 正文字节、173.946831 秒 RPC，4 次额外推进、4 条错误命令、0 条明确 unknown；该独立输入未另划分阶段，分析器原样保留为 unclassified，全部不混入正常主局指标。

[最终机器核对](../evidence/verification-native-final.json) 确认 129 个 source／knowledge 文件仍与 ab02 一致；原主局 decision465 与恢复385的 identity／checks，以及 units、cities、threats、victory、notifications、blockers、outcome 七段正文逐字相同；精确帝国468=388，队列与外交／议会／信息行相同（忽略观测时长）。overview 仅同数量兵种的枚举顺序变化，按兵种／数量排序后全同；不是完整 decision 逐字相同或二进制世界相同。T57 preview 前后单位全文、精确帝国和队列特殊行一致的断言也通过。

后验中的 citystate 拼写错误、T61 `skip-remaining` 的 CANNOT_OPERATE 等另计操作成本；后者拒绝后先读当前战车393219为ready，再改fortify，没有重复skip。剩余未验证边界为真实小数收益场景、神学／AIR预估（当前不支持）、资源时序差异、未公开状态／RNG，以及整体决策质量和胜率。隔离证据已保全；app 的 `archive_worktree` 对准确 attached identityKey 两次均拒绝 `Another active task still uses this worktree.`。清理受 app 占用保护阻塞，未强删或接管其他任务，worktree 与证据保留。
