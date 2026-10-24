# CLI 领域命令与自动状态验收实测

2026-10-24 复用本轮 T136 瑞典暂停现场，对新增 CLI 领域命令和 `verify state` 自动验收执行实机检查，已测范围 **PASS**，无非预期错误。未解除交接暂停、执行游戏变更或重载存档。

## 现场与结果

| 检查 | 实测结果 |
| --- | --- |
| 自动验收 | `civ6 verify state --expect-turn 136 --expect-civilization CIVILIZATION_SWEDEN` 返回 PASS、退出码 0、`game_verification=passed_readonly`。一个 MCP 会话完成概览及结构化快照两次目标 RPC。 |
| 世界身份 | 文明 `sweden`、种子 `-1896272000`、玩家 1、回合 136；读取前后身份、玩家和回合一致。 |
| 实体范围 | 6 城、7 个已列单位；城市数与概览一致，城市归属／种子匹配，城市和单位 ID 唯一。 |
| 错误世界保护 | 故意指定 `--expect-turn 135`，观察仍为 T136，正确返回 `BLOCKED`、退出码 2、`UNEXPECTED_WORLD` 及修正建议；未自动换档。 |
| 领域命令 | 同一 debug 会话输入 25 条命令，13 次真实查询均正常返回，涵盖单位、城市、生产选项、科技市政、回合、存档列表、知识及会话内自动验收。 |
| dry-run | 单位移动／删除、城市生产、研究、保存／重载／菜单加载、回合推进共 8 项预演，全部 `would_send=false`，无目标变更工具进入发送记录。 |
| 状态保持 | 三次结构化快照完整内容一致；没有传输未知或 pending，正常退出并关闭自有 MCP。 |
| 原存档 | 原生 T136 检查点和保留副本 SHA-256 均保持 `814177e6bf21b7a1199db3b961a02c7543179bb92ac2d6c9b24b3e32a3b64a11`，未创建预演保存文件。 |
| 控制通道 | 原 9 个调度／控制进程在本次检查后全部仍为挂起状态，游戏进程保持；新 CLI 期间仅通过自身 MCP 访问游戏。 |

本次误配回合是预期拒绝测试，未计为功能错误。普通前台摘要只描述会话退出，自动只读通过另由 `verification` 事件及原生快照逐项判断。

## 本地证据

原始证据均留在本轮忽略目录，不提交个人路径及完整日志：

- `evidence/cli-domains-20261024-state-final/`：一次性自动验收的摘要、事件、两次原始响应和 `verification-snapshot.json`。
- `evidence/cli-domains-20261024-world-guard/`：故意指定错误回合时的阻塞摘要及观察事实。
- `evidence/cli-domains-20261024-session-final/`：领域命令全部 JSONL、原始返回、`live-assertions.json` 和控制进程检查。

三组证据中的运行层源码 SHA-256 均已与最终代码核对一致；服务 stderr 无 ERROR、Traceback 或 Exception。工程回归与设计范围见[CLI 领域化验收](../../../../../../../docs/acceptance/2026-10-24-cli-domains.md)。

## 未验证

实际单位动作、生产／研究变更、保存／读档及推进只测试参数转换和 dry-run，未在暂停的热座现场执行。它们的单次提交、原请求确认和 offline 门禁另由隔离 stdio／真实 Runtime 测试覆盖，不能据此宣称实机变更通过。Single 文件系统存档回退仍不覆盖 Hotseat，普通 `turn end` 仍不替代专用热座席位交接。
