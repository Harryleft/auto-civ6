# B07-01：完成显式政体选择的原生考虑确认

Status: resolved
Blocked by: none
Spec: ../spec.md

职责：在既有player-choice政府边界补齐原生considered确认，支持显式保持当前政体；保留采用／无政府／未知与一次提交语义。同公共缝隙内TDD。工作者拥有相关choice模块、测试、本ticket；父代理负责实机、progress。


## Implementation

本机 GovernmentScreen.lua 629 对 Culture 调用 SetGovernmentChangeConsidered(true)，LaunchBar.lua 69 独立读取 GovernmentChangeConsidered。显式政府选择在原 token 的一次提交中补齐该写入；保持当前政府也只调用考虑确认，不重复 RequestChangeGovernment。因为保持政府现在包含原生写入，同样要求活动本地回合与允许战略命令；不要求重新满足新政体解锁／变更资格。

准备阶段检查 setter/getter 存在且 considered 可读布尔值，缺失时零提交。分别保留政府请求返回与考虑确认 entered/returned；采用目标政府且无无政府状态，或既有严格接纳＋新无政府区间事实成立后，才独立检查考虑确认返回和原生 considered=true。考虑确认失败、静默无效或读失败都不能冒充完成；重试只读原 receipt，不重写政府请求或考虑确认。其他选择分支不执行此 setter。

TDD：新选择／保持当前政府时 considered=false，缺 API／未知 getter，setter 抛错／静默无效／返回后 getter 不可读，以及保持政府的非活动回合／战略命令禁用均先红后绿。覆盖 noop 丢回应／取消后一次 ack、零政府请求，既有目标未采用仍未知、严格无政府区间、dedication 不写该标志。player_choices／action_scope／scope_observation_recovery／server 共 587 passed；research_eligibility／policy_lua_execution 共 150 passed；compileall 与 diff --check 通过。独立审查及同一 T42 政体确认→T43／保存由主代理继续。

## Live verification

实施6131c3a／集成06263a6；737相关离线通过，compileall/diff-check通过，双独立审查无实质发现。T42原生government3/consideredfalse/changedtrue已记录；刷新MCP后#627显式保持古典共和，#628回读consideredtrue，#629确认T42→T43，#630保存gold_20261019_t043_b07_verified。无需世界重载，未改变政体或现有政策计划。
