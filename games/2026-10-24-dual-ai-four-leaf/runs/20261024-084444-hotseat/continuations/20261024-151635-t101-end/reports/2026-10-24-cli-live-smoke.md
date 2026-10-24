# CLI 实机只读续验

2026-10-24 使用提交 `386ec2c` 的 `civ6` CLI 验证当前已加载的 T136 瑞典现场，结果为 **PASS（限下列范围）**。9 次真实工具调用全部成功，未观察到非预期 CLI 或 MCP 错误；另有 1 次故意传入错误类型的参数被正确拒绝。

## 起点与控制通道

用户要求使用此前操作过的存档验收 CLI。本轮复用原对局已加载的暂停现场，没有重新读档：瑞典／克里斯蒂娜、T136、6 城、42 人口、7 单位、293 分，与[暂停记录](../README.md#用户暂停t136-瑞典交接页)一致。检查点为 `dual_endurance_20261024_151635_e1_t136.Civ6Save`，原生文件和保留副本均为 802,271 字节，SHA-256 均为 `814177e6bf21b7a1199db3b961a02c7543179bb92ac2d6c9b24b3e32a3b64a11`。

既有双 AI 调度器及其控制进程在验收前后均保持 SIGSTOP 挂起；本次只通过新前台 CLI 的一个 MCP 子进程执行查询，没有调用 Codex 内置游戏 MCP，没有解除热座交接或推进回合。新 CLI 的 pending 仅表示自身服务内存，不替代其他进程的未决事实；旧轮次的暂停证据记录 pending 为空。

## 实际结果

| 功能 | 结果与证据范围 |
| --- | --- |
| 真实状态查询 | `state overview/units/cities/turn` 均返回成功；概览、单位和城市的前后完整响应一致。热座交接时 turn 返回 `LOCAL_TURN_STARTING`，没有把它当作已就绪或执行结束回合。 |
| 同一服务与 pending | 所有已记录服务身份相同；全部 pending 快照无未决，传输未知为空。正常 quit 返回 PASS，关闭自有 MCP，未丢失未决上下文。 |
| 显式工具调用 | `call get_units {}`、`call list_saves {}` 成功；与状态别名共 9 次实际目标调用。 |
| dry-run | 移动、保存、放弃退出各预演一次；目标动作及保存未进入调用记录。保存预演未创建 `cli_smoke_preview_only.Civ6Save`，已有检查点指纹不变。 |
| 分层发现 | `help`、`call --help`、`tools unit_action` 分别返回主题、调用说明和完整真实 schema。 |
| 可修正错误 | `unit_id` 故意传字符串时，本地以 `INVALID_TOOL_ARGUMENT` 拒绝，指出 `details.issues.path=["unit_id"]`、integer 类型要求和修正建议，没有发送该动作。 |
| 日志查询 | `logs list/show/timeline` 正常；筛选单位查询命中 3 条，完整时间线命中 9 条，顺序与本次真实目标调用一致，无 dry-run 动作或保存记录。 |
| 离线入口复核 | 同一检出的 doctor、verify knowledge 均 PASS；它们未访问游戏。 |

## 证据

原始证据在本轮忽略目录 `evidence/cli-smoke-20261024/`，不提交个人路径和原始日志。

- `verification.json`：独立验收断言；50 条事件序号连续，9 个原始 MCP 结果无错误，同一进程，前后状态一致。
- `debug.jsonl`、`debug-response-*.json`、`summary.json`：前台命令、调用参数、原始返回及退出结果。CLI 的摘要仍保留产品定义的 `game_verification=unverified`；本报告的通过只覆盖本次实际核对的范围。
- `context.json`、`debug-tools.json`、`debug-stderr.log`：源码指纹、工具契约和服务 stderr；受测源码与当前源码一致，stderr 无 ERROR、Traceback 或 Exception。
- `checkpoint-integrity.json`、`process-state-after.txt`：原存档指纹、预演文件缺席和既有控制进程仍挂起。
- `logs-list/`、`logs-show/`、`logs-timeline/`、`doctor/`、`knowledge/`：对应 CLI 的独立 JSON 摘要及证据。

## 未覆盖

未测试重新加载检查点、实际动作／保存／回合推进、真实未决请求的确认或异常恢复。`list_saves` 的现有文件系统回退返回 Single 存档列表，未列出本局 Hotseat 检查点；本次不把它记为热座读档通过。未注入真实连接故障，也未终止、恢复或替换既有控制进程。
