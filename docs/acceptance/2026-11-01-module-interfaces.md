# 模块接口收敛验收

五项[设计建议](../specs/2026-11-01-module-interfaces.md)全部实施。基线为 `f8da63024702ebdba0dce6a381381a1c61829070`，受测代码为其上的本轮修改；实机证据 `context.json` 保存当时源码指纹。命名 CLI、原文本结果、原生资格与确认规则保持兼容。

| 范围 | 完成结果 |
| --- | --- |
| 结果契约 | `ResultText` 由原所有者提供状态、错误码与事实，MCP 保留文本并附 `outcome`；CLI、日志、验收和回合统计共享解释。畸形显式结果不退回文案猜测。 |
| 动作所有权 | 34 个领域入口使用统一执行接口。GameState 中 `_PendingAction` 构造、`matches` 和旧 `_submit_action` 调用均为零；计划和显式多阶段延续交给 ActionLifecycle，普通确认不执行下一阶段。 |
| 查询边界 | 公共 Lua JSON 编码及世界见证集中；单位、威胁、通知、阻塞、终局和胜利的完整性由领域 Observation 解释。快照、决策和回合报告拒绝把坏行当成实体消失。 |
| 命令契约 | CLI、MCP 和调度共享操作能力与静态参数组合；包含内部兼容别名。动态门禁仍由原所有者控制，dry-run 无游戏 I/O。 |
| 知识读取 | 构建、独立 CLI、研究目录和 Runtime 共用快照 reader。Runtime 保留子进程约束和前后完整性检查；wheel 携带受信 reader，不导入配置数据目录中的代码。 |

## 离线验证：PASS

- 全仓 `uv run --locked pytest -q`：**6772 passed，4 skipped**，309.35 秒。日志：本地 `.scratch/design-all-tests-20261101.log`，并复制至本轮实机证据目录。
- 专项覆盖单次提交、丢回应、取消、重入、世界范围、多阶段延续、原生拒绝码、查询坏行、CLI 参数组合和真实隔离 stdio。最终全仓结果包含这些专项测试；不累加重复执行数。
- `compileall` 与 `git diff --check` 通过。
- `civ6 doctor`、`civ6 verify knowledge` 通过。知识源快照 SHA-256 为 `02bba8b29260d6ec4afbd34f738a92ae5503ddeeeea527813fc4599227916d29`，11 份源／规则文件与重构前逐字节一致。
- wheel 构建后解包，在原源码目录不可用的安装布局中导入 Runtime，使用外部知识数据查询成功，注册 96 个基础工具。证据：本地 `.scratch/design-wheel-final-20261101/verification.json`。
- 最后独立只读复核未发现结果语义、批量动作 metadata 或文档与实现不一致的新问题。

本轮修复了实施过程中暴露的兼容问题，包括原生拒绝码、多阶段编队入口、未知查询的旧错误信号以及 editable 安装的 reader 模块遮蔽；上述最终验证在修复后执行。

## 实机验证：PASS（限定范围）

使用用户指定的 `benchmark_turn_1`，在 macOS 的同一普通 CLI 会话完成 T1→T3。建城、生产、科研、两次移动、批量跳过和两次回合推进均确认；城市增长／工作人口／收益、公开报告与决策查询通过。新检查点保存与同轮回载通过，回载前后概览、单位和城市文本完全一致，最终 pending 为空。

具体命令、序号、指纹、两次只读验收和现场保留情况见[实机报告](../../games/benchmark-sumeria-719665280/runs/2026-11-01-module-interfaces/reports/validation.md)。原始基准指纹不变。

没有把本轮结果扩展为全路径实测：Windows 原生运行、战斗／编队、外交／议会多阶段动作、真实断线与取消、完整对局胜利均为 **NOT_RUN**。这些范围中适用的隔离回归已通过，但不替代真实引擎验收。
