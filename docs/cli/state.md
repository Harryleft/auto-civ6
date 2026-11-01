# 决策事实查询

[返回 CLI 索引](../cli.md)

连续决策可使用 `civ6 debug --compact`，在同一会话执行 `state decision` 或 `game decision`。该入口将现有领域 getter 合成一次有界 InGame 请求，返回本方概览、地图内单位、自有城市、当前可见威胁、公开胜利进度、通知、通知阻塞和原生终局。每部分保留原解析器和可见性规则，不生成策略、目标排序或动作。

```text
state decision
game decision --include research diplomacy trade
game decision --city 65536 --include research
game decision --view structured
game decision --view compact
```

`--city` 的 ID 必须来自本次真实查询，示例值不能直接照用。`--include` 只补充明确要求的领域；`--city` 补充指定自有城市的原生生产候选。默认 `--view brief` 复用现有领域报告；`compact` 为单位、城市与胜利进度共享表头，保留对应事实、未知标记、警告和明细，其他段保持原报告；`structured` 返回解析后的数据，体积通常更大。三种展示均重新读取当前事实，不依赖上次报告或缓存，不改变未决门禁与确认语义。未请求的领域不出现在 `sections` 中，不表示其内容为空。地图、规则、资源库存、政策、外交会话及议会详情仍按决策需要通过对应命令补查。

先检查顶层 `status`、`identity`、`checks` 和各 section 的 `status`。读取前后核对文明、种子、玩家和回合，并核对实体数量、城市归属及胜利数据范围。任一未决请求在游戏 I/O 前返回 `blocked`；分项异常、缺少完整回执或范围不一致返回 `unknown`，保留其他已读事实。`ready` 表示本次约定的读取与范围检查通过，字段内原有的未知、不可见和原生统计冲突仍需保留。读取不是引擎原子快照，也不缓存或接管动作确认。

`blockers` 仅为通知阻塞；空列表不证明所有推进前提满足。`turn end` 仍执行原有就绪、外交、议会和单次提交检查。需要诊断等待阶段或确认旧请求时使用 `turn status` 及原入口；`verify state` 继续使用独立的验收快照。

## 显式变化展示

```text
game decision --view changes
game decision --view changes --since TOKEN_FROM_PREVIOUS_DISPLAY
```

`changes` 是前台 CLI 的无损展示选项，内部每次仍执行完整 `get_decision_context(view="compact")`；不省略原生读取，不选择策略，不影响 Runtime 的请求和门禁。首次调用返回全量当前事实及 `presentation.token`。只有调用者明确提供上一份已解码展示的精确 token，才可缩短重复的长文本；遗漏或不匹配 token 时返回全量，并在 `reset_reason` 说明原因。默认 brief、compact、structured 和 `state decision` 不改变。

每次完整保留当前 `status`、`pending`、`identity`、`checks`、各段状态及错误。`data` 为字符串时含义与 compact 相同；较短的变化表示则为 `encoding="line-splices-v1"` 对象：`splices` 中每项 `[start, delete_count, replacement_text]` 使用**旧段文本的零起点行号**，删除指定行数后插入文本，所有位置均相对于同一旧段，行尾符号保留。按顺序拷贝未覆盖的旧行并插入新文本，可恢复原文。`base_sha256` 校验旧段；顶层 `presentation.sha256` 校验完整重建结果。哈希使用 UTF-8、`ensure_ascii=False`、无额外空格的 JSON 序列化。补丁比全段更大时直接展示全段。

`anchors` 给出变更附属行所属的当前单位／城市行或表头；`old_anchors` 标明删除附属行的旧所属实体。`removed_from_observation` 明确列出本次已不在单位／城市观察中的旧行，**不表示单位阵亡或城市被占领**。这些行仅帮助理解精确补丁；不参与游戏规则判断。数组、null、空集合、错误与未知字段不借用旧值补齐。

CLI 内存只保存一份展示基线，绑定当前 CLI／Runtime、查询参数、文明、种子、玩家与回合；同回合或下一回合允许比对。参数变化、回合倒退／跳跃、身份变化或无法验证、本次决策非 ready、传输未知和世界替换尝试都会失效。普通已确认动作、正常推进及独立只读查询不清除历史展示；推进暂时 pending 也不使它变成当前事实，此时再查 decision 仍由原门禁拒绝并失效。普通保存不替换世界。更换 CLI、缺少完整基线或上下文中不再保留基线时，省略 `--since` 重新读取全量，不能猜测未展示的事实。

完整原始 RPC 始终先写入 `debug-response-*.json`。`tool_result` 事件结构与 relay 完成边界不变；显式 changes 的 `content` 和 `structuredContent.outcome.data` 使用同一展示投影，不同时混入全量原文与补丁。状态、错误码及计时身份保持原值；不支持或相互冲突的协议回应原样显示且不建立基线。消费者先查看 `presentation`，再展示各段 `data`；重建时保留上一份完整解码文档与其 `presentation`，可用纯函数 `devtools.decision_display.restore_display` 校验。想持续减少重复输出，应保持 changes 的查询范围固定，临时追加的领域可另用对应只读命名命令。

## 自动实机只读验收

`civ6 verify state` 启动一个 MCP 会话，先检查其 pending，再查询概览和结构化 `get_game_snapshot`，最后核对服务身份及 pending，关闭自有 MCP。stdout 只输出一份 JSON；详细事件和原始响应保留在证据目录。可选 `--expect-turn`、`--expect-civilization` 约束预期现场，不匹配返回 `BLOCKED`，不自动换档。每个 RPC 的默认超时为 300 秒，可用 `--timeout` 调整。

快照直接返回原生数据，包含文明／种子／玩家／回合、两次概览、城市和地图内单位。读取前后核对世界和玩家回合，城市须匹配所有者和种子，城市数须与概览一致，实体 ID 须唯一。单位列表可能省略离开地图的间谍，所以只检查列表数不超过概览数，不把未列出的单位解释为不存在。它不是引擎的原子快照，也不证明迷雾外事实。

存在任一未决动作、推进、外交、议会、保存或加载时，结构化快照在游戏 I/O 前返回 `blocked`；自动验收不会借普通实体查询消费确认。读取失败、身份变化、字段未知或范围检查不全，均不能获得通过。`get_runtime_status` 仍仅表示本服务内存，不能检测另一个 MCP 进程的意图；运行前须明确唯一控制通道。

该命令使用 `mode=live`，成功时 `game_verification=passed_readonly`，未确认时为 `unconfirmed_readonly`。这只覆盖只读验收；实际动作、保存、加载和回合推进须分别验证。会话内也可运行 `verify state`，使用当前 MCP 和 pending 上下文，结果写入 `verification` 事件；前台会话最终的 `PASS` 仍只表示正常退出。

