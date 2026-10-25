# CLI 命令手册

本页是项目日常调用的唯一命令目录，涵盖顶层命令、会话控制及 101 个领域子命令。`civ6` 提供离线诊断、验证、测试、日志查询，以及前台 `debug` 会话。`doctor`、`verify knowledge`、`test` 和 `logs` 不连接 FireTuner；`verify state` 明确访问当前真实游戏，`debug` 在收到查询或工具调用时访问游戏。`civ-mcp` 仍是游戏 MCP 服务入口。

## 安装与调用

在源码项目根目录运行 `uv sync --locked`，然后使用 `uv run --locked civ6 ...`。也可使用同一解释器运行 `python -m civ_mcp.cli ...`。CLI 从当前目录向上查找项目，再尝试安装它的源码目录；使用 `--project PATH` 明确指定另一检出。doctor、知识验证及领域测试会拒绝加载了另一检出代码的解释器。

日常操作按以下路径执行：一次性诊断／验证／日志走顶层命令，连续游戏操作先启动一个 `civ6 debug` 并持续使用其 stdin。已有游戏控制会话时，在原会话内执行命令；不要为每个动作重启 CLI，也不要另开内置 MCP 并发控制。未封装的公开工具仍用同一会话的 `tools NAME` 和 `call TOOL JSON_OBJECT` 调用。CLI 缺依赖或报错时按错误建议修复，不绕过 CLI 直接调用内部函数或 Lua。

| 公共参数 | 说明 |
| --- | --- |
| `--project PATH` | 指定源码检出；默认从 cwd 向上发现，再尝试安装源码位置。 |
| `--output PATH` | 新建证据目录；路径必须尚不存在，且不位于知识目录内。 |
| `--json` | 结果默认已是 JSON；与 `--help` 合用时返回机器可读帮助。 |
| `--help` | 显示当前层级帮助，不启动 MCP、不操作游戏。 |

默认知识目录是所选项目的 `civ6_knowledge`。doctor 和知识验证可用 `--knowledge-dir PATH` 指定完整知识包；未采集本机游戏来源，不使用 `CIV6_KNOWLEDGE_DIR` 的隐含值选择验证对象。

## 顶层命令

```sh
uv run --locked civ6 doctor
uv run --locked civ6 debug --offline
uv run --locked civ6 verify knowledge
uv run --locked civ6 verify state
uv run --locked civ6 test --list
uv run --locked civ6 test actions
uv run --locked civ6 test knowledge --collect-only
uv run --locked civ6 test turns --timeout 300 -- -k pending -x
uv run --locked civ6 logs list --limit 10
uv run --locked civ6 logs show --file .scratch/action-sample.jsonl --tool get_units
uv run --locked civ6 logs timeline --file .scratch/action-sample.jsonl --since 2026-10-24T00:00:00+08:00
uv run --locked civ6 logs turns --relay .scratch/example-relay
```

日志示例中的文件是调用方提供的已有动作 JSONL，不由 CLI 自动创建。`logs list` 返回的 `session` 可代替 `--file`；用 `--dir PATH` 指定日志目录。

| 命令 | 执行内容与结果范围 |
| --- | --- |
| `doctor` | 检查 Python、实际代码目录、运行依赖、开发依赖、知识包完整性、真实 stdio MCP 初始化及工具发现；不调用游戏工具。缺少 pytest 或 lupa 记 `NOT_RUN`，不阻止知识路径。 |
| `verify knowledge` | 使用当前知识包验证搜索「写作」及 `TECH_WRITING` 的摘要、规则、证据读取；比较 CLI 与真实 MCP 返回，保留静态适用范围，核对知识文件未变。 |
| `verify state` | 对已运行游戏执行有界只读验收，核对世界身份、玩家／回合、实体数量与范围、同一 MCP 进程和无未决上下文；不启动、加载或推进游戏。 |
| `debug [--offline] [--timeout SECONDS]` | 启动一个前台 MCP 会话；从 stdin 接收会话命令，持续保留 pending。`--offline` 拒绝真实游戏调用。 |
| `test --list` | 显示领域与测试文件，不运行测试。 |
| `test <领域>` | 只执行显式登记的 `tests/` 文件，pytest 原始退出码保留在 data.pytest_exit_code，CLI 自身使用下文统一退出码。`--collect-only` 只验证收集，`data.collection_only=true`，不代表测试用例已通过。 |
| `logs list` | 列出动作日志文件，按修改时间从新到旧排序；目录尚不存在时返回空列表。 |
| `logs show` | 保留文件顺序，返回最后 N 条匹配记录；按工具、结果子串、起止时间过滤。 |
| `logs timeline` | 返回时间最新的 N 条匹配记录，并按时间升序排列；不把日志文字解释为提交、确认或门禁状态。 |
| `logs turns --relay PATH` | 从已有前台转接器 `requests/` 凭据重建回合耗时，JSON 写入本次 CLI 证据目录；不连接游戏、不修改原始凭据。 |

### 顶层选项

| 命令 | 专用参数 |
| --- | --- |
| `doctor`、`verify knowledge` | `--knowledge-dir PATH`：完整知识包，默认 PROJECT/civ6_knowledge。 |
| `verify state` | `--expect-turn TURN`、`--expect-civilization CIVILIZATION_ID` 约束预期现场；`--timeout SECONDS` 为单次 RPC 预算，默认 300 秒。 |
| `debug` | `--offline` 禁止游戏调用；`--timeout SECONDS` 为单次 RPC 预算，默认 300 秒。 |
| `test` | DOMAIN 或 `--list`；`--collect-only` 只收集，`--timeout SECONDS` 为测试子进程预算（默认 300 秒）；pytest 选项放在 `--` 后。`--list` 不与领域或收集／运行选项组合。 |
| `logs list` | `--dir PATH` 指定动作日志目录；`--limit N` 默认 20，范围 1～1000。 |
| `logs show`、`logs timeline` | SESSION 或 `--file PATH` 二选一；`--dir PATH`、`--limit N`（默认 50）、`--tool NAME`、`--contains TEXT`、`--since TIME`、`--until TIME`。多个过滤条件按 AND 组合，contains 是 result 内的字面子串，时间含时区且边界包含。 |
| `logs turns` | `--relay PATH`：现有转接器证据目录；`--actors NAME ...`：限定参与者，默认排除 setup、repair、final、offline。 |

所有 timeout 必须是有限正数。日志默认目录为 `~/.civ6-mcp/actions/`，查询只读取现有文件，不跟随新日志。

领域登记在 `src/civ_mcp/devtools/testing.py`：`debug`、`knowledge`、`actions`、`turns`、`checkpoints`、`diplomacy`、`connection`。`civ6 test debug` 使用真实 stdio 和隔离的引擎替身验证会话，不访问游戏。这是定向回归集合，不承诺覆盖该领域所有行为；修改影响范围超出集合时按[开发与验证](development.md)追加直接相关测试。

pytest 透传放在 `--` 后，支持 `-q`、`-v`、`-vv`、`-x`、`-k EXPR`、`-m EXPR`、`--maxfail N`、`--maxfail=N`、`--collect-only`、`--disable-warnings`。额外路径、插件和配置覆盖会被拒绝。测试子进程不继承 `PYTEST_ADDOPTS`、`PYTEST_PLUGINS`，并关闭第三方插件自动加载，保持登记的离线选择；pytest 与项目 conftest 仍正常加载。

日志仅支持当前 `GameLogger` 的 `timestamp/tool/params/result` 行格式。时间过滤必须带时区，边界包含。解析失败行计入 `malformed_lines`，残缺末行标记 `tail_incomplete`；它们不进入结果集。单次查询读取打开文件时已有的字节，不跟随新日志；`source_sha256` 是实际读取字节的指纹。`--limit` 为 1～1000，截断时 `truncated=true`。

## 回合耗时统计

`civ6 debug` 自动在本次证据目录更新 `turn-timing.json` 和 `command-timing.json`，不需要额外开关。记录只消费已有命令与回应，不新增游戏查询、自动推进或后台轮询；统计失败不改变原命令结果和 pending。普通 CLI 标记 actor 为 `cli`；本机前台转接器还会在自己的证据目录按调用者区分 Codex、Claude 等模型。

| 字段 | 含义 |
| --- | --- |
| `wall_seconds` | 从上一次确认推进完成，到本回合确认推进完成的跨度；首个观测回合从首条计入命令开始。包含命令间等待。 |
| `cli_seconds` | 本回合逐条 CLI 命令执行时间之和，包含参数检查、预演和工具等待。 |
| `rpc_seconds` | 按日志时间戳计算显式 `tool_started` 至对应结果的跨度之和，包含验证内的多项查询；起始事件缺失或时间戳异常则为 JSON null，不补成零。内部纯内存状态检查计入 CLI 时间。 |
| `gap_seconds` | `wall_seconds - cli_seconds`，包含模型／服务、Shell、人工和交接等待，不能称为纯思考时间。 |
| `command_count`、`error_count`、`unknown_count` | 命令数、错误／阻塞返回次数、未知返回次数；不等于独立故障或重复提交次数。 |
| `status`、`clock_source` | `completed` 仅来自原 `end_turn` 已确认的推进回执；停止在当前回合记 `partial`。新记录使用单调时钟，旧记录回填使用墙钟并标记来源。 |

JSON 另按 actor 和命令汇总调用量、累计／最长耗时及错误数；逐命令 JSON 可定位具体参数。普通概览显示更高回合不会补造已完成回合；加载或世界身份变化会切分记录。无法关联的回应、缺失时间和时钟异常保留在 `issues`，不编造零耗时。未结束回合的跨度截至最后一条计入命令，不包含停止测试后的闲置时间。

转接器历史记录可用 `civ6 logs turns --relay PATH` 离线重建；普通 action JSONL 只有单点时间，不能据此还原每次调用耗时。当前完成边界对应普通单人 `end_turn`；热座专用席位交接尚未纳入本项验收。用户暂停若落在命令间隔内会进入 `gap_seconds`，当前没有自动识别或扣除暂停的机制。

## JSON 与退出码

一次性命令 stdout 只输出一个 JSON 对象；`debug` 输出逐行 JSON 事件，最后一行是会话摘要。默认即为 JSON，`--json` 可显式标注。`--help` 显示当前层级的用途、参数、示例和结果范围；加 `--json` 返回结构化帮助（`kind=help`），只列直接子命令。help 不查找项目、不创建证据、不启动 MCP。终端提示和 Ctrl+C 提示写 stderr；子进程原始 stderr 写入证据。

运行目录建立后，结果包含 `schema_version=1`、`command`、`run_id`、`mode=offline`、`status`、`exit_code`、起止时间、项目路径、Git 提交及脏状态、`data`、`errors` 和 `evidence`。`game_verification=not_run` 明确实机未测；命令成功只覆盖本命令实际执行的路径。输出及证据文件使用 UTF-8。错误对象含 `code`、`message`、`hint`、`details`；`hint` 指出应修改的参数或下一项检查，工具 schema 错误的 `details.issues` 给出字段路径、校验规则和原因。

普通 debug 会话使用 `mode=debug`、`game_verification=unverified`，不把用户调试当作实机验收。`debug --offline` 保持 `mode=offline`、`game_verification=not_run`。

## 组合与发现

根 help 只展示命令组；逐层使用 `civ6 logs --help`、`civ6 logs show --help`。机器可用 `civ6 logs show --help --json` 读取同一参数契约。会话内 `help` 返回主题，`help call` 或 `call --help` 展示调用语法，`tools NAME` 才展开该工具的完整 schema。

一次性命令可将 JSON 结果交给 `jq` 等工具；组合脚本应同时检查退出码和 `status`。debug 接收 stdin 中每行一条命令，返回 JSONL；调用参数是原始 JSON 对象，不经过 shell 展开。筛选 `tool_result` 时按 `request` 关联 `tool_started.sequence`，不要把最后的会话 `PASS` 当作每次工具成功。

```sh
uv run --locked civ6 test --list | jq '.data.domains'
uv run --locked civ6 logs list | jq '.data.sessions[].session'
uv run --locked civ6 debug --offline <<'COMMANDS'
help call
tools unit_action
call --dry-run unit_action {"unit_id":0,"action":"move","target_x":22,"target_y":9}
pending
quit
COMMANDS
```

## 自动实机只读验收

`civ6 verify state` 启动一个 MCP 会话，先检查其 pending，再查询概览和结构化 `get_game_snapshot`，最后核对服务身份及 pending，关闭自有 MCP。stdout 只输出一份 JSON；详细事件和原始响应保留在证据目录。可选 `--expect-turn`、`--expect-civilization` 约束预期现场，不匹配返回 `BLOCKED`，不自动换档。每个 RPC 的默认超时为 300 秒，可用 `--timeout` 调整。

快照直接返回原生数据，包含文明／种子／玩家／回合、两次概览、城市和地图内单位。读取前后核对世界和玩家回合，城市须匹配所有者和种子，城市数须与概览一致，实体 ID 须唯一。单位列表可能省略离开地图的间谍，所以只检查列表数不超过概览数，不把未列出的单位解释为不存在。它不是引擎的原子快照，也不证明迷雾外事实。

存在任一未决动作、推进、外交、议会、保存或加载时，结构化快照在游戏 I/O 前返回 `blocked`；自动验收不会借普通实体查询消费确认。读取失败、身份变化、字段未知或范围检查不全，均不能获得通过。`get_runtime_status` 仍仅表示本服务内存，不能检测另一个 MCP 进程的意图；运行前须明确唯一控制通道。

该命令使用 `mode=live`，成功时 `game_verification=passed_readonly`，未确认时为 `unconfirmed_readonly`。这只覆盖只读验收；实际动作、保存、加载和回合推进须分别验证。会话内也可运行 `verify state`，使用当前 MCP 和 pending 上下文，结果写入 `verification` 事件；前台会话最终的 `PASS` 仍只表示正常退出。

## 会话内领域子命令

以下命令输入到已启动的 `civ6 debug` 会话，不是在 shell 中执行 `civ6 unit ...`。带空格的参数加引号，参数不经过 shell 展开。先用 `help`、`help unit`、`unit --help` 逐层发现，再用 `unit move --help` 查看实参。

表中「变更」命令均先加 `--dry-run` 预演，核实目标后再明确执行原命令；参数仍由真实 MCP schema 和 Runtime 校验。「只读」不等同无确认影响：普通实体查询可能顺带确认原动作；`pending` 仅看内存，`state snapshot` 则遇任一 pending 就拒绝进入游戏 I/O。

### 单位

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `unit list` | `get_units` | 只读 |
| `unit move ID X Y` | `unit_action`，action=move | 变更 |
| `unit action ID ACTION [--x X --y Y] [--improvement TYPE]` | `unit_action` | 变更 |
| `unit attack ID X Y` | `unit_action`，action=attack | 变更 |
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

其他已有动作也有命名入口：`unit repair/remove-improvement/remove-feature/build-route/automate ID`、`unit activate/sacrifice-charges/spread-religion/launch-inquisition/condemn-heretic/remove-heresy ID`；带目标坐标的入口为 `unit trade-route/teleport/religious-attack ID X Y`。斜线表示分别可用的子命令，不能原样输入。完整参数用 `unit COMMAND --help` 查询；原有 `unit action` 保留。

所有变更支持 `--dry-run`，只核对 schema，不验证当前资源和目标资格。`delete` 永久删除单位，`corps`／`army` 会消耗一个原单位，执行前先预演并读取当前状态。`skip-remaining` 只处理当前就绪单位，保留既有站立指令。编队／合并结果未知时，保留同一会话与完整参数，通过原命令确认；不能再次选择别的单位绕过门禁。只读 `unit formations` 不提交或退役请求。合并后使用回执给出的存活单位 ID，不假设发起单位一定保留。

本轮编队能力已完成隔离原生 Lua／MCP 与离线 CLI 验证，未在真实对局执行合并；实际原生事件顺序、解锁与合并结果仍需具备条件的受信存档验收。实现与证据见 [单位 CLI 说明](specs/2026-10-25-unit-cli.md)。

### 城市

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `city list` | `get_cities` | 只读 |
| `city production ID` | `get_city_production` | 只读 |
| `city set-production ID TYPE NAME [--x X --y Y]` | `set_city_production` | 变更 |
| `city attack ID X Y` | `city_action`，action=attack | 变更 |
| `city district ID DISTRICT_TYPE` | `get_district_advisor` | 只读 |
| `city focus ID FOCUS` | `set_city_focus` | 变更 |
| `city buy ID TYPE NAME [--yield YIELD_TYPE]` | `purchase_item` | 变更 |

生产 `TYPE` 为 `UNIT`、`BUILDING`、`DISTRICT` 或 `PROJECT`，购买仅支持前两者。`YIELD_TYPE` 为 `YIELD_GOLD` 或 `YIELD_FAITH`，默认金币。`FOCUS` 为 `food`、`production`、`gold`、`science`、`culture`、`faith`、`default`，default 清空焦点。`--x` 和 `--y` 须成对提供；候选、费用、人口限制和建造资格仍以当前查询和原生引擎为准。

### 研究、回合与存档

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `research show` | `get_tech_civics` | 只读 |
| `research set ID [--category CATEGORY]` | `set_research` | 变更 |
| `turn status` | `get_turn_status` | 只读 |
| `turn end` | `end_turn` | 变更 |
| `save list` | `list_saves` | 只读 |
| `save create NAME` | `save_game` | 变更 |
| `save reload NAME` | `reload_game_save` | 变更 |
| `save load NAME` | `load_game_save` | 变更 |

`CATEGORY` 为 tech（默认）或 civic，研究 `ID` 为本次候选的原生类型。`save create` 可能覆盖同名普通存档，须使用独立名称；`save reload` 会丢失保存后现场，`save load` 仅从主菜单加载。文件系统存档回退当前只扫描 Single，不能据此声明 Hotseat 恢复可用。`turn end` 不替代双 AI 热座专用席位交接；热座沿用任务指定控制器及轮次记录，不并行另开 CLI 控制它。

### 地图、胜利与知识

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `map area X Y [--radius N]` | `get_map_area` | 只读 |
| `victory show` | `get_victory_progress` | 只读 |
| `knowledge search QUERY [--topic TOPIC] [--limit N]` | `search_knowledge` | 静态只读 |
| `knowledge show ID [--level LEVEL]` | `get_knowledge` | 静态只读 |

地图半径默认 2、范围 1～4，越界直接拒绝。知识搜索 limit 默认 5、范围 1～20；LEVEL 为 summary（默认）、rule 或 evidence。静态规则不证明本局资格；迷雾、未知和不存在仍须区分。知识查询也可在 `debug --offline` 内执行。

### 政策、城邦与总督

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `policy show` | `get_policies` | 只读 |
| `policy set SLOT=POLICY_ID [SLOT=POLICY_ID ...]` | `set_policies` | 变更 |
| `city-state list` | `get_city_states` | 只读 |
| `city-state envoy PLAYER_ID` | `send_envoy` | 变更 |
| `governor list` | `get_governors` | 只读 |
| `governor appoint TYPE` | `appoint_governor` | 变更 |
| `governor assign TYPE CITY_ID [--owner PLAYER_ID]` | `assign_governor` | 变更 |
| `governor promote TYPE PROMOTION_TYPE` | `promote_governor` | 变更 |

政策槽位可用空格或逗号分隔，NONE 显式清空，未指定槽位保持原值；格式错误和重复槽位在发送前拒绝。城邦 player ID、总督类型及晋升均先查询；owner 省略时取本地玩家，显式 owner 为 0～63。政策、使者、任命和晋升可能消耗金币、使者或总督头衔，实际资格由引擎决定。

### 议会与通知

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `congress show` | `get_world_congress` | 只读 |
| `notifications show` | `get_notifications` | 只读 |

议会查询不打开投票、不提交票单；通知查询不关闭弹窗或代答外交。未封装的票单、交易、宗教等公开工具按下节 `tools NAME` 与 `call` 执行，保持同一会话。机制及确认条件仍见[游戏操作](agent-playbook.md)、[特殊操作](agent-special-operations.md)和[保存恢复](agent-recovery.md)。

### 科技、市政和新增领域命令

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
| `great-person list` | 当前伟人、点数进度及金币／信仰赞助费用。 |
| `great-person recruit ID` | 用本类伟人点数领取。 |
| `great-person buy ID --currency gold\|faith` | 用明确选择的金币或信仰赞助；未知时原意图只确认，不重复付款。 |
| `great-person reject ID`、`great-person history [--player ID]` | 明确拒绝，或读取原生往期招募记录。历史未提供的付款方式／金额为 null，不从报价反推。 |
| `climate show`、`climate history [--since-turn T]` | 原生碳排放、温升、气候变化与全球／已揭示区域事件历史。累计 footprint 不是物理 ppm 浓度。 |
| `history show [--since-turn T]` | 本方原生历史时刻、回合、时代、时代得分和说明。 |
| `empire show`、`empire resources`、`empire cities`、`empire gossip` | 每回合产出及余额、资源库存、城市现状、原生可见小道消息。show 包含信仰每回合、外交支持和可派使者。 |
| `civilization list`、`civilization show PLAYER_ID` | 已相遇文明与领袖身份；show 联合展示其缓存能力规则，未知文明不可查询。 |
| `civilization abilities TYPE [--level rule]` | 按文明／领袖 ID 读取静态能力；可读静态规则不代表本局已遇到或能力已激活。 |
| `resources global [--player ID]` | 全球资源界面对应的自有库存和已相遇文明可交易资源；不返回隐藏库存或全图矿藏。 |
| `era show`、`era dedications` | 当前时代进度／得分／阈值，以及可选时代着力点。 |
| `ranking show` | 总分及科技、文化、统治、宗教、外交等启用路线的世界进度。 |
| `map search resource\|luxury\|strategic\|antiquity\|village [--query TEXT]` | 按类型、中文名或内部 ID 定位。非消耗资源限已揭示且本方可识别地块；村庄、古迹和可消耗加成资源要求当前可见。空结果不证明迷雾里不存在。 |

科技／市政搜索支持重复 `--filter`，可选 food／食物、science／科学、production／生产力、culture／文化值、gold／金币、faith／信仰值、housing／住房、unit／单位、improvement／改良设施、wonder／奇观；`--match any|all` 指定并集／交集，`--limit 1..100 --offset N` 分页。例如：

```text
technology search 写作 --filter 科学
technology search --filter 食物 --filter 住房 --match all
civic search --filter 生产力 --filter 单位 --match any --limit 10
```

匹配依据来自实际解锁关系、已捕获的正收益／住房及 modifier 关联，JSON 返回 `match_evidence` 与静态适用范围；条件、文明专属内容、DLC／模组及当前可研究资格均未由静态检索验证。活跃资格另用 status 读取，不能仅凭过滤结果提交研究。

历史、地图、全球资源、小道消息等列表支持 `--limit 1..200 --offset N`。原生字段不可读取时明确返回 partial／unknown 和 null，不补成零或空列表；CLI 会将显式 JSON 查询错误标记为错误，但仍保留原始完整 JSON。总督和城邦继续使用前述 `governor`、`city-state` 命令。

### 最小调用顺序

```text
state overview
unit list
knowledge search 写作 --limit 5
knowledge show TECH_WRITING --level rule
pending
quit
```

所有实体 ID、坐标和选择均取自本次查询；命令表只定义语法。历史频次与 CLI 化选择依据见[覆盖检查](acceptance/2026-10-24-cli-frequency.md)，不作为当前现场参数。

## 副作用预演

标准选项拼写为 `--dry-run`。可能改变游戏状态的调用先使用会话内 `call --dry-run TOOL JSON_OBJECT`。该入口只校验工具名称、严格 JSON 和真实输入 schema，返回 `event=dry_run`、原参数、`schema_valid=true`、`would_send=false`；未明确标为只读的工具按 `potentially_mutating=true` 展示。预演仍写本地证据。

工具预演不发送目标 RPC、不查询游戏、不确认或清除 pending，也不检查实体是否存在、坐标可达、当前门禁或游戏前提。`scope=schema_only`、`pending_checked=false` 明确此范围；`local_policy_allows_execution` 只表示当前 offline／传输未知限制，不能当作游戏执行许可。可在 `debug --offline` 中预演游戏工具，实际调用仍被拒绝。

`quit --abandon --dry-run` 只读取服务内存，返回 `would_exit`、`would_lose_context` 并保留会话。真正 `quit --abandon` 仍显式丢弃进程上下文；有未决时返回 `BLOCKED`。预演没有自动执行、确认令牌或自动重试；实际执行须明确输入不带 `--dry-run` 的命令。

## 前台 debug 会话

运行 `uv run --locked civ6 debug` 后，整个前台会话只启动一个 MCP 子进程、初始化一次、使用同一个 ClientSession。启动只发现工具并读取服务内存，不自动查询、启动或加载游戏。退出不调用 `kill_game`。该命令不会接管已有 Codex MCP 的进程或未决请求；实机调试前须明确交接唯一控制通道，不能同时由两者控制游戏。

会话控制命令如下；它们不是独立 shell 子命令：

| 会话命令 | 行为 |
| --- | --- |
| `help [TOPIC]`、`TOPIC --help` | 分层帮助，返回 JSON 事件。 |
| `tools [NAME]` | 列工具名，或读取一个工具的真实输入 schema。 |
| `pending` | 读取本 MCP 的内存 pending 与本地 transport_unknown。 |
| `state overview`、`state units`、`state cities`、`state turn`、`state snapshot` | 概览、单位、城市、回合与结构化快照。 |
| `verify state` | 在当前会话执行自动实机只读验收。 |
| `call [--dry-run] TOOL [JSON_OBJECT]` | 未封装工具的统一调用入口；省略 JSON 等同 {}。 |
| `quit [--abandon] [--dry-run]` | 正常退出、预览退出或显式放弃上下文。 |


`state` 是对应公开 MCP 查询的别名，`state snapshot` 返回结构化当前玩家快照。`call TOOL JSON_OBJECT` 只调用工具列表中实际存在的入口；省略参数等同 `{}`。JSON 不能有重复键、NaN 或 Infinity。CLI 按实际发现的 `inputSchema` 在发送前校验参数，MCP 仍执行服务端校验；动作确认与门禁仍由现有 Runtime 所有者处理；CLI 不调用内部 setter 或清理未决状态。使用实体 ID 和坐标前须先查询真实状态。

`pending` 调用 `get_runtime_status`：返回本服务的 UUID、PID、连接标记、动作摘要与动作／推进／外交／议会／保存／替换的未决事实。该工具只读取所有者内存，不查询游戏、确认请求、刷新缓存或清门禁。`connected=false` 不表示世界不存在，`has_pending=false` 不证明别的 MCP 进程没有未决请求。

服务 UUID／PID 标识本次 MCP 生命周期，不是世界身份或测试轮次。每次显式工具调用前也读取同一内存快照核对服务身份，不自动确认任何请求。

`--offline` 只允许发送 `get_runtime_status`、`search_knowledge`、`search_research`、`get_knowledge`，本地 `help/tools/quit` 和其他工具的 dry-run 也可使用。其他工具的实际调用在发送前拒绝，不能通过 `state` 或显式 `call` 绕过。离线检查可向会话输入知识查询和 pending，不必运行游戏。

会话异常与退出：

- Runtime 正常返回 `OUTCOME_UNKNOWN` 时，保持同一服务和原请求。先 `pending` 及只读核对，再按[恢复指南](agent-recovery.md)通过原入口确认；CLI 不自动重发。
- RPC 超时或丢回应时，额外保留本地 `transport_unknown`，后续只允许只读工具。空 pending、缓存刷新和后续读成功都不能清除这个标记；本版不自动恢复该次传输结果，也不重建 MCP 进程。
- `quit` 会新读 pending；有未决请求、传输未知或无法确认服务身份时拒绝退出，留在原会话。`quit --abandon` 是明确放弃当前进程内上下文的退出入口，仍返回 `BLOCKED`，不退役游戏中的原请求。保留证据后按已授权的检查点恢复流程处理，不能开一个空会话冒充已确认。
- 交互终端 Ctrl+D 有未决时返回提示；stdin 管道已耗尽时无法继续交互，保存未决快照、返回 `BLOCKED` 后关闭自有 MCP，`data.exit_reason=eof_unresolved`。该结束会丢失进程内上下文，不支持跨进程续接。
- Ctrl+C 不取消正在执行的 RPC，也不终止 MCP；等待请求返回后继续输入。每个 RPC 默认等待上限 300 秒，可用 `debug --timeout SECONDS` 调整。POSIX 子进程使用 SDK 的独立进程组；Windows 的实际控制台信号行为尚未实测。
- 服务 UUID／PID 变化会锁存失效并阻止后续变更。stdio 关闭时结束前台等待、保留失败证据；没有进程替换、自动恢复或重放。

`debug.jsonl` 记录命令、调用开始、原始返回、未知结果与退出原因；每条事件有递增序号，`tool_result.request` 对应 `tool_started.sequence`。完整 MCP 返回另存 `debug-response-*.json`。`runtime-status.json` 是最后一次成功读取的内存快照，`transport-unknown.json` 保留首个传输未知的原参数，后续读失败不会覆盖它。`debug-process.json`、`debug-initialize.json`、`debug-tools.json` 保存启动与协议事实。`data.tool_calls` 仅计显式调用和状态别名，不含内部的纯内存状态检查；日志查询的 session 文件名也不等于服务 UUID。

最后的 `event=session_closed` 摘要只判断会话是否正常结束；先前工具调用是否成功，应检查各自 `is_error` 和原始返回。`tool_result.is_error` 识别协议错误及服务显式的 `Error:`／`ERR:` 文本错误边界；`protocol_is_error` 保留原 MCP 标志，`error` 给出错误码、来源和修正建议，`result` 保留未改写回包。原生资格拒绝也应读取并处理；没有错误标志不等同动作已确认，CLI 不由其他展示文案推断成功或释放 pending。明确放弃或异常关闭时 `data.context_lost=true`；证据文件不是可恢复的意图存储。

| 退出码 | 状态 | 含义 |
| --- | --- | --- |
| `0` | `PASS` | 本次操作成功；收集、列目录和执行测试的范围见 `data` |
| `1` | `FAIL` | 观察到验证不通过、测试失败、超时或执行异常 |
| `2` | `BLOCKED` | 参数、依赖、路径、权限或测试选择不满足条件；没有测试命中也属此类 |
| `130` | `BLOCKED` | 用户中断；检查已有证据后再决定是否重跑 |

## 证据与清理

默认目录为 `.scratch/civ6/<run_id>/`；`--output PATH` 可指定一个尚不存在的目录。全局参数可放在子命令之前或最终子命令之后。现有目录不会被覆盖，知识目录不能作为证据输出位置。

- `summary.json` 在执行期间标记 `RUNNING`；结束后与 stdout 的 JSON 对象相同，包括失败原因和部分完成的数据。
- `context.json` 保存解释器、Git 完整状态和运行层源码 SHA-256；stdout 只引用该文件，避免重复输出大量上下文。
- `cli-*.json`、`pytest.json` 保存完整命令、工作目录、起止时间、退出码、stdout/stderr。超时保留已收到的输出。
- 知识验证保存 MCP 子进程命令与显式环境覆盖、初始化、工具列表、`mcp-attempts.json`、每次原始响应和 stderr；doctor 只初始化与发现工具，不产生工具调用记录。
- 日志查询保存 `logs.json`，包括源指纹、筛选结果和坏行数量，不修改源日志。

知识验证只关闭自己创建的 MCP 管道及子进程，删除自己的临时工作目录，保留证据。MCP 的标准工具日志仍追加到 `~/.civ6-mcp/actions/`；不会删除其他会话的记录。

参数无法解析、项目无法定位或证据目录不能创建时，仍输出错误 JSON，但 `evidence=null`；这时没有可写入的运行目录。证据可能含本机路径和原始日志，默认留在忽略目录，不自动提交或外发。

`$verify-civ6` 使用此 CLI 执行知识验证；旧的技能 helper 继续作为兼容入口。实机验收仍按[保存与恢复](agent-recovery.md)及当前任务授权单独执行。
