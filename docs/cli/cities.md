# 城市

[返回 CLI 索引](../cli.md)

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `city list` | `get_cities` | 只读 |
| `city yields [ID] [--view brief\|structured\|evidence]` | `get_city_yields` | 只读 |
| `city growth [ID] [--view brief\|structured\|evidence]` | `get_city_growth` | 只读 |
| `city workforce ID [--view brief\|structured\|evidence]` | `get_city_workforce` | 只读 |
| `city production ID [--item-type UNIT\|BUILDING --item NAME] [--view brief\|structured]` | `get_city_production` | 只读 |
| `city set-production ID TYPE NAME [--x X --y Y]` | `set_city_production` | 变更 |
| `city attack ID X Y` | `city_action`，action=attack | 变更 |
| `city district ID DISTRICT_TYPE` | `get_district_advisor` | 只读 |
| `city focus ID FOCUS` | `set_city_focus` | 变更 |
| `city buy ID TYPE NAME [--yield YIELD_TYPE]` | `purchase_item` | 变更 |

`city list` 与 `state decision` 的城市段在原同次 InGame 查询中提供每座本方城市的 `Religion` 多数宗教 ID／原生名称。`-1` 是已知 none，unknown 不能当作没有宗教；名称读取失败也不覆盖已知 ID。该观察绑定本次城市身份，不保证原子快照或其他上下文的新鲜度。玩家多数宗教和全局信教城市数不能替代逐城归属；压力、流入和可见宗教单位仍按需要用 `religion show` 读取。

生产 `TYPE` 为 `UNIT`、`BUILDING`、`DISTRICT` 或 `PROJECT`，购买仅支持前两者。`YIELD_TYPE` 为 `YIELD_GOLD` 或 `YIELD_FAITH`，默认金币。`FOCUS` 为 `food`、`production`、`gold`、`science`、`culture`、`faith`、`default`，default 清空焦点。`--x` 和 `--y` 须成对提供；候选、费用、人口限制和建造资格仍以当前查询和原生引擎为准。

`city production` 将生产与购买候选合并，分别列出金币／信仰的当前价格、余额、能否购买及拒绝原因。只能购买的宗教单位不再显示生产回合；币种未启用时显示 `CURRENCY_NOT_SUPPORTED`，不展示通用换算价。余额不足显示缺口，缺前置、同类单位占位和容量等保留原生原因；无法核实的价格或资格显示 unknown／unavailable。

默认 `--view brief` 显示可生产候选的原生当前生产成本、已投入生产力和预计回合；静态 `base_cost` 已知且与当前成本不同时另列，不能替代读取失败的当前成本。原生零成本和零回合保留为零，读取失败保持 unknown。`--view structured` 返回完整 `options`，包含 `cost`、`production_progress`、`base_cost`、`turns` 和各币种购买事实；这些数值口径也适用于区域、项目和维修候选。

要核对未出现在列表中的物品，用 `city production ID --item-type UNIT --item UNIT_APOSTLE`，两个筛选参数成对提供。报价是本次观察，实际 `city buy` 会重新检查价格、资格和余额；成功仍需新产物与精确扣款同时确认。`game decision --city ID` 使用同一报价与展示。

`city yields` 一次读取全部自有城市；提供本次查询所得 ID 时只读指定城市。它分别返回已放置区域的原生区域相邻值、地块相邻值与区域产出、完成／受损状态、每座建筑的六种原生收益，以及理性主义的解锁／可装槽／已装备状态。未完成区域也可能返回非零相邻值，不能当成已生效产出。缺席、零值、接口未知分别保留。

简报中的 `B1`／`B2` 只是在本次响应内引用完整修正 ID 的别名。逐项相等的地块相邻／区域产出写为 `=district_adj`；有差异或未知值时按原口径保留，完整展开始终可用。学院建筑收益来自当前 `GetBuildingYield`，不能用建筑清单或全城科技乘政策百分比代替。

默认 `brief` 将当前 `GameInfo` 的两条条件链共享展示一次，逐城对照人口与地块相邻门槛；区域相邻值另列，不能把翻倍后的 4 直接当作地块相邻达到 4。政策已装备时，同一命名查询继续读 GameCore 的修正实例、主体集合与 RequirementSet 状态，再回读世界范围。未装备时只需一次 InGame 读取；已装备时为三次原生读取，模型仍只调用一个命令。

`structured` 保留完整当前标量规则行、六种收益、原生条件及实例身份，`evidence` 另读城市科技 tooltip。人口／地块门槛比较是推断，`native condition` 与 `native effect` 来自已绑定自有城市的原生状态，不表示科技净增量。身份绑定要求引擎对象诊断中的精确城市 ID、所有者、当前城市对象及原始名称全部一致；不按城市名称猜测。状态冲突、绑定失败、反向／额外／不支持的条件或原生集合不完整时保留 unknown，完整展开继续可用。

该入口不缓存、不修改政策或城市，任一 pending 在游戏 I/O 前返回 `blocked`；身份／回合变化、实体扫描或原生资料不完整时返回 `unknown`。查询数和读取量应针对同一个决策问题比较，不能用新增事实的完整展开与缺少这些事实的旧报告宣称全量等价。

`city district` 只读取原生 `PLOTS` 中的全部可用落点及地块相邻收益，不使用手写相邻公式或固定 Top-N；保留分数、负值和未知。`city wonder` 同样排除 `BLOCKED_PLOTS` 等其他数组，展示已知资源／设施的占地影响，不按固定权重推荐落点。城市攻击提示要求原生目标标志、当前命令资格、可见与交战关系；没有原生目标不能单独诊断为已开火或视线受阻。

`city tiles` 返回全部原生购地候选，分别列出实际金币报价、余额、当前资格及拒绝原因，包含原生合法的零价候选。报价与执行前检查共享纯事实读取；执行仍重新核对原生候选、资格、归属和精确扣款。

`unit tasks` 保留全部已识别的地形／资源／维修提示，显示原生建造者就绪状态与科技锁定原因。地形／资源映射仍是建议，不证明远处地块可改良；几何距离不等于路径或可达性。查询不调用会破坏引擎状态的远程操作资格接口。

`map settle` 只扫描所请求的本地可见范围，不再自动另查全球。`map settle-global` 分页返回已揭示土地的地形候选，排除已确认有主的中心，保留当前归属未知的中心；支持显式中心和半径，不使用固定收益权重或隐藏城市人口估算忠诚度。忠诚度来自原生开拓者警告；没有警告记录表示未知，不补成零。外国城市距离排除只使用当前可见城市；防御地形值只是已揭示丘陵／河流的几何汇总，不是原生战斗加成。这些地形候选尚未通过原生建城资格验证。路径缺失或不一致显示 unknown；未来回合数保留明确的启发式标识。

选址的当前粮食、生产力和资源只读取当前可见地块。粮食／生产力总量仅在半径 3 内已揭示地块全部取得对应观测时给出，否则为 unknown；已观测小计、各项观测地块数、当前可见数和已揭示范围地块数分别保留。资源清单不完整时明确标注，不能把迷雾内未读取的动态收益、资源或中心归属当成零、空集或无主；这些地块汇总也不等于建城后可工作收益或城市净收益。

`city growth [CITY_ID] [--view brief|structured|evidence]` 按需读取本方城市的原生增长／饥饿回合、粮仓、粮食盈余、住房和宜居度需求，以及总体、住房、忠诚度、占领、幸福度和其他增长修正。幸福度用百分比，其他增长用加法分数，其余指定值用乘数，不能统一相乘。幸福度的非食物产出修正也单独保留，不能当成全城所有产出修正。

食物消耗为原生食物产出减盈余；有效增长粮食使用原生总体增长乘数，口径与 UI 进度条一致，亏粮时不套用正增长修正。这些派生值不覆盖原生预计回合；`-1` 表示当前没有该增长／饥饿预期，`null` 才表示未知。`evidence` 增加六种原生收益 tooltip 和住房／宜居度建议。查询使用一次 InGame 读取，先拒绝全部 pending；身份／回合或城市范围不符时不交付混合城市事实，不修改市民、政策或优先级。

`city workforce CITY_ID` 必须指定本次查询所得的自有城市 ID，默认 `--view brief`。它配对读取原生 `MANAGE` 的地块、市民数、容量和锁定数，另列是否正在工作、是否属于本城购地集合、六种当前地块收益及城市产出偏好。可管理地块由原生数组确定，不能由几何半径、购地集合或人口数推算；地块收益也不表示边际城市净收益或专家收益。`structured` 保留完整字段及零收益，`evidence` 另附本城购地 ID 和原生数组键。先检查 `status`、`scan_complete`、`purchased_plot_scan_complete` 和 `unknown_fields`；默认简报在正文保留相应的不完整提示。分项读取失败保留未知，数组不配对或城市／世界身份变化返回 `unknown`，任一 pending 在游戏 I/O 前返回 `blocked`；查询不分配或锁定市民，不改变焦点或界面。

`evidence` 还提供 MANAGE 参数与列结构诊断。`MAX_CITIZENS` 是原生独立读数，不能当成所有地块市民数的上限；城市中心可合法返回工作人数 1、容量 0。简报逐地块保留当前可见性。增长的 `evidence` 另附住房与宜居度原生来源／损失分项，保留原始符号和未知值，不强求分项之和等于总量；默认增长视图不增加这些读取。

`map settle-global [--center-x X --center-y Y --radius R] [--limit N --offset N]` 的范围参数须同时提供，半径为 0～20；默认每页 20，最大 200。响应提供范围内总数、展示量、遗漏量和下一页 offset，按地图扫描顺序返回，页间不评分排序。中心与半径在地形候选计算前筛选；分页在同一次完整候选读取后切片，因此主要减少模型读取正文，不宣称降低引擎全图扫描成本。每次查询读取当前状态，不是冻结快照；若局面变化，重新核对各页，不拼接成同一局面。空页不等于总量为零。

`empire resources` 与资源搜索区分资源目击、归属和设施状态。外国地块的当前归属、设施与受损详情仅在当前可见时显示，迷雾内保持未知；附近资源可属于外国，不自动称为无主。设施存在或未受损不证明已向库存供给，也不证明当前可以买地或开发。城市未改良资源提示保留“资格未验证”；古迹／沉船另列为考古资源，不进入建造者改良任务。工作单位类型是资源分类提示，实际执行仍需原生资格。

