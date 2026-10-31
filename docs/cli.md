# CLI 命令手册

本页是 AI 唯一操作入口的命令目录，涵盖顶层命令、会话控制及 148 个命名子命令（含 3 个既有双人热座实验命令）。95 个基础 MCP 工具均有命名入口，其中任意 Lua 只在显式开发模式开放。`doctor`、`verify knowledge`、`test` 和 `logs` 不连接 FireTuner；`verify state` 明确访问当前真实游戏，`debug` 在收到查询或动作时访问游戏。`civ-mcp` 保留为 CLI 内部服务，客户端不直接注册。

## 安装与调用

在源码项目根目录运行 `uv sync --locked`，然后使用 `uv run --locked civ6 ...`。也可使用同一解释器运行 `python -m civ_mcp.cli ...`。CLI 从当前目录向上查找项目，再尝试安装它的源码目录；使用 `--project PATH` 明确指定另一检出。doctor、知识验证及领域测试会拒绝加载了另一检出代码的解释器。

日常操作按以下路径执行：一次性诊断／验证／日志走顶层命令，连续游戏操作先启动一个 `civ6 debug` 并持续使用其 stdin。已有游戏控制会话时，在原会话内执行命名命令；不要为每个动作重启 CLI。`tools NAME` 和 `call TOOL JSON_OBJECT` 只作为同会话协议发现与兼容诊断。CLI 缺依赖或报错时按错误建议修复，不另开 MCP 客户端或直接调用内部函数。

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
uv run --locked civ6 tuner status
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
| `tuner status` | 只读诊断：探测 FireTuner 端口状态，列出端口占用者（`lsof`）和本地 CLI／MCP 进程（`ps`），给出 `occupied`／`available`／`available_with_stale_processes`／`stale_session_likely`／`game_offline` 判定与处置提示；不发送游戏命令、不占用客户端连接。 |
| `verify knowledge` | 使用当前知识包验证搜索「写作」及 `TECH_WRITING` 的摘要、规则、证据读取；比较 CLI 与真实 MCP 返回，保留静态适用范围，核对知识文件未变。 |
| `verify state` | 对已运行游戏执行有界只读验收，核对世界身份、玩家／回合、实体数量与范围、同一 MCP 进程和无未决上下文；不启动、加载或推进游戏。 |
| `debug [--offline] [--timeout SECONDS]` | 启动一个前台 CLI 会话及其内部 MCP；从 stdin 接收命令，持续保留 pending。`--offline` 拒绝真实游戏调用。开发／热座选项见下文。 |
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
| `debug --compact` | stdout 只保留一份文本：当 MCP 的 `structuredContent` 仅含与唯一 text 块完全相同的 `result` 时，省略该重复字段；其他结构化字段、错误和多媒体照常返回。原始完整响应保留在 `debug-response-*.json`。默认输出契约不变。 |
| `tuner status` | 无专用参数；输出固定结构 JSON，证据写入本次 CLI 目录。 |
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

JSON 另按 actor 和命令汇总调用量、累计／最长耗时及错误数；逐命令 JSON 可定位具体参数。合法的 `get_decision_context` 简报或结构化回应也可作为回合锚点：要求 schema 1、ready、完整通过的范围检查和有效世界身份；多份内容身份冲突时拒绝锚定并记入 `issues`。无合法锚点的原生确认推进保留在 `unattributed_confirmed_advance`（`from`／`to`），质量标记为 partial，不虚构耗时跨度或已完成回合。普通概览显示更高回合不会补造已完成回合；加载或世界身份变化会切分记录。无法关联的回应、缺失时间和时钟异常保留在 `issues`，不编造零耗时。未结束回合的跨度截至最后一条计入命令，不包含停止测试后的闲置时间。

转接器历史记录可用 `civ6 logs turns --relay PATH` 离线重建；普通 action JSONL 只有单点时间，不能据此还原每次调用耗时。当前完成边界对应普通单人 `end_turn`；热座专用席位交接尚未纳入本项验收。用户暂停若落在命令间隔内会进入 `gap_seconds`，当前没有自动识别或扣除暂停的机制。

## JSON 与退出码

一次性命令 stdout 只输出一个 JSON 对象；`debug` 输出逐行 JSON 事件，最后一行是会话摘要。默认即为 JSON，`--json` 可显式标注。`--help` 显示当前层级的用途、参数、示例和结果范围；加 `--json` 返回结构化帮助（`kind=help`），只列直接子命令。help 不查找项目、不创建证据、不启动 MCP。终端提示和 Ctrl+C 提示写 stderr；子进程原始 stderr 写入证据。

运行目录建立后，结果包含 `schema_version=1`、`command`、`run_id`、`mode=offline`、`status`、`exit_code`、起止时间、项目路径、Git 提交及脏状态、`data`、`errors` 和 `evidence`。`game_verification=not_run` 明确实机未测；命令成功只覆盖本命令实际执行的路径。输出及证据文件使用 UTF-8。错误对象含 `code`、`message`、`hint`、`details`；`hint` 指出应修改的参数或下一项检查，工具 schema 错误的 `details.issues` 给出字段路径、校验规则和原因。

普通 debug 会话使用 `mode=debug`、`game_verification=unverified`，不把用户调试当作实机验收。`debug --offline` 保持 `mode=offline`、`game_verification=not_run`。

## 组合与发现

根 help 只展示命令组；逐层使用 `civ6 logs --help`、`civ6 logs show --help`。机器可用 `civ6 logs show --help --json` 读取同一参数契约。会话内 `help` 返回命名主题，`help unit`、`unit --help` 或 `unit move --help` 逐层查看命令及参数。只有协议兼容诊断才使用 `help call` 或 `tools NAME` 查看内部工具 schema。

一次性命令可将 JSON 结果交给 `jq` 等工具；组合脚本应同时检查退出码和 `status`。debug 接收 stdin 中每行一条命令，返回 JSONL；调用参数是原始 JSON 对象，不经过 shell 展开。筛选 `tool_result` 时按 `request` 关联 `tool_started.sequence`，不要把最后的会话 `PASS` 当作每次工具成功。

```sh
uv run --locked civ6 test --list | jq '.data.domains'
uv run --locked civ6 logs list | jq '.data.sessions[].session'
uv run --locked civ6 debug --offline <<'COMMANDS'
help unit
unit move --help
unit move 0 22 9 --dry-run
pending
quit
COMMANDS
```

## 决策事实查询

连续决策可使用 `civ6 debug --compact`，在同一会话执行 `state decision` 或 `game decision`。该入口将现有领域 getter 合成一次有界 InGame 请求，返回本方概览、地图内单位、自有城市、当前可见威胁、公开胜利进度、通知、通知阻塞和原生终局。每部分保留原解析器和可见性规则，不生成策略、目标排序或动作。

```text
state decision
game decision --include research diplomacy trade
game decision --city 65536 --include research
game decision --view structured
```

`--city` 的 ID 必须来自本次真实查询，示例值不能直接照用。`--include` 只补充明确要求的领域；`--city` 补充指定自有城市的原生生产候选。默认 `--view brief` 复用现有领域报告；`structured` 返回解析后的数据，体积通常更大。未请求的领域不出现在 `sections` 中，不表示其内容为空。地图、规则、资源库存、政策、外交会话及议会详情仍按决策需要通过对应命令补查。

先检查顶层 `status`、`identity`、`checks` 和各 section 的 `status`。读取前后核对文明、种子、玩家和回合，并核对实体数量、城市归属及胜利数据范围。任一未决请求在游戏 I/O 前返回 `blocked`；分项异常、缺少完整回执或范围不一致返回 `unknown`，保留其他已读事实。`ready` 表示本次约定的读取与范围检查通过，字段内原有的未知、不可见和原生统计冲突仍需保留。读取不是引擎原子快照，也不缓存或接管动作确认。

`blockers` 仅为通知阻塞；空列表不证明所有推进前提满足。`turn end` 仍执行原有就绪、外交、议会和单次提交检查。需要诊断等待阶段或确认旧请求时使用 `turn status` 及原入口；`verify state` 继续使用独立的验收快照。

## 自动实机只读验收

`civ6 verify state` 启动一个 MCP 会话，先检查其 pending，再查询概览和结构化 `get_game_snapshot`，最后核对服务身份及 pending，关闭自有 MCP。stdout 只输出一份 JSON；详细事件和原始响应保留在证据目录。可选 `--expect-turn`、`--expect-civilization` 约束预期现场，不匹配返回 `BLOCKED`，不自动换档。每个 RPC 的默认超时为 300 秒，可用 `--timeout` 调整。

快照直接返回原生数据，包含文明／种子／玩家／回合、两次概览、城市和地图内单位。读取前后核对世界和玩家回合，城市须匹配所有者和种子，城市数须与概览一致，实体 ID 须唯一。单位列表可能省略离开地图的间谍，所以只检查列表数不超过概览数，不把未列出的单位解释为不存在。它不是引擎的原子快照，也不证明迷雾外事实。

存在任一未决动作、推进、外交、议会、保存或加载时，结构化快照在游戏 I/O 前返回 `blocked`；自动验收不会借普通实体查询消费确认。读取失败、身份变化、字段未知或范围检查不全，均不能获得通过。`get_runtime_status` 仍仅表示本服务内存，不能检测另一个 MCP 进程的意图；运行前须明确唯一控制通道。

该命令使用 `mode=live`，成功时 `game_verification=passed_readonly`，未确认时为 `unconfirmed_readonly`。这只覆盖只读验收；实际动作、保存、加载和回合推进须分别验证。会话内也可运行 `verify state`，使用当前 MCP 和 pending 上下文，结果写入 `verification` 事件；前台会话最终的 `PASS` 仍只表示正常退出。

## 会话内领域子命令

以下命令输入到已启动的 `civ6 debug` 会话，不是在 shell 中执行 `civ6 unit ...`。带空格的参数加引号，参数不经过 shell 展开。先用 `help`、`help unit`、`unit --help` 逐层发现，再用 `unit move --help` 查看实参。

表中「变更」命令均先加 `--dry-run` 预演，核实目标后再明确执行原命令；参数仍由真实 MCP schema 和 Runtime 校验。「只读」不等同无确认影响：普通实体查询可能顺带确认原动作；`pending` 仅看内存，`state snapshot` 则遇任一 pending 就拒绝进入游戏 I/O。

占城结果未知时，可在原会话执行 `unit list` 或 `city list`，由原 Runtime 所有者只读确认既有请求，再用 `pending` 核对门禁；不需要再次输入攻击命令。实体查询正文仍报告实际单位位置和城市状态。若此次查询确认了原请求，结果末尾追加 `Action confirmation:` 及原动作回执，仅显示一次；后续查询不重放，仍未决时不显示完成回执。攻城完成回执的可选 `completion:terminal_conquest` 表示本次近战占领已触发本方原生征服胜利，但攻击者仍在目标相邻格，不表示单位已进入城市。该例外仍要求原战斗、范围、城市归属和原生获胜队伍等证据完整；普通占领继续等待单位实际进城。

磁盘补丁不会替换存活服务内已冻结的确认查询。保留旧 owner、终局和未决请求时，实体查询只会运行该进程已有的确认器；不能把新版本的离线通过或另一个会话的空 pending 当作旧请求已确认。

### 单位

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `unit list` | `get_units` | 只读 |
| `unit move ID X Y` | `unit_action`，action=move | 变更 |
| `unit rebase ID X Y` | `unit_action`，action=rebase | 变更：飞机转场，原生核对基地／航母的范围、准入与容量 |
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

攻城使用 `unit attack ID X Y`。可见目标格存在城市时，攻击绑定城市中心区域，优先于城内驻军。原生资格、攻击方式和路径通过核验后，只提交一次；`UNIT_CITY_ATTACK_CONFIRMED` 同时要求匹配的战斗参与者事件和城防／单位伤害，或近战单位进入已归本方的目标城。返回城墙、驻军、归属和攻击单位生命变化。普通 `unit move` 进入已识别的敌城会返回 `ENEMY_CITY_MOVE`，不提交移动。结果未知时保留原请求，以完全相同的攻击命令确认。

移动确认回执复用原生 POS 观察，在 `now_at` 后保留普通剩余移动力 `moves_remaining`、有效移动力 `movement_moves_remaining` 和 `zoc`，缺失值显示 unknown。普通移动力大于零不单独证明可以继续行动；到达、耗尽、控制区和未决确认的判断仍依据原有原生见证。

飞机使用 `unit rebase ID X Y` 转场，不使用地面 `move`。命令先核对当前原生可转场目标，再提交一次 `REBASE`；只有原飞机到达目标且消耗移动力才确认。结果未知时，以同一单位和目标确认原请求，不重复转场。

其他已有动作也有命名入口：`unit repair/remove-improvement/remove-feature/build-route/automate ID`、`unit activate/sacrifice-charges/spread-religion/launch-inquisition/condemn-heretic/remove-heresy ID`；带目标坐标的入口为 `unit trade-route/teleport/religious-attack ID X Y`。斜线表示分别可用的子命令，不能原样输入。完整参数用 `unit COMMAND --help` 查询；原有 `unit action` 保留。

商人迁移／建路确认回执另带原商人的 `moves_remaining` 观测，来自同一确认请求；读取失败、缺失或绑定不符保留 `unknown`，不默认零，也不撤销已成立的原动作确认。`route_eligibility:unchecked` 表示尚未核验下一条商路的完整资格，正移动力不保证可建路，迁移成功也不保证能立即建路。世界／回合范围守卫及原请求仅确认规则保持不变。

所有变更支持 `--dry-run`，只核对 schema，不验证当前资源和目标资格。`delete` 永久删除单位，`corps`／`army` 会消耗一个原单位，执行前先预演并读取当前状态。`skip-remaining` 只处理当前就绪单位，保留既有站立指令。编队／合并结果未知时，保留同一会话与完整参数，通过原命令确认；不能再次选择别的单位绕过门禁。只读 `unit formations` 不提交或退役请求。合并后使用回执给出的存活单位 ID，不假设发起单位一定保留。

本轮编队能力已完成隔离原生 Lua／MCP 与离线 CLI 验证，未在真实对局执行合并；实际原生事件顺序、解锁与合并结果仍需具备条件的受信存档验收。实现与证据见 [单位 CLI 说明](specs/2026-10-25-unit-cli.md)。

### 城市

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
| `save load NAME [--source-run RUN --sha256 SHA]` | `load_game_save` | 变更 |

`CATEGORY` 为 tech（默认）或 civic，研究 `ID` 为本次候选的原生类型。`save create` 可能覆盖同名普通存档，须使用独立名称；`save reload` 会丢失保存后现场，`save load` 仅从主菜单加载。文件系统存档回退当前只扫描 Single，不能据此声明 Hotseat 恢复可用。`turn end` 不替代双 AI 热座专用席位交接；热座沿用任务指定控制器及轮次记录，不并行另开 CLI 控制它。

`save reload`、`save load` 和 `game recover` 只接受本 CLI 登记过的检查点：`save create` 完成原生确认后登记身份与指纹，原生 `AutoSave_*` 不在登记表中。名字未登记时命令拒绝执行并说明原因，同时列出本轮可用的已登记检查点名称。FireTuner 一次只接受一个客户端连接；连接被拒时先结束其他 `civ6 debug` 会话或残留的 `civ-mcp` 进程再重试。

用户授权从历史阶段另开测试时，`save load` 可成对提供 `--source-run` 与 `--sha256`，在完整加载确认后新建独立轮次。原历史检查点保留；默认加载和恢复仍要求当前轮次。来源校验与另存起点要求见[保存与恢复](agent-recovery.md#检查点与测试轮次)。

### 地图、胜利与知识

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `map area X Y [--radius N]` | `get_map_area` | 只读 |
| `victory show` | `get_victory_progress` | 只读 |
| `knowledge search QUERY [--topic TOPIC] [--limit N] [--view brief\|evidence]` | `search_knowledge` | 静态只读 |
| `knowledge show ID [--level LEVEL]` | `get_knowledge` | 静态只读 |

地图半径默认 2、范围 1～4，越界直接拒绝。知识搜索 limit 默认 5、范围 1～20；LEVEL 为 summary（默认）、brief、rule 或 evidence。brief 保留完整规则，合并来源、父级 ID 和公共字段；公共字段作用于组内每行，格式见[知识说明](../civ6_knowledge/README.md#cli-查询与内部协议)。静态规则不证明本局资格；迷雾、未知和不存在仍须区分。知识查询也可在 `debug --offline` 内执行。

`map area` 的收益来自每次 GameCore_Tuner 查询中的 `Plot:GetYield`，保留原生小数；报告一次性标注来源。该上下文的修正完整性与原生数据年龄未验证，不能直接等同 InGame 城市工作地块收益，也不能把两者差异自动解释为缓存、基础值或缺少某项政策。收益块始终保留 F／P 的零值，省略的 G／S／C／Fa 明确为零；没有收益块表示未取得收益，不表示全零。迷雾与归属范围不变。

`knowledge search` 默认 `--view brief`，搜索结果、计数、截断、覆盖和适用范围均完整保留。已知重复的嵌套环境资料通过 `profile_reference` 引用，包含完整 profile 的 canonical SHA-256、`omitted_fields` 和同一 query／topic／limit 的 `view=evidence` 展开参数；零命中也可展开。profile 的标量、警告及尚未识别的新字段保留。`--view evidence` 返回完整 profile；内部 search API 默认 evidence，以兼容原调用。

### 政策、城邦与总督

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `policy show [--policy POLICY_ID]` | `get_policies` | 只读 |
| `policy set SLOT=POLICY_ID [SLOT=POLICY_ID ...]` | `set_policies` | 变更 |
| `city-state list` | `get_city_states` | 只读 |
| `city-state envoy PLAYER_ID` | `send_envoy` | 变更 |
| `governor list [--include-ineligible]` | `get_governors` | 只读 |
| `governor appoint TYPE` | `appoint_governor` | 变更 |
| `governor assign TYPE CITY_ID [--owner PLAYER_ID]` | `assign_governor` | 变更 |
| `governor promote TYPE PROMOTION_TYPE` | `promote_governor` | 变更 |

`policy show` 同时检查原生过时状态、当前槽位资格、政策更换成本／余额，以及零费用时是否已经改过政策。默认省略已过时或没有新增槽位的卡片，报告覆盖数量；资格未知的候选继续保留。`--policy` 可核对一张未列出的卡片，区分 `POLICY_LOCKED`、`POLICY_OBSOLETE`、`POLICIES_ALREADY_CHANGED` 和余额不足。一般检查通过不代表某份完整槽位方案一定合法，实际执行仍重新检查。

`governor list` 使用与执行入口相同的 `CanEarnPromotion`／`CanEverAppointGovernor` 及当前任命资格。默认省略原生明确拒绝的选项并报告数量，保留未知资格；`--include-ineligible` 展开未取得的晋升树与未任命总督，逐项标注原生资格。未取得的晋升不再自动表示现在可以晋升；资格为 false 的布尔接口未提供具体原因时，不猜测缺少哪项前置。

政策槽位可用空格或逗号分隔，未指定槽位保持原值。首次填槽或切换政体后，一次提供所有空槽；任何变更后的最终方案仍有空槽时，返回 `POLICY_PLAN_INCOMPLETE` 并列出编号，在付款或原生提交前拒绝。`NONE` 保留解析支持，但不能让变更后的方案留下空槽；原已为空且完全不变的方案仅返回 no-op。格式错误和重复槽位在发送前拒绝。城邦 player ID、总督类型及晋升均先查询；owner 省略时取本地玩家，显式 owner 为 0～63。政策、使者、任命和晋升可能消耗金币、使者或总督头衔，实际资格由引擎决定。

### 议会与通知

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `congress show` | `get_world_congress` | 只读 |
| `notifications show` | `get_notifications` | 只读 |

议会查询不打开投票、不提交票单；通知查询不关闭弹窗或代答外交。票单、交易、宗教与恢复均使用后文命名命令，保持同一会话。机制及确认条件仍见[游戏操作](agent-playbook.md)、[特殊操作](agent-special-operations.md)和[保存恢复](agent-recovery.md)。

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

### 外交、生命周期与完整命名入口

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
| `religion spread`、`religion found TYPE FOLLOWER_BELIEF FOUNDER_BELIEF` | 可见城市宗教分布；按已选原生信条创立宗教 |
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

### 开发与热座模式

普通会话不在 `tools` 中公开 `run_lua`，通过 `call` 或 `developer lua` 也会在发送前拒绝。用户明确授权诊断后，可在无未决状态下正常关闭原会话，再启动 `civ6 debug --developer`；此时 `developer lua "print(...)" --context gamecore --dry-run` 预演任意 Lua，正式执行仍受 Runtime 门禁保护。开发模式不自动清理未知请求，也不允许从另一个进程接管它。

既有双人热座实验可使用 `civ6 debug --hotseat-seat 0 --hotseat-run DIR`，其中 DIR 是已有、已核验补丁清单的授权轮次。该模式启动原专用控制器，`hotseat begin` 确认本席位 PlayerChange，`hotseat end` 确认原生交接，`hotseat actions 'JSON_ARRAY'` 执行最多 20 个已明确的独立动作。每条仍支持 `--dry-run`；普通 `turn end`、Single 存档操作及任意 Lua 在该控制器不可用。

热座不能与 `--offline`／`--developer` 合用；两个热座选项必须成对。现有共享 pending 会阻止新建该轮会话；活动会话的 `pending` 同时展示纯内存事实与独立的 `shared_pending`。正常退出会同时检查两者，保留原文件，不把空内存当作可以接管。此入口不创建补丁、不启动对局，也没有扩展到任意地图或更多席位；其真实交接验收仍按专用实验范围执行。

`scripts/hotseat_endurance.py` 是 2026-10-24 历史实验的复现器，仍向模型注入直接 MCP，必须显式提供 `--acknowledge-legacy-mcp`。它不属于当前支持的操作入口，不得用于新对局或替代上述 CLI 热座流程。

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

标准选项拼写为 `--dry-run`。可能改变游戏状态的命名命令直接追加该选项，例如先查询当前单位，再执行 `unit move 0 22 9 --dry-run`。CLI 校验命名参数及其映射后的真实输入 schema，返回 `event=dry_run`、原参数、`schema_valid=true`、`would_send=false`；未明确标为只读的工具按 `potentially_mutating=true` 展示。预演仍写本地证据。协议兼容诊断的 `call --dry-run TOOL JSON_OBJECT` 使用同一预演边界，不是普通对局的操作方式。

预演不发送目标 RPC、不查询游戏、不确认或清除 pending，也不检查实体是否存在、坐标可达、当前门禁或游戏前提。`scope=schema_only`、`pending_checked=false` 明确此范围；`local_policy_allows_execution` 只表示当前 offline／传输未知限制，不能当作游戏执行许可。可在 `debug --offline` 中预演游戏命令，实际调用仍被拒绝。

`quit --abandon --dry-run` 只读取服务内存，返回 `would_exit`、`would_lose_context` 并保留会话。真正 `quit --abandon` 仍显式丢弃进程上下文；有未决时返回 `BLOCKED`。预演没有自动执行、确认令牌或自动重试；实际执行须明确输入不带 `--dry-run` 的命令。

## 前台 debug 会话

运行 `uv run --locked civ6 debug` 后，整个前台会话只启动一个 MCP 子进程、初始化一次、使用同一个 ClientSession。启动只发现工具并读取服务内存，不自动查询、启动或加载游戏。退出不调用 `kill_game`。该命令不会接管旧客户端服务的进程或未决请求；旧注册移除后仍须先确认其请求并正常结束原所有者。

会话控制命令如下；它们不是独立 shell 子命令：

| 会话命令 | 行为 |
| --- | --- |
| `help [TOPIC]`、`TOPIC --help` | 分层帮助，返回 JSON 事件。 |
| `tools [NAME]` | 列工具名，或读取一个工具的真实输入 schema。 |
| `pending` | 读取本 MCP 的内存 pending 与本地 transport_unknown。 |
| `state overview`、`state units`、`state cities`、`state turn`、`state snapshot` | 概览、单位、城市、回合与结构化快照。 |
| `verify state` | 在当前会话执行自动实机只读验收。 |
| `call [--dry-run] TOOL [JSON_OBJECT]` | 同会话协议兼容入口；普通对局使用命名命令，省略 JSON 等同 {}。 |
| `quit [--abandon] [--dry-run]` | 正常退出、预览退出或显式放弃上下文。 |


`state` 是对应公开 MCP 查询的别名，`state snapshot` 返回结构化当前玩家快照。`call TOOL JSON_OBJECT` 只调用工具列表中实际存在的入口；省略参数等同 `{}`。JSON 不能有重复键、NaN 或 Infinity。CLI 按实际发现的 `inputSchema` 在发送前校验参数，MCP 仍执行服务端校验；动作确认与门禁仍由现有 Runtime 所有者处理；CLI 不调用内部 setter 或清理未决状态。使用实体 ID 和坐标前须先查询真实状态。

`pending` 调用 `get_runtime_status`：返回本服务的 UUID、PID、连接标记、动作摘要与动作／推进／外交／议会／保存／替换的未决事实。该工具只读取所有者内存，不查询游戏、确认请求、刷新缓存或清门禁。`connected=false` 不表示世界不存在，`has_pending=false` 不证明别的 MCP 进程没有未决请求。

服务 UUID／PID 标识本次 MCP 生命周期，不是世界身份或测试轮次。每次显式工具调用前也读取同一内存快照核对服务身份，不自动确认任何请求。

`--offline` 只允许发送 `get_runtime_status`、`search_knowledge`、`search_research`、`get_knowledge`，本地 `help/tools/quit` 和其他工具的 dry-run 也可使用。其他工具的实际调用在发送前拒绝，不能通过 `state` 或显式 `call` 绕过。离线检查可向会话输入知识查询和 pending，不必运行游戏。

会话异常与退出：

- Runtime 正常返回 `OUTCOME_UNKNOWN` 时，保持同一服务和原请求。先 `pending` 及只读核对，再按[恢复指南](agent-recovery.md)通过原入口确认；CLI 不自动重发。
- RPC 超时或丢回应时，额外保留本地 `transport_unknown`，后续只允许只读工具。空 pending、缓存刷新和后续读成功都不能清除这个标记；本版不自动恢复该次传输结果，也不重建 MCP 进程。
- 游戏侧只读工具有有界预算（45 秒）：主菜单、加载中或模态界面阻塞时，工具返回 `ERR:TOOL_TIMEOUT`（未提交、未确认、未重放任何内容），CLI 不再等到 300 秒 RPC 超时并锁存 `transport_unknown`。用 `civ6 tuner status` 区分“游戏不可达／连接被占用／残留会话”，再决定加载或恢复。
- `quit` 会新读 pending；有未决请求、传输未知或无法确认服务身份时拒绝退出，留在原会话。`quit --abandon` 是明确放弃当前进程内上下文的退出入口，仍返回 `BLOCKED`（`context_lost=true`、`exit_reason=abandoned`），不退役游戏中的原请求；传输未知导致无法确认时，这是 CLI 内的显式收尾路径。保留证据后按已授权的检查点恢复流程处理，不能开一个空会话冒充已确认。
- 交互终端 Ctrl+D 有未决时返回提示；stdin 管道已耗尽时无法继续交互，保存未决快照、返回 `BLOCKED` 后关闭自有 MCP，`data.exit_reason=eof_unresolved`。该结束会丢失进程内上下文，不支持跨进程续接。
- Ctrl+C 的设计行为是不取消正在执行的 RPC，也不终止 MCP；等待请求返回后继续输入。每个 RPC 默认等待上限 300 秒，可用 `debug --timeout SECONDS` 调整。POSIX 子进程使用 SDK 的独立进程组。Windows 终端中断导致的空读会继续读取原输入流；Windows 11／Python 3.12.2／UU 真终端已验证连续两次空闲 Ctrl+C 后保持同一 PID／session ID，继续查询并显式退出，见 [Windows 11 修复复验](acceptance/2026-10-27-windows11-cli-fixes.md)。实际 Windows 游戏 RPC 中断行为仍未验证。
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
