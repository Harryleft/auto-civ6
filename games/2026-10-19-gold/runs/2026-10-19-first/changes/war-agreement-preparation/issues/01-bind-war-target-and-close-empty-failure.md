# B08-01：完整战争条款与失败空草稿收尾

Status: implemented-pending-review
Blocked by: none
Spec: ../spec.md

职责：在既有trade边界修复target/WarType/原生option的传播与校验，保留模型决策；修复有原始no-send receipt的原生清空草稿安全退出，完整保留ownership与no-replay。现有公共缝隙TDD。工作者拥有相关server/trade实现、测试和本ticket；父代理负责实机、防御、恢复、progress。


## Implementation

propose_trade 保留 joint_war_target 为显式 WAR_AGREEMENT 请求意图，并增加可选 joint_war_type（原生 WarType hash）。指定 WarType 但未指定目标在经济准备前拒绝。Runtime 不固定创建双方 JOINT_WAR：依据同一 working deal、双方实际 GetPossibleDealItems，只接受唯一的对方 THIRD_PARTY_WAR 或双方同 WarType 的 JOINT_WAR；多个候选要求显式选择，报告实际候选，不排序战争政策。缺 API、真正空候选与选项读取未知分别报告。

依本机 DiplomacyDealView 原生流程，Duration 取四参协议摘要，ForType 与 Parameters.WarType 取五参目标选项；摘要 IsValid=false 但 ValidationResult=MISSING_DEPENDENCY 仍按原生 UI 允许补齐条款，最终完整 deal 独立 Validate。本次原生额外探测确认 Duration25、MISSING_DEPENDENCY2；代码引用原生枚举。设置后回读完整 owner/subtype/duration/target/WarType，指纹保留目标和 WarType；counter 修改目标或类型不会自动接受。旧无绑定 AGREEMENT/JOINT_WAR 形式明确拒绝。

失败准备的 abort 继续要求原世界、原观察者／会话／参与者、receipt token/sid/target、failed=true／sent=false，且无 queued/pending 替换。除旧精确指纹路径外，仅允许原生可读且 ItemCount=0、指纹为空的当前草稿退出。不同非空或不可读草稿仍未知；不调用通用 clear、不重发原请求。失败原因与原生可选项写入 receipt，安全清理后返回给模型。

TDD：server target 传播、WarType selector、漏目标拒绝、原生 ongoing-war 条款、无绑定旧 war item、原生清空 failed draft、读取错误分类均先红后绿。补充双方新战争同类型、缺／歧义／改变选项、目标／WarType counter、lost preview不重发，以及12项空草稿 ownership 篡改拒绝。最终 trade_progress／server／diplomacy_context_lua／incoming_negotiation／incoming_final_response／diplomacy_cleanup 共 452 passed；compileall 与 diff --check 通过。独立审查与 T44 实机预览／防御／推进由主代理继续。
