# 来约试价与反提案验收

集成 `edf8d3f`，分支 `codex/native-pillage`，未 push。[规格](../specs/2026-10-18-incoming-counteroffer.md)与[工作项](../specs/2026-10-18-incoming-counteroffer-tickets.md)。

用户要求有限试价后再决定成交。旧查询把手改后已遭拒绝的瑞典来约仍当作可接受报价；现有主动交易入口也不能修改当前来约。新 `negotiate_incoming_trade` 使用本机官方 DiplomacyDealView 的 INSPECT／EQUALIZE 路径，调整已存在的金币项，读取可接受资格与当前条款；不另开会话、不成交。最终接受仍是明确的独立动作。

本机官方 Expansion2 原始 UI 是接口依据：编辑金额后 INSPECT，EQUALIZE 请求反提案；ACCEPTED／ADJUSTED 属于最终提议或接受流程，不能用于试价。来源路径、行号及 SHA-256 保留于 `incoming-counter-impl/official-api-research.md` 与来源指纹文件。

| 验证 | 结果 |
| --- | --- |
| 初版相关测试 | 9 个模块 637 项通过，独立合入 `184dce1` |
| 世界门禁修复 | `9152059`／集成 `9aad7f4`；6 个相关模块 589 项通过。旧 end_turn 只为同一来源世界放行当前来约处理 |
| 叙述及同价 EQUALIZE 修复 | `a441ad4`、`5a585f2`；独立集成 4 个相关模块 228 项通过，2.68 秒；各批次用例有重叠，不累加 |
| 独立审查 | Standards／Spec 修复后均无遗留问题 |
| 实机试价 | T138 来约原为和平、巨作、8 金币及每回合 2 金币；50、25、12 金币条件被拒，EQUALIZE 提出巨作及每回合 3 金币、无一次性金额 |
| 不同评估模式 | 4 GPT、3 GPT 的 INSPECT 均遭拒；EQUALIZE 对相同 3 GPT 条款给出可接受资格。不能将不同模式观测概括为唯一价格上限 |
| 修复复验 | 423 同价 EQUALIZE 返回 WILLING，0.46 秒；424 当前资格 ACCEPTABLE；425 才明确接受；426 确认和平，427 无开放提案，428 确认原推进 T138→139 |
| 成交价值 | 接受和平、巨作《实验哲学观察》及 3 GPT×30 回合，无一次性金币。名义金币流 90 对原报价 68 增加 22；未声称未来全部兑现或达到全局最高价 |
| 结果保存 | benchmark_terminal_20261018_t139_negotiation_verified，1,064,738 字节，原生文件及身份回合确认 |

初次同价 EQUALIZE 虽已显示可接受，旧确认分支仍保留 UNKNOWN，重试未重发。修复增加相关正面原生事件、资格及精确条款确认，并保留 AI_REFUSE 优先。故障恢复前归档，当前未决门禁拒绝菜单换档，改走已授权完整恢复；首次启动端口瞬开后断线，第二次受保护恢复 63.036 秒成功。恢复使用本轮已确认 T138 检查点；之后复现相同报价和试价顺序，验证该分支通过，未借恢复更换战略结果。

证据：`~/.civ6-mcp/acceptance/20261018-turn-terminal121/` 的 `incoming-counter-*`、`incoming-equalize-stall/` 与 `live/386–445`。原生来约已实际成交并续行，失联／取消／异世界等分支由离线测试覆盖。旧存档与旧验收保留，整轮终局验证继续。
