# 决策事实查询

[返回 CLI 索引](../cli.md)

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

