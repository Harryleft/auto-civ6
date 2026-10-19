# B13-01 Confirm native local turn rejection

Status: diagnostic-ready
Dependencies: none
Spec: ../spec.md

Find and implement request-bound native local rejection confirmation, retain ambiguous pending owners, verify via existing public seams and necessary native source. Record evidence and red→green. Worker never accesses live game. Parent performs same-run recovery/reproduction and T58 save. Resolve after two independent reviews and live result.


## Native contract investigation

本机已安装源码：Base/Assets/UI/ActionPanel.lua 的 DoEndTurn（526–600）在当前阻塞单位分支仅 SelectNextReadyUnit；正常分支 RequestAction 后没有可供使用的本地拒绝完成回执。OnEndTurnBlockingChanged（612–654）核对当前 blocker 后检查自动推进／选择单位；OnEndTurnDirty（1006）仅 RequestRefresh。两者均不能证明本次请求已结束。WorldView/CityBannerManager.lua:2939 明示 GameCoreEventPublishComplete 是一组 gamecore 事件发布后的通知，不携带 ACTION_ENDTURN 身份或拒绝结果；WorldInput.lua:3511 的 MatchEndTurnComplete 是 PlayByCloud 提交结果。本阶段未证明普通本地拒绝的终结契约，未实现退休 owner。

## Temporary diagnostic slice

TurnProgress 每次实际提交生成 attempt_token，pending 确认沿用、get_turn_status 仅显示它。最终原生 guard 后安装本次 __civmcpTurnAttempt，记录 token／原 scope／owner／turn、entered／returned、RequestAction returnType／returnValue（nil 显式记录为字符串 nil）。events 为限长128条有序记录，字段 seq／name／phase（armed、in_call、after_return）／scope／args（type:value）；available 标识可用监听器。记录 EndTurnBlockingChanged、EndTurnDirty、UnitOperationSegmentComplete、UnitOperationsCleared、UnitOperationDeactivated、LocalPlayerTurnEnd／Begin／Unready 和 Publish／PlaybackComplete。scope 改变或下次实际请求移除旧监听器；达到上限标记 dropped 并移除；格式异常仅标 captureFailed，不能打断原请求。

该 global 的 diagnosticOnly=true。原 END_TURN|SUBMITTED 协议、确认／未知门禁、get_turn_status 观察职责和不重发保持不变。无事件／泛用 blocker／返回 false／Publish／Playback 均不被解释为拒绝证据。父代理通过普通 end_turn 单次取证后，仅只读该 global，再显式恢复原 checkpoint；诊断无用部分将在证据判定后移除。

TDD：原请求 token／identity、native返回false和in-call→after-return事件顺序先红后绿；诊断格式错误不得干扰请求先红后绿。nil／true／false 返回均保持原协议、原生抛错保留 entered 且无 returned、公开 status token 稳定且不退休／重发。end_turn_native／end_turn／turn_status 共198 passed；compileall、diff --check通过。实际拒绝修复仍待实机证据、独立审查和原局T58验证；本票未 resolved。
