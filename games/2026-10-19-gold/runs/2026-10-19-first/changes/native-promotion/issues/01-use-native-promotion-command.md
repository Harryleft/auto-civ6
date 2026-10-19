# B10-01：原生晋升命令与单次确认

Status: implemented-pending-review
Blocked by: none
Spec: ../spec.md

替换promote_unit直接状态修改，绑定当前原生CanStartCommand晋升选项并提交PROMOTE，保留ActionLifecycle所有权及不重发。现有公共缝隙TDD。工作者拥有unit_transforms及必要GameState/tests/架构说明和本ticket，父拥有progress与实机。不得操作游戏。


## Implementation

promote_unit 改由 InGame 原生 PROMOTE 命令执行。以 CanStartCommand(unit, PROMOTE, false, true) 的实时 PROMOTIONS 选项绑定请求的 Index，并在提交前重查资格、完整单位身份和世界／回合；命令与参数枚举不可读则零提交。删除直接 SetPromotion、ChangeStoredPromotions 和 SetDamage 路径，经验、恢复和行动力由引擎处理。

确认要求本次 receipt 的 token／意图／owner／完整 ID／文明／seed／turn 一致，entered 与 returned 均为真，且提交前未拥有、提交后已拥有精确晋升；附实际 damage／moves，不要求固定恢复量或行动力。InGame 没有 GetStoredPromotions（实机只读 #899），因此该计数仅作可选事实，无法读取时输出 unknown。丢回复、取消、原生命令抛错、迟到或不可读结果只观察，不再派发。

TDD 首条原生命令／Index 参数／丢回复用例先红后绿；补充两次资格检查期间更改选项、完整 ID 或回合的零提交红绿用例，错误 receipt Index 不确认红绿用例，以及缺失 InGame stored getter 的红绿用例。外部原生 adapter 使用实测 PROMOTE=-1259482049、PARAM_PROMOTION_TYPE=-927495623、VOLLEY Index14／Hash664752614、选项14／15；直接 mutator 一旦被调用即报错。覆盖严格资格、选项不可读／不包含请求、未知事实恢复、原生异常、取消、身份／作用域改变、token 和 no-replay；保留 upgrade／delete 与显式过期通知清理。

离线：unit_transforms、scope_observation_recovery、action_confirmation、action_scope、server 共 694 passed；compileall 与 diff --check 通过。工作者未操作游戏；独立审查、原局 T52 晋升及 T53 验证由父代理继续。
