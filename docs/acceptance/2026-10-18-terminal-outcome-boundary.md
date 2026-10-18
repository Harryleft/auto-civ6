# 第 244 回合终局类型与保存边界验收

原生已显示苏美尔失败、瑞典科技胜利；旧工具遗漏 GetWinningTeam 第二返回，且附加进度 `33.5` 会破坏整数解析并回退 Unknown。改为两上下文共用官方胜队／类型映射，胜负按队伍判断，删除进度猜测和 Python 改写。已确认终局的新保存明确零提交；旧未知仍优先门禁。

| 验证 | 结果 |
| --- | --- |
| 实施与集成 | `d7452c6` → `942a70d`；完整 tree `49f868e3a9d803b4023f7168afc3ba56f95500b4` 与实际已测树一致 |
| 离线 | 1,385 项通过，31.46 秒；相关 576 项，3.74 秒；新增 45 项真实 Lua／GameState 用例；编译、diff 通过 |
| 独立审查 | Standards／Spec 各 0 发现；Spec 独立重跑 45 项通过 |
| 终局重放 | 从已确认第 244 回合恢复；一次 end_turn 用时 11.0788 秒，返回 DEFEAT／Sweden／Technology，概览返回 `VICTORY_TECHNOLOGY`，与原生截图一致 |
| 保存保护 | 新独立名称调用 0.0375 秒明确返回未提交；54 个原生存档的名称、大小、mtime 均不变；未产生该名称文件 |
| 拒绝后读取 | 概览、单位、城市和胜利进度均成功，仍第 244 回合、同 PID 77131；两城七单位可读 |
| 结果保留 | 终局前 `benchmark_turn_244_terminal_progress_20261018` 及首次／复验终局截图保留，未生成的终局后存档不列为成功 |

原生确认与新鲜非空文件的双证据保存流程保持；公开回归覆盖活动回合一次保存、旧未决优先、准备期间才发生终局的同帧零提交，以及零提交保留同名旧检查点资格。可能已提交或回应丢失仍作未知并撤销旧资格。独立审查还用临时目录复现元数据删除 PermissionError：未知门禁保留，恢复端再次核对文件 SHA，未复现错误文件加载。

首次终局后保存确有一次未知，名称 `benchmark_turn_244_terminal_defeat_20261018`，9.6386 秒后无文件和原生完成确认；菜单先被门禁拒绝，再显式完整恢复 244，61.7190 秒、PID 30265→77131、零回合增长。没有重发旧名称或重建客户端来遗忘未决。此次终局重放也没有 244→245 增长，作为一次终局事件复验记录。

终局保护是本项目活动回合保存边界，不声称引擎所有终局或“再战一回合”永久不能保存；未自动关闭结果窗口或释放暂停。游戏保持原生失败界面。

证据目录 `~/.civ6-mcp/acceptance/20261018-turn-terminal/`：`terminal-boundary-research.md`／`.json`、`terminal-boundary-parser-repro.json`、`terminal-boundary-fix/`、`terminal-boundary-review-*.md`、`terminal-boundary-live-validation.json`、`terminal-guard-files-before.json`／`after.json`、`terminal-defeat.png`／`terminal-defeat-verified.png`。[规格](../specs/2026-10-18-terminal-outcome-boundary.md)；[工作项](../specs/2026-10-18-terminal-outcome-boundary-tickets.md)；[全程验收](2026-10-18-turn-terminal.md)。
