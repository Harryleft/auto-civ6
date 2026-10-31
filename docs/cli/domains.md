# 科技、市政和新增领域命令

[返回 CLI 索引](../cli.md)

所有命令均输入到同一个 debug 会话；变更仍需先 `--dry-run`，原生资格和结果确认由 Runtime 负责。

| 命令 | 用途 |
| --- | --- |
| `technology status`、`civic status` | 当前科技／市政进度与可选目标；沿用原生实时查询。 |
| `technology set TYPE`、`civic set TYPE` | 明确选择当前允许的科研／市政目标。 |
| `technology search [QUERY]`、`civic search [QUERY]` | 在已校验指纹的知识快照中检索中英文名称、ID、说明及解锁内容。 |
| `government show`、`government list` | 当前政体／政策槽，以及本局已解锁政体、槽位和原生更换资格。 |
| `government change TYPE` | 明确更换已解锁政体；不代替政策选择。 |
| `religion show`、`religion pantheon`、`religion beliefs` | 宗教压力／多数派、万神殿加成和宗教信条候选。 |
| `religion choose-pantheon BELIEF_TYPE` | 选择万神殿加成；需要原生信仰与资格，不是修建一座建筑。 |
| `great-person list [--individual ID] [--class CLASS_TYPE]` | 当前原生时间线中的伟人、点数进度及金币／信仰赞助费用；两个筛选条件按 AND 组合。 |
| `great-person recruit ID` | 用本类伟人点数领取。 |
| `great-person buy ID --currency gold\|faith` | 用明确选择的金币或信仰赞助；未知时原意图只确认，不重复付款。 |
| `great-person reject ID`、`great-person history [--player ID]` | 明确拒绝，或读取原生往期招募记录。历史未提供的付款方式／金额为 null，不从报价反推。 |
| `climate show`、`climate history [--since-turn T]` | 原生碳排放、温升、气候变化与全球／已揭示区域事件历史。累计 footprint 不是物理 ppm 浓度。 |
| `history show [--since-turn T]` | 本方原生历史时刻、回合、时代、时代得分和说明。 |
| `empire show`、`empire resources`、`empire cities`、`empire gossip` | 每回合产出及余额、资源库存、城市现状、原生可见小道消息。show 包含信仰每回合、外交支持和可派使者。 |
| `civilization list`、`civilization show PLAYER_ID` | 已相遇文明与领袖身份；show 联合展示其缓存能力规则，未知文明不可查询。 |
| `civilization abilities TYPE [--level brief]` | 按文明／领袖 ID 读取静态能力；可读静态规则不代表本局已遇到或能力已激活。 |
| `resources global [--player ID]` | 全球资源界面对应的自有库存和已相遇文明可交易资源；不返回隐藏库存或全图矿藏。 |
| `era show`、`era dedications` | 当前时代进度／得分／阈值，以及可选时代着力点。 |
| `ranking show` | 总分及科技、文化、统治、宗教、外交等启用路线的世界进度。 |
| `map search resource\|luxury\|strategic\|antiquity\|village [--query TEXT] [--limit N --offset N] [--view brief\|structured]` | 按类型、中文名或内部 ID 定位，默认 brief。非消耗资源限已揭示且本方可识别地块；村庄、古迹和可消耗加成资源要求当前可见。空结果不证明迷雾里不存在。 |

政体窗口、逐目标资格和保留当前的 ACK 统一见[政体说明](government.md#政体)。

伟人列表使用原生时间线的使用位置、主动效果、被动条件及定义中的次数，分别保留金币／信仰的报价、余额和赞助资格。一个币种接口失败不会覆盖另一个币种；报价不可用、原生不允许与余额不足分别展示。`great-person advisor` 使用 `unit list` 的 `idx`，原生高亮表示可激活地点，不保证单位现在已经就绪，也不表示城市总产出就是激活收益。

伟人筛选使用原生 `individual_id`（非负整数，0 有效）和 `GREAT_PERSON_CLASS_*` 类型，不能使用翻译后的类别名称。读取完整时间线并绑定身份后，才对匹配者读取能力、点数与报价；结果报告原生总数、匹配数、展示数和扫描完整性。只有完整扫描才能确认当前没有匹配者；缺行、身份绑定失败和不完整读取保留未知。原生耗尽类别单列，不合成候选伟人；不带筛选可恢复完整列表。同一次响应内完全相同且已知的余额只展示一次，变化或未知余额逐项保留。

`map search` 的默认 `brief` 将共同的范围说明展示一次，逐地点保留坐标、资源身份、当前可见性、归属、设施、受损及工作单位类型；`?` 表示未知，工作单位类型不证明当前可以执行。`--view structured` 保留完整原生报告和逐行范围字段；内部 `get_public_report` 的默认视图仍为 `structured`。分页默认 `limit=50`、`offset=0`，limit 范围为 1～200。`count_complete=true` 才表示匹配计数完整；否则 `total`、`truncated` 为 null，`observed_total` 仅表示本次已观测匹配数，不能用空页证明不存在。两种视图均保留身份、状态、分页和 `unknown_fields`，每次查询重读当前事实。

科技／市政搜索支持重复 `--filter`，可选 food／食物、science／科学、production／生产力、culture／文化值、gold／金币、faith／信仰值、housing／住房、unit／单位、improvement／改良设施、wonder／奇观；`--match any|all` 指定并集／交集，`--limit 1..100 --offset N` 分页。例如：

```text
technology search 写作 --filter 科学
technology search --filter 食物 --filter 住房 --match all
civic search --filter 生产力 --filter 单位 --match any --limit 10
```

匹配依据来自实际解锁关系、已捕获的正收益／住房及 modifier 关联，JSON 返回 `match_evidence` 与静态适用范围；条件、文明专属内容、DLC／模组及当前可研究资格均未由静态检索验证。活跃资格另用 status 读取，不能仅凭过滤结果提交研究。

历史、地图、全球资源、小道消息等列表支持 `--limit 1..200 --offset N`。原生字段不可读取时明确返回 partial／unknown 和 null，不补成零或空列表；CLI 会将显式 JSON 查询错误标记为错误，但仍保留原始完整 JSON。总督和城邦继续使用前述 `governor`、`city-state` 命令。

