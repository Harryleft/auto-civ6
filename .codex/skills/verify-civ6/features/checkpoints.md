# 保存与检查点恢复

验证独立名称的检查点保存及本轮检查点恢复，保留原始基准和已有成果。

## Sub-features

- `checkpoint.save`：原生保存完成、新鲜非空文件、SHA-256、前后身份回合及轮次一致。
- `checkpoint.reload`：正常换档、主菜单加载和显式完整恢复三个入口分别验收，不能相互替代。
- `checkpoint.historical`：用户授权历史阶段测试时，精确原轮次与 SHA-256 校验，确认加载后新建独立轮次；普通恢复资格不放宽。

## How to get to it (user POV)

日常调用遵循[CLI 命令手册](../../../../docs/cli.md)，使用同一 debug 会话的命名命令；MCP 仅由 CLI 在内部管理。

- 保存：`save create NAME --dry-run`，核实独立名称后明确执行 `save create NAME`。
- 正常换档：`save reload NAME --dry-run`，核实检查点后明确执行 `save reload NAME`。
- 已在主菜单：`save load NAME`；辅助入口为同会话 `save menu-load NAME`。
- 历史阶段测试：`save load NAME --source-run RUN --sha256 SHA`，来源参数与另存起点要求见[保存与恢复](../../../../docs/agent-recovery.md#检查点与测试轮次)。
- 故障后的完整重启：任务已授权时在原会话通过 `game recover NAME --dry-run` 预演，再显式调用；参数取自已确认检查点和真实 schema。

## 通过 CLI 验证

Preconditions:

- 任务明确允许对应保存或替换现场；具备本轮身份与来源记录。按[保存与恢复](../../../../docs/agent-recovery.md)逐项核对前置条件。
- 活动回合可保存，且没有阻塞保存的未决动作或推进。保存名称为本轮新名称，已确认不会覆盖任何已有文件、原件或保留成果。

- **保存前。** 记录世界身份、回合和轮次，核对新名称；按 `save create NAME` 参数传入本轮名称。
- **确认保存。** 保留工具原生完成证据；核对实际文件路径、非空大小、SHA-256、保存前后身份回合和本轮检查点记录。缺少任一项就保留未确认状态。
- **选择恢复入口。** 正常运行选 `save reload NAME`；已在主菜单选菜单加载；完整重启只用于当前任务明确允许的故障恢复。传入精确已确认检查点名称，或本次列表中的索引。
- **加载后。** 保存完整加载确认，再调用 `state overview`、`unit list`、`city list`；核对目标文件、原世界身份、回合、轮次、本地就绪和完整新快照。普通换档还需证明进程保持，而非隐式重启。
- **分别报告。** 写明实际走过哪个入口，其他入口记 `NOT_RUN`。离线可选 `tests/test_game_lifecycle.py` 和 `tests/test_checkpoint_run.py`。
- **历史来源。** 核对原文件和登记未改写，新轮次有来源记录；确认加载后另存独立起点。离线使用 `tests/test_historical_checkpoint.py` 覆盖错误原轮次／指纹、加载未知和未决门禁。

## Gotchas

- 同名文件可被覆盖；保存前必须核对名称。不能从文件存在或修改时间单独推定检查点有效。
- 保存、退出或加载未知时不重发；菜单加载失败不自动升级为完整重启。
- 重新加载原始基准建立新轮次；恢复本轮检查点延续轮次。相同文明和种子不能代替轮次核对。
- 已确认终局不能新建检查点；保留终局前检查点和终局证据，不为通过保存测试改写状态。
