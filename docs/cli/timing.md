# 回合耗时统计

[返回 CLI 索引](../cli.md)

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

