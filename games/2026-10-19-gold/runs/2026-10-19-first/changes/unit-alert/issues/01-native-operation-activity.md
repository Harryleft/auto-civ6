# B03-01：原生警戒 OPERATION 活动确认

Status: implemented-pending-review
Blocked by: none
Spec: ../spec.md

职责：最小修复任务开始确认与已有目标状态的区别；公共 GameState/MCP seam 内 TDD，保留严格活动／身份／事件／未知保护。父代理负责实机。


## Implementation

真实 T16 证据 `evidence/b03-native-alert.txt`：`entered/returned/started=true`，fullID `262146`，警戒 Hash `-448283625`／Index `54`，原生非就绪且当前活动 `1225574625`（ACTIVITY_OPERATION），目标 SENTRY 为 `-793848223`。

确认分离为两类：ALREADY 仍须精确回读原目标活动；新警戒在精确原生开始、完整范围、单次返回、非就绪均满足后，允许原目标活动或本机枚举 ACTIVITY_OPERATION。仅警戒使用此兼容，heal／sleep 活动要求保持。该结果确认任务开始，不推断未来警戒效果。

TDD：真实 T16 身份／Hash／活动值的公共 MCP 用例先失败后通过；补充 OPERATION 无／错事件、错 fullID 不确认，ALREADY 从 SENTRY 变 OPERATION 不确认，heal／sleep 不放宽。alert 默认外部原生 adapter 使用 OPERATION，使既有丢回应、取消、延迟及范围测试覆盖本机状态。相关 `test_unit_tasks.py`、`test_unit_readiness.py`、`test_action_scope.py`、`test_server.py`：314 passed；compileall、diff --check 通过。独立审查与精确 T16 检查点实机验收待父代理完成。
