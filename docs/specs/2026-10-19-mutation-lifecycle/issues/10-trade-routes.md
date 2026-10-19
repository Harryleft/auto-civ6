# 10: 商路与商人转移确认

**What to build:** 商人建立商路或转移到指定城市时绑定原单位、始末城与结果。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 商路容量及实际路线回读与原意图一致。
- [x] 转移完成与旅行后续收益区别表达。
- [x] 延迟、丢回应、取消和重复请求不会生成第二操作。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Resolution

- 实施提交：`20c114c`（Confirm trader routes and relocation against native identities）；合入集成分支 `5ed6158` 的提交：`455df2c`。
- 商路和转移复用 ActionLifecycle 领域证据描述；原 full trader ID、动作和目标坐标属于显式意图，原生提交同时保存原始单位类型、始末城市完整 ID／owner／坐标以及路线数量与容量。
- 商路只有在原商人与精确始末城的 outgoing route 出现、实际路线数量增加一且未超过容量后报告已建立；输出明确尚未赚取后续收益。转移只在原商人到达原指定自有城市、仍无商路且路线数未改变后完成。
- MCP 保留商人的 full ID；unit_action 对已迁移所有者不先确认再分派，重复调用由原入口只确认，防止确认后再次发起操作。
- 原生资格与参数依据本机 TradeRouteChooser／TradeOriginChooser；路线关联依据 TradeSupport／TradeOverview 的 TraderUnitID 及始末城 owner／ID。未修改外交交易协议。
- 合并后的 9 组相关离线回归 **603 passed**（15.96s），含新增 **58 项**商路／转移测试；覆盖延迟、丢回应、取消、资格拒绝、错误完整身份／城市／路线、容量、原生异常和跨入口门禁。
- 6 个修改的 Python 文件语法编译与 `git diff --check` 通过。未操作真实游戏，未 push。
