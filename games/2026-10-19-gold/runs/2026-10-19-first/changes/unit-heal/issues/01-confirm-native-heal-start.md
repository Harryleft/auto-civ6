# B09-01：精确休整开始的 OPERATION 活动确认

Status: ready-for-agent
Blocked by: none
Spec: ../spec.md

依现有unit-task公共缝隙TDD修复fresh heal。仅明确本次原生任务开始可接受OPERATION且ready=false；noop维持精确HEAL，其他任务和归属规则不变。工作者拥有unit_tasks及必要测试和本ticket，父代理拥有progress和实机，不得操作游戏。实施分支合入integration最新tip后交merger与双独立审查。
