# 外交、生命周期与完整命名入口

[返回 CLI 索引](../cli.md)

以下命令均在当前 `civ6 debug` 的 stdin 输入。变更先追加 `--dry-run`，确认后显式执行；预演仍只检查 schema。参数详情通过 `COMMAND --help` 逐层发现，ID 和候选均来自当前查询。

| 命令 | 用途 |
| --- | --- |
| `session status`、`game snapshot` | 当前服务内存身份／未决事实；结构化当前玩家快照 |
| `game launch`、`game stop`、`game menu`、`game recover SAVE_NAME` | 显式启动、停止、返回原生主菜单（保留进程、不读档不存档）；按已确认检查点完整重启恢复 |
| `save load-index INDEX`、`save menu-load SAVE_NAME` | 按本次列表索引或明确文件名从原生菜单加载 |
| `map settle UNIT_ID`、`map settle-global`、`map strategic` | 当前开拓者、全局已知选址及战略地图 |
| `city wonder CITY_ID WONDER_TYPE`、`city tiles CITY_ID` | 奇观候选与可购买地块 |
| `city buy-tile CITY_ID X Y`、`city decision CITY_ID ACTION` | 金币买地；明确保留、拒绝、夷平或解放待决城市 |
| `great-person advisor UNIT_INDEX` | 当前伟人单位的原生激活位置建议 |
| `era choose INDEX` | 选择 `era dedications` 返回的当前索引 |
| `religion spread`、`religion found TYPE FOLLOWER_BELIEF FOUNDER_BELIEF` | 已相遇主要文明中本方或已揭示城市的宗教分布（无城邦／压力，时效未验证）；按已选原生信条创立宗教 |
| `diplomacy list`、`diplomacy pending` | 已相遇文明关系、当前外交语句及合法回应 |
| `diplomacy respond PLAYER_ID RESPONSE`、`diplomacy close PLAYER_ID` | 明确回应当前选项；取消已核验参与者的会话 |
| `diplomacy action PLAYER_ID ACTION`、`diplomacy peace PLAYER_ID` | 代表团、友谊、谴责、宣战等原生行动；提议和平 |
| `diplomacy alliance PLAYER_ID --type RESEARCH` | 按当前资格选择联盟类型 |
| `trade pending`、`trade options PLAYER_ID` | 来约条款与 fingerprint；当前可交易内容／关系资格 |
| `trade respond PLAYER_ID --accept` 或 `--reject` | 明确接受／拒绝当前已核验条款；两选一必填 |
| `trade negotiate PLAYER_ID --fingerprint VALUE [--our-gold N] [--their-gpt N] [--mode inspect\|equalize]` | 编辑已有金币条款并原生试价／询问反提案，不成交 |
| `trade propose PLAYER_ID [--offer-gold N] [--request-gold-per-turn N] [--mode test\|send]` | 显式交易预览或发送；全部物品选项见 `trade propose --help` |
| `trade routes`、`trade destinations UNIT_ID` | 商路容量、活动路线、指定商人的合法目的地 |
| `spy list`、`spy action UNIT_ID ACTION X Y` | 当前间谍；明确任务、目标或逃离路线 |
| `congress open`、`congress votes 'JSON_ARRAY'` | 显式进入已核实投票阶段；完整票单登记／提交 |
| `popup dismiss` | 关闭已核实的信息弹窗或结束后残留视图，不代答外交 |

`pending` 的 `popup` 字段表示通用弹窗写入批次或信息界面关闭结果未知。此时重复 `popup dismiss` 只返回保留的原请求，不重发关闭；普通变更、推进、保存与退出受门禁保护。旧通用批次没有可追认的逐项原生回执，不能靠界面消失或缓存刷新释放门禁；需要当前任务明确授权的受保护检查点恢复。已有专属回执的外交视图关闭继续沿原外交确认通道处理。

`trade propose` 的 `--offer/request-open-borders` 是明确布尔开关，金币／GPT／外交支持及资源各有独立选项。试价可能进入原生会话，仍按变更预演并确认；`--mode test` 与 `--dry-run` 含义不同。JSON 票单用单引号包住完整数组，例如 `congress votes '[{"hash":42,"option":1,"target":0,"votes":1}]' --dry-run`；标识和选项必须替换为本次查询事实。

