# B11-01：绑定开放边境期限与完整原生条款

Status: implemented-pending-review
Blocked by: none
Spec: ../spec.md

在现有trade准备边界TDD修复OPEN_BORDERS的原生摘要/期限绑定及回读，不猜期限，不扩展交易政策。保留war及原有owner/no-replay。工作者拥有相关trade实现/tests和本ticket，父拥有progress/live。不得连接游戏。


## Implementation

OPEN_BORDERS 的每个方向独立调用同一 working deal／原生参与者对的四参 GetPossibleDealItems；要求匹配 subtype 的唯一可读有效摘要，以原生 Duration 编码，不猜 25、0 或速度换算。普通 OnClickAvailableAgreement 只设置 subtype／duration，因此保留 AddItem 的原生默认 value；ForType 提供可读值时核对一致，不提供时读取实际 item value（T55 #960 为 -1），不强写不需要的参数。

抽取 B08 的窄原生列表读取 helper，保留其 war 候选、WarType 和诊断。OPEN_BORDERS 写入后及 Validate 后均核对每一方向的 owner／subtype／duration／value；缺失、重复、不可读、资格失效或条款变化均不评估／发送。Agreement 现有文案附实际 duration（包括 0），便于明确判断期限反报价。

交易原始报价指纹继续在 Validate 前记录，经济项仍受原报价约束，Validate 改价不能成为“原价可接受”。仅本次 OPEN_BORDERS 准备失败且尚未发送时，可更新失败草稿的清理指纹：来源限定于同一个同步准备回调持有的自有 working deal，覆盖原生 Validate 改条款导致准备失败的情况；更新前复核 ready() 的世界／参与者／session／pending／UI 归属、同一 receipt、token／sid／target 与 sent=false。实际 abort 继续要求已有 failed／未发送／owner／session／world／精确当前草稿指纹，未放宽 B08 对其他非空草稿的保留。失败后再换 token、session、世界、参与者，出现 pending、sent=true 或外来条款，均保持未知，不关闭也不清空。

TDD：双方 25 回合和独立 17／41 回合先红后绿；原生 Validate 改期限的零发送及安全清理先红后绿；经济 Validate 改价仍是反报价的回归先红后绿；公共预览显示双方期限先红后绿。补充双方摘要缺失／歧义／不可读／无效、原生值回读、期限／value／subtype 反报价、丢回复不重复读选项或评估、失败清理归属门禁，既有普通经济及 war 回归继续通过。

离线：trade_progress、diplomacy_context_lua、diplomacy_cleanup、server、incoming_negotiation、narrate 共 476 passed；compileall 与 diff --check 通过。工作者未连接游戏；独立审查及 T55 原局准确条款的接受／拒绝核对由父代理继续，未保证 AI 接受。
