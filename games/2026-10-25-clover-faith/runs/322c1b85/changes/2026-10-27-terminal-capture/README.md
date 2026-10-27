# 终局占城确认修复与验收

2026-10-27，修复最后一次近战占领触发原生征服胜利、攻击者仍停在相邻格时的确认问题。修复版通过 713 项隔离定向检查；原 T237 胜利现场、旧 CLI owner 和原 pending 按用户确认边界保留，没有再次攻击或重启。

## 规格与任务

工作按 `to-spec` → `to-tickets` → `implement-spec` 进行。用户确认公开 GameState 攻击／实体查询与真实 Lua 的测试边界，以及 `01→02` 两任务依赖；本地 tracker 原件位于 `.scratch/terminal-capture-confirmation/`。以下为可提交的归档快照，规格父项未关闭。

- [规格说明书](spec.md)
- [01：终局近战占领确认](issues/01-terminal-capture.md)，已解决
- [02：实体查询零重发确认与验收](issues/02-query-confirm-validation.md)，已解决
- [独立双轴审查](review.md)，最终 Standards／Spec 均无实质问题

集成分支为 `codex/terminal-capture-confirmation`。各票通过独立 worktree 和分支实施，再由合并子代理整合；审查问题交同一实施子代理修复。未 push、未改写历史。

## 行为变化

终局例外同时要求：提交前已知尚无获胜队伍；原请求 token、世界／回合／本地玩家匹配；精确原战斗参与者见证；目标城市归本方；有效且存活的原攻击者仍与目标相邻；原生获胜队伍匹配本方队伍，胜利类型为 `VICTORY_CONQUEST`。确认末尾复核原生结果及实体身份，已观察到的有效冲突继续锁存。

满足条件时，原动作输出 `completion:terminal_conquest`，只声明终局占领完成，不声明攻击单位已经进城。普通非终局占领继续等待进城；缺失、未知或不匹配证据不能清门禁。

延后终局证据可以通过现有 `unit list`／`city list` 确认原动作。确认回执在本次实体查询的文本结果中显示，后续查询不重复显示；查询期间不再调用攻击入口。默认领域实体返回类型保持不变，没有新增持久状态或生产工具。

## 提交与离线验证

| 提交 | 变化 | 验证 |
| --- | --- | --- |
| `4e0cba5` | 归档已确认规格 | 文档链接及 diff 检查 |
| `8684155` | 精确终局占领确认与普通占领保护 | 原症状先红后绿；攻城 79 项通过 |
| `73a1e72` | 命名 CLI 实体查询纵向回归、说明文档 | 旧攻城模块下两个正例失败、两个负例通过；修复版四例通过；8 文件 381 项通过 |
| `cc84abf` | 一次性查询确认回执，修复初审 P2 | 回执正例先红后绿；查询 6 项通过；11 文件 713 项通过 |

最终定向检查覆盖：攻城、攻击模式、动作确认、未决调度、终局边界、实体查询 CLI、常用命令、debug CLI、server、GameState 和会话命令。编译检查与 `git diff --check` 通过；最终无排除项。381 与 713 是递进检查范围，不相加为独立测试总量。

一条基点已有的撤销检查点回归仍断言旧文案 `Error: Checkpoint`，在未改攻城代码的版本也失败。本次最小更新为当前公开错误码 `UNREGISTERED_CHECKPOINT`；保存未决门禁和原生保存次数断言保留，没有修改检查点生产行为。

命名 CLI 纵向测试使用 in-process MCP 传输边界，实际执行 DebugSession、服务工具、GameState、ActionLifecycle 和生成的 Lua；仅原生 API 使用隔离夹具，没有连接真实 FireTuner。

## 原现场与证据保全

本任务在原 CLI 会话只执行两轮 `pending`、`state overview`，共四条只读命令。均读取到 T237、苏美尔、seed `-1896272000` 和原生 `VICTORY_CONQUEST`。owner 仍为 `61299c3714ff42599b2f43cce1fab837`，原占领动作仍由它持有。

| 保全对象 | 核对结果 |
| --- | --- |
| 四叶草受信原始基准 | SHA-256 未变 |
| 51 份已登记 `clover_faith` 检查点及元数据 | 内容、字节数、SHA-256 均与修复前一致 |
| 原 T237 终局摘录 | SHA-256 仍为 `a7e7405ea6f4f250f759421454d0d29bcfc2c19f72dda4b45bfee36198cdca47` |
| 原 CLI 日志前 2703 条记录 | 与原终局归档时的 SHA-256 一致；新查询仅追加 |
| T173 来源存档 | `f8056aab7abf7338bfbeb8bc86f9c5412d2b644fc21a4505f3b520b66e85f5d4` |
| T237 进城前检查点 | `8ce33bdbe8ca1ee31ecad5d571c227e4fb795cd8cc2319f43986c9965755a8a6` |

证据路径相对仓库根目录，本地保留，不提交大型原始文件：

- 原终局：`games/2026-10-25-clover-faith/runs/322c1b85/evidence/2026-10-27-terminal.json`。
- 本次现场读取与前后指纹：同目录 `2026-10-27-terminal-confirmation-preservation.json`。
- 红绿、定向检查及文件指纹清单：同目录 `2026-10-27-terminal-confirmation-tests/`。
- 本次只读现场查询追加在 `.scratch/civ6/75516909285341bdb87d1fe105076ebf/debug.jsonl`。

## 验收边界

离线修复与命名 CLI 隔离验证通过；原生胜利现场及文件保全复核通过。旧 owner 已加载的 Python 确认器与首次提交时冻结的 Lua 查询无法通过磁盘修改更新，因此本次没有把修复版热加载到旧 owner，没有退役原 pending，也没有宣称原动作已由修复版实机确认。这一限制已在实施前由用户确认。

## Worktree 清理限制

已尝试通过 Codex 的管理工具归档三个实施 worktree。`terminal-capture-review-fix` 被拒绝，原因为 `The task's primary checkout cannot be removed.`；`terminal-capture-01`、`terminal-capture-02` 被拒绝，原因为 `Another active task still uses this worktree.`。恢复聊天到原项目本地检出的请求也因原目录不是 managed worktree 被拒绝。三个 worktree 均保留，没有绕过主检出／占用保护执行强制删除；源代码已全部合入集成分支，测试原始证据另存于本局 evidence 目录。
