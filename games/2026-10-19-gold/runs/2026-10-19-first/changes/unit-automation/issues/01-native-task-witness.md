# B02-01：确认自动探索的实际原生任务开始

Status: resolved
Blocked by: none
Spec: ../spec.md

职责：调查任务事件契约，最小修改任务确认与相关离线测试，保留单次提交／未知门禁／精确范围。父代理负责实机观测与续局。先取得实际事件证据再决定修复，不根据位移推断 started。


## Implementation

原生 T11 证据 `evidence/b02-native-events.txt`：仅收到 `UnitOperationAdded(0,196608,0,1)`，未收到 Started；同一运行环境的 UnitOperations 第 1 行是自动探索，Hash 为 `-1676983098`。Added 第四参数按请求行 Index 匹配；第三参数不作任务身份依据。Started 保留原生 UI 使用的 Hash 匹配。完整单位 ID、owner、世界、回合、单次提交与 returned 门禁保留。

本机 UnitPanel 的第四参 `true` 是宽松 UI 可见性检查；提交前改用 `false` 检查真实可执行资格。自动探索缺失 Added 观察器或有效 Index 时零提交拒绝。

TDD：UI 可见但不可执行、真实 T11 四参事件、缺观察器／Index 均先红后绿。公共入口覆盖错字段、错任务、错 owner／完整 ID、换世界／回合、延迟事件、丢回应与取消后不重放。直接相关离线回归：`test_unit_tasks.py`、`test_unit_readiness.py`、`test_action_scope.py`、`test_server.py` 共 307 passed；compileall 与 diff --check 通过。独立审查和精确 T11 检查点实机验证待父代理完成。

实机及审查：T11 单次 automate 原生开始确认、T11→T12、独立 checkpoint 保存均通过（本轮工具日志 #130–135）；Standards / Spec 无实质发现。
