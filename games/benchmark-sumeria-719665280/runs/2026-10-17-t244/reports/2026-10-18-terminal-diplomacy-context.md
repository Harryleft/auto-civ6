# 外交当前上下文验收

规格：[当前会话与交易缓存](../changes/2026-10-18-terminal-diplomacy-context.md)；[工作项](../changes/2026-10-18-terminal-diplomacy-context-tickets.md)。实现 `bd54c47`，集成 `a08791e`；GOODBYE 指引 `ee30f84`，集成 `a688633`。任务分支 `codex/architecture-deepening`，未 push。

工作交易会在会话结束后保留，不能单凭非空且有效的缓存判断当前议题。两个查询和交易回应现共用会话、参与方、根视图及 incoming 方向判断；普通对话不读旧交易，无法关联时保留 UNKNOWN，发送或关闭前重新核验当前提案与目标。GOODBYE 指向显式 `EXIT`，未扩大自动关闭条件。

| 验证 | 结果 |
| --- | --- |
| 完整离线 | 1,119 项通过，29.36 秒；真实 GameState／Lua 5.1 覆盖多会话、重复 sid、参与方、方向、隐藏根、旧缓存、异常及零提交；编译与 diff 检查通过 |
| 指引增量 | 362 项通过，2.19 秒；公开工具 → GameState → Lua 5.1 验证空文案 GOODBYE 可用 `EXIT`，没有 AddResponse 或交易发送 |
| 独立双轴 | 实现及指引增量的 Standards／Spec 均 0 项发现 |
| 第 150 回合 NONE | 两入口均无当前会话；使用正确参数回应瑞典交易，返回 `NO_CURRENT_PROPOSAL\|NONE` |
| 第 152 回合 INCOMING | 两入口一致显示巴西的当前提案；错填瑞典被资格检查拒绝，巴西提案仍在；正确接受巴西后两入口 NONE，原推进请求完成 152 → 153 |
| 第 155 回合 DIALOGUE | 两入口均识别瑞典普通对话，不附旧交易；使用正确参数交易回应，返回 `NO_CURRENT_PROPOSAL\|DIALOGUE`，再次查询对话保留；POSITIVE 返回 SESSION_CONTINUES，显式 EXIT 关闭，随后 NONE，原推进请求完成 155 → 156 |

三类实机流程通过；UNKNOWN 和零原生提交的异常边界由隔离测试覆盖。实机日志第 1341 行曾使用错误参数 `player_id`，在参数校验阶段失败，不计作上下文资格检查证据。原始基准、现有存档及既有验收结果保留；本修复的实测完成，整体已到第 244 回合原生终局，见[全程验收](2026-10-18-turn-terminal.md)。

证据位于 `~/.civ6-mcp/acceptance/20261018-turn-terminal/`：`diplomacy-context-live-evidence.json` 提取 session.jsonl 第 1241–1245、1275–1289、1337–1355 行的对应结果；`diplomacy-context-fix/` 保留红绿测试、完整／增量测试日志、公开 EXIT 验证及 tested-tree 指纹；`diplomacy-context-review-standards.md`、`diplomacy-context-review-spec.md` 保留独立审查。源码关联研究及第 145 回合纯读探测另见规格中的证据指针。
