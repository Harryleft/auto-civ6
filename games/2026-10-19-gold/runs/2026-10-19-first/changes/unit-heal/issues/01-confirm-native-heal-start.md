# B09-01：精确休整开始的 OPERATION 活动确认

Status: implemented-pending-review
Blocked by: none
Spec: ../spec.md

依现有unit-task公共缝隙TDD修复fresh heal。仅明确本次原生任务开始可接受OPERATION且ready=false；noop维持精确HEAL，其他任务和归属规则不变。工作者拥有unit_tasks及必要测试和本ticket，父代理拥有progress和实机，不得操作游戏。实施分支合入integration最新tip后交merger与双独立审查。


## Implementation

仅为 freshly submitted heal 扩展现有 alert 的活动兼容：保留 entered／returned／精确 started、scope、完整单位身份、token 和原生 ready=false，当前活动可以是 HEAL 或可读数值枚举 ACTIVITY_OPERATION。ALREADY_HEALING 继续要求原目标 HEAL 活动，ALREADY_HEALTHY 继续回读完整健康事实；sleep 和 automate 的原有契约不变。成功仍描述任务开始，不声称 HP 增加。

TDD：原生 T48 fullID262146、HEAL Hash2126026491／Index13、OPERATION1225574625、ready=false、damage15 用例先红后绿。heal 外部 adapter 使用 OPERATION，让既有丢回应／取消／延迟测试覆盖实测状态；复用 alert 的负向测试验证缺事件／错操作／错完整 ID 和严格 no-op，补充 heal ready=true／未知、活动未知／错误、缺 OPERATION 枚举、世界／回合／token／完整单位改变均保持未知且只提交一次。真实用例确认 damage15 未被测试动作修改。

unit_tasks／unit_readiness／action_scope／server 共 342 passed；compileall 和 diff --check 通过。独立审查及同轮 T48→T49 实机验收由父代理继续；工作者未操作游戏。
