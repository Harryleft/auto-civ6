# B13-01 Confirm native local turn rejection

Status: implemented-pending-review
Dependencies: none
Spec: ../spec.md

Find and implement request-bound native local rejection confirmation, retain ambiguous pending owners, verify via existing public seams and necessary native source. Record evidence and red→green. Worker never accesses live game. Parent performs same-run recovery/reproduction and T58 save. Resolve after two independent reviews and live result.


## Initial native contract investigation

本机已安装源码：Base/Assets/UI/ActionPanel.lua 的 DoEndTurn（526–600）在当前阻塞单位分支仅 SelectNextReadyUnit；正常分支 RequestAction 后没有可供使用的本地拒绝完成回执。OnEndTurnBlockingChanged（612–654）核对当前 blocker 后检查自动推进／选择单位；OnEndTurnDirty（1006）仅 RequestRefresh。两者均不能证明本次请求已结束。WorldView/CityBannerManager.lua:2939 明示 GameCoreEventPublishComplete 是一组 gamecore 事件发布后的通知，不携带 ACTION_ENDTURN 身份或拒绝结果；WorldInput.lua:3511 的 MatchEndTurnComplete 是 PlayByCloud 提交结果。本阶段未证明普通本地拒绝的终结契约，未实现退休 owner。

## Temporary diagnostic slice (8eb37f1, superseded)

TurnProgress 每次实际提交生成 attempt_token，pending 确认沿用、get_turn_status 仅显示它。最终原生 guard 后安装本次 __civmcpTurnAttempt，记录 token／原 scope／owner／turn、entered／returned、RequestAction returnType／returnValue（nil 显式记录为字符串 nil）。events 为限长128条有序记录，字段 seq／name／phase（armed、in_call、after_return）／scope／args（type:value）；available 标识可用监听器。记录 EndTurnBlockingChanged、EndTurnDirty、UnitOperationSegmentComplete、UnitOperationsCleared、UnitOperationDeactivated、LocalPlayerTurnEnd／Begin／Unready 和 Publish／PlaybackComplete。scope 改变或下次实际请求移除旧监听器；达到上限标记 dropped 并移除；格式异常仅标 captureFailed，不能打断原请求。

该 global 的 diagnosticOnly=true。原 END_TURN|SUBMITTED 协议、确认／未知门禁、get_turn_status 观察职责和不重发保持不变。无事件／泛用 blocker／返回 false／Publish／Playback 均不被解释为拒绝证据。父代理通过普通 end_turn 单次取证后，仅只读该 global，再显式恢复原 checkpoint；诊断无用部分将在证据判定后移除。

TDD：原请求 token／identity、native返回false和in-call→after-return事件顺序先红后绿；诊断格式错误不得干扰请求先红后绿。nil／true／false 返回均保持原协议、原生抛错保留 entered 且无 returned、公开 status token 稳定且不退休／重发。end_turn_native／end_turn／turn_status 共198 passed；compileall、diff --check通过。实际拒绝修复仍待实机证据、独立审查和原局T58验证；本票未 resolved。


## Request-bound native evidence

父代理在诊断集成0be1adf以普通end_turn #1057只提交一次，#1058比对token01bdbe67f1514c5ba9e87caba668c287，证据保存 evidence/b13-native-turn-attempt.txt。RequestAction返回nil；真实同scope链为LocalPlayerTurnEnd → UnitOperationDeactivated(0,262146,-1676983098,1) → 同单位UnitOperationsCleared → LocalPlayerTurnBegin → EndTurnBlockingChanged(0,23669119) → PlaybackComplete。全程 CIVILIZATION_SUMERIA|-1896272000|0|57，最终local active/complete=true、cart ready=true、processing/sent/busy/unready=false。这是原生同回合重新开启，和只有当前 blocker 的旧观测不同。父代理随后显式恢复T57并重新确认alert；工作者未操作游戏。

## Final implementation and offline validation

移除泛事件日志、Publish／Dirty／segment监听、返回值跟踪及其临时测试。保留绑定token／owner／world／turn的有限阶段见证：原生End→AUTOMATE_EXPLORE精确Hash的完整单位Deactivated→同单位Cleared→Begin→NONE到UNITS blocker→PlaybackComplete。原生Hash在提交前由GameInfo读取；缺必需API／监听器不影响普通推进，但不能授权本地重新开启。回合／世界／玩家改变或显式unready令见证失效；首次提交仍保留pending，不在同一提交调用中退休。

仅后续显式end_turn在原turn、完整事件链、entered/returned且最终active/complete=true、processing/corebusy/sent/unready=false、CanEndTurn=true、当前UNITS blocker和同完整ID／owner的ready单位（firstReady也匹配）时确认REOPENED_LOCAL，移除监听、退休本次owner并返回“未推进”和单位待办。该调用不再提交；模型选择普通单位动作后，另一次end_turn才创建新token和请求。get_turn_status只观察；旧无token或旧诊断schema不能补证退休。未知scope回读允许后续读取恢复，观测到实际scope变化则持久失效。普通推进／终局、外交／议会／弹窗门禁保留。

TDD公共闭环先红后绿：请求1→本地唤醒→status仍pending→单位动作被挡→显式确认REOPENED_LOCAL→普通skip→新token请求2→T57→58。补充scope未知可恢复和最终proof getter期间真实scope改变保持失效的红绿用例。覆盖延迟／丢回应／取消、缺或错序事件、旧schema／错token／错完整ID和owner、ready／active／complete／processing／busy／sent／unready／CanEnd未知或错误、vanished blocker、跨turn/world后恢复原scope、原生抛错后returned缺失，均不重发且无充分证据不释放。

最终离线：turn_reopened／end_turn_native／end_turn／turn_status／server共438 passed；compileall和diff --check通过。剩余双独立审查、父代理同轮T57→T58及保存实机验证；本票未resolved。
