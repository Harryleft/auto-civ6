# 来约两项 P2 离线修复验收

两项暂缓 P2 已修复。基点 `43ed6c6`；[规格](../specs/2026-10-19-incoming-p2-boundaries.md)与[工作项](../specs/2026-10-19-incoming-p2-boundaries-tickets.md)先行，实施提交 `e75b399`、`63fd02c`，独立集成至 `5ad7a68` 后合入 main。只做离线验证，没有连接游戏、推进回合或 push。

## 修复与回归

| 风险 | 修复 | 回归证据 |
| --- | --- | --- |
| 原生编辑校验失败并恢复草稿，但零提交回应丢失／取消后无法解除未决 | 原尝试确认支持绑定世界、会话、参与方及 token 的 ZERO 回执；原生明确 `zero == true` 且 `sent == false`，零提交 finish 也确认后才退役并返回 NOT_SUBMITTED | 发送／finish 回应丢失或取消后只确认；草稿指纹不变、零评估发送。缺失／错 token、会话、参与方、矛盾 sent 标志或 finish 未确认继续保护 |
| 没有未决 end_turn 时，观察到换世界后返回原身份可错误确认旧来约 | DiplomacyProgress 独立核对并锁存完整的不匹配身份，继续向 TurnProgress 交付观测；身份未知或不完整只阻止当前确认 | 没有推进请求时，发送、回执、最终报价及原生 guard 各边界换世界后，旧事件不能解除门禁；UNKNOWN／不完整身份恢复后，同世界原尝试仍可安全确认 |

独立 Standards 初审又发现：ZERO 回执之后的 finish 调用若换世界，原生 guard 提前返回会丢失世界证据。补修让来约 arm、响应读取、即时／重试 ZERO finish 和正常 finish 在原生 guard 返回前输出实际世界，Python 在处理结果前消费观测。回归覆盖外国身份只出现在一次原生调用内、返回前已经恢复的情况。主动交易保持默认协议，不增加后台任务、自动重发或隐式恢复。

## 离线验证与审查

沿用既有 GameState／内存 Lua 5.1 边界。新增 34 个参数化回归用例，按红绿顺序完成；仅运行以下 6 个受影响模块。表中均为最终源码结果，各行测试文件不重叠。

| 测试模块 | 结果 |
| --- | --- |
| test_incoming_negotiation.py | 83 passed，0.92 秒 |
| test_trade_progress.py、test_diplomacy_context_lua.py | 112 passed，3.41 秒 |
| test_diplomacy_cleanup.py、test_end_turn.py、test_pending_dispatch.py | 146 passed，0.81 秒 |
| 合计 | 341 passed；未运行全量测试 |

三个变更代码／测试文件编译检查与 `git diff --check` 通过。独立 merger 复用同一受测源码结果，不重复测试已通过模块。最终 Standards 0 项、Spec 0 项；初审及补修证据分别保留，没有覆盖原报告。

## 存档与终局保全

任务前记录原生 Single 存档、全部既有本机 acceptance、检查点、仓库存档及旧验收文档，共 8,665 个文件的 SHA-256。任务后逐项核对：缺失 0、变化 0。保留原始基准、T232 获胜前存档和 T233 原生科技胜利验收，不新增实机结论。

本次证据独立保存在 `~/.civ6-mcp/acceptance/20261019-incoming-p2-offline/`：`impl/` 保存各片红绿日志；`merge/final-result.json` 保存最终模块结果及源码指纹；`review-standards-final.md`、`review-spec-final.md` 保存复审；`preservation-before.json`、`preservation-after.json` 保存文件保全。既有 T233 结果仍见[终局验收](2026-10-19-turn-terminal.md)。
