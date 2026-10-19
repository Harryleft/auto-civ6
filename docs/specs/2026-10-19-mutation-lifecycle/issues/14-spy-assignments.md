# 14: 间谍旅行与任务确认

**What to build:** 绑定原间谍全 ID、目标和任务，确认原生安排是否生效。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 旅行安排与到达、任务安排与最终成败分开表达。
- [x] 延迟、取消、丢回应只确认原安排；不覆盖 escape 所有者。
- [x] 能力缺失和陈旧单位身份在提交前拒绝。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

实现提交：`98d0885`；合入集成 `b61e3c7` 的合并提交：`3d86b49`。

- `spy_action` 的 travel／mission 不再截断单位 ID。GameState 用既有 ActionLifecycle 保存间谍、任务、目标坐标和原世界／回合／玩家；Lua 捕获原完整间谍身份、目标城市完整 ID／owner／中心位置、目标地块、原生操作 hash／index。旧的直接发送 travel／mission builder 已删除。
- 原生选择提交前要求空闲间谍、已知 GetSpyOperation／EndTurn、精确目标城市和严格资格。新逻辑直接索引 GameInfo.UnitOperations，旅行真实类型为 `UNITOPERATION_SPY_TRAVEL_NEW_CITY`；不依赖会返回空集的迭代器。公开 get_spies 的当前任务名称也改为原版使用的直接索引，避免实际执行中仍展示 none。
- 使用原版 PARAM_X/Y。旅行要求城市中心并核对 native CanStartOperation(spy, hash, plot, params)。任务要求间谍已在该城市，按原版 CanStartOperation(spy, hash, cityPlot, false, true) 的 PLOTS 核对明确请求的区块；城市中心不是合法任务区块时，零提交并返回原生候选坐标，不替用户挑区块。没有 PLOTS 的任务使用城市中心。
- 仅 exact UnitOperationStarted(owner, fullID, hash) 加 fresh GetSpyOperation 对应的 index/hash、future GetSpyOperationEndTurn 以及原目标城市／地块身份才确认 `SPY_ASSIGNMENT_STARTED`。任务额外核对间谍实际位于请求的任务区块；旅行不虚构目的地 getter，也不凭 -9999 坐标单独认定完成。输出只报告指派启动及原生结束回合，不报告抵达、任务成功或奖励。
- 闲置间谍已经位于目的城市时返回 SPY_ALREADY_AT_DESTINATION，零提交。已有任务不能被新请求覆盖。丢回应／取消后相同意图只确认原安排；未知时保留全局变更门禁。确认启动就释放门禁，不等待多回合结算。现有 escape 实现与所有者保持原状，本路径从不读取 GetNextEscapingSpyID。
- MCP 文案同步准确的目标坐标含义，移除旧的“只可去自己城市／城邦”和“回合结束即抵达”承诺；目的地合法性由原生资格决定。

原版依据：`Base/Assets/UI/Choosers/EspionageChooser.lua:199–230,371–374,612–616` 的任务候选、PARAM_X/Y 和旅行资格；同文件 `:170–178` 的非同盟大文明城市候选，`:403–405` 的旅行加建立情报耗时；`PartialScreens/EspionageOverview.lua:659–684` 的 operation index、直接 GameInfo lookup 和 EndTurn，`:993` 的 UnitOperationStarted；`Gameplay/Data/UnitOperations.xml:114` 的旅行类型。

验证：间谍指派通过公开 MCP／GameState 执行真实 Lua 5.1，模拟边界仅为原生引擎／连接。先复现“执行后丢回应只返回 Error 并丢失待决”，再按指派、no-op、明确区块候选与当前任务查询逐项红绿实施。新增领域 **54 项通过**，连同原 escape 测试 **106 项通过**。公开 GameState 的组合测试使用真实 Lua 指派和既有原生回合协议 adapter，在间谍仍为 -9999、任务结束回合 58 时成功从 50 推进到 51，且只提交一次原指派与一次回合推进。

合并最新集成后，间谍、escape、MCP／GameState、未决调度／scope、回合／状态、地块购买与新合入宗教计划相关回归 **834 项通过（7.49s）**；7 个修改的 Python 文件 AST 解析及 `git diff --check` 通过。覆盖完整 ID 复用、错误目标/任务/事件、缺能力、资格拒绝、延迟、部分证据、丢回应、原生抛错、取消、读回异常、世界锁定、提交前 scope 变化和跨域门禁。未实机、未启动或修改真实对局、未 push。
