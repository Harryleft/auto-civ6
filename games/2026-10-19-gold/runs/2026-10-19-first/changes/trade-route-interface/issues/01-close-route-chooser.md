# B05-01：按原生流程结束商路选择界面

Status: implemented-pending-review
Blocked by: none
Spec: ../spec.md

职责：核对原生chooser流程，TDD补齐商路请求的界面收尾；保留经济操作一次提交、范围和精确路线确认，明确处理清理失败，不清理无关对话。工作者拥有相关trader模块、测试和本ticket实施记录，主代理负责实机及progress。


## Implementation

本机原生源码：TradeRouteChooser.lua 835–836、TradeOriginChooser.lua 88–91 均在 RequestOperation 后切回 SELECTION。两种 trader 操作沿用此收尾，仅在原世界／回合仍可核对且当前仍为该操作对应 MAKE_TRADE_ROUTE／TELEPORT_TO_CITY 时切换一次；其他界面模式原样保留。

提交前核对所需接口、专属模式与 SELECTION 枚举；已知缺失时零经济提交。请求返回后记录独立 cleanupOK／cleanupError，精确路线／迁移效果仍由原有单位、城市、容量和路线记录确认。效果已确认但模式读不到或仍停原 chooser 时，返回 ROUTE_ESTABLISHED_INTERFACE_CLEANUP_UNCONFIRMED（迁移对应 TRANSFER_ESTABLISHED）并保留 pending。后续确认只读，无经济或清理重发；原生界面后来离开 chooser 后可确认原请求。

TDD：route／origin 两个 chooser 留在专属模式的公共接口用例先红后绿；补充缺接口／枚举前置拒绝、清理失败与经济效果分别报告、失败后只读确认、不改原有／新打开的无关模式、关闭 UI 不能证明商路、丢回应后不重发。test_trader_confirmation、test_trade_destinations、test_action_scope、test_server 共 336 passed；新增提交后 scope 读取涉及的 test_scope_observation_recovery 为 293 passed；compileall 与 diff --check 通过。独立审查及本轮 T31 商路→移动→T32 实机验收由主代理继续。
