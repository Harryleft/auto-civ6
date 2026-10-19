# 16: 外交关系和宣战清理归属

**What to build:** 和平、友谊、使团、使馆、同盟、谴责和各类宣战都保留原请求及完成证据。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** ready-for-agent

- [ ] 关系回读绑定双方和原世界；延迟或丢回应无重复会话／回应。
- [ ] OPEN_BORDERS 继续复用交易所有者，不重建可靠交易协议。
- [ ] 宣战视图清理归属原世界／会话，不让延迟任务污染后续会话。
- [ ] 来约接受／拒绝的直接提交 fallback 接入现有 DiplomacyProgress；已拥有的 outgoing counter 路径保持原协议。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。

实施调研补充：respond_to_deal 只在有 outgoing attempt 时归属现有所有者，无 attempt 时的 incoming 分支仍直接 SendWorkingDeal。该遗漏在本外交票修复，18 票复核全入口覆盖；不新增协议或工作票。
