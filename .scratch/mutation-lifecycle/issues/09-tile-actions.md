# 09: 建造、维修、移除与道路确认

**What to build:** 单位在指定地块完成受支持的地块操作后，核对地块与次数／单位事实。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 拒绝不合法原生操作，移除 CanStartOperation=false 仍盲发的路径。
- [x] 绑定单位和原地块；正确区分无变化、完成与未知。
- [x] 消耗后单位移除、丢回应及重复同意图不导致二次消耗。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

实现提交：`48a159d`。合并集成 `f60a7ec` 后提交为 `1a36f1a`。

- `unit_action` 的 improve／repair／remove_feature／remove_improvement／build_route 保留完整单位 ID；GameState 复用 ActionLifecycle 的待决所有者，新 `lua/tile_actions.py` 保存原单位、原地块、世界／回合／玩家、原次数、操作 hash 与事件回执。同意图重入只确认；未知时跨单位、帝国操作与 end_turn 继续受门禁。
- 五类旧 Lua builder 已删除，包含两条 CanStartOperation=false 仍盲发维修的路径。资格必须严格为 true；improve 遇受损改良明确要求显式 repair。已满足的改良／维修／移除目标返回 ALREADY_*，零提交、零消耗。道路没有显式目标类型；不猜测已有道路已满足要求，原生拒绝明确为未提交。
- 原地块效果与消费事实交叉确认。改良次数来自原生 `Improvement_ValidBuildUnits.ConsumesCharge`，保留免费改良和 ValidRepairOnly 资格约束。道路按 `Route_ValidBuildUnits`／`Routes_XP2`／`Route_ResourceCosts` 预存各合法路线费用与资源，再按实际新路线核对；普通道路 1 次、铁路 0 次加铁煤消费的非固定 index 测试均通过。
- 维修／移除没有查到可靠固定费用字段，使用精确操作结束事件和实际剩余次数；非零差要求匹配 charge 事件。输出报告实际次数差，不再声称拆除固定消耗 1 次。最后次数导致单位移除时，要求原单位 charge→0、原单位移除与原地块效果，调用后不读取已消费 userdata。清理地貌只报告地貌清除，不推算未测量产能／食物奖励。
- 保留原生可建改良候选提示，包括文明专属改良；资格未知的候选不列出。该公开入口回归先观察到缺失候选提示的失败，再补提示通过。

验证：公开 MCP／GameState 执行实际生成 Lua 5.1，仅连接与原生引擎为 adapter。领域加候选提示 **71 项通过**；合并最新集成后的相关回归 **1,138 项通过（11.88s）**，涵盖单位变换／任务／readiness、门禁／作用域、server／GameState、地块购买、Royal Society、劫掠及新合入的 choices／policies／great-person-pool／research。7 个修改的 Python 文件 AST 解析通过，`git diff --check` 通过。

故障覆盖：严格拒绝零提交、缺费用能力、完整 ID 被复用、legacy 低 ID 捕获原实例、延迟与部分地块／次数／资源证据、最后次数消失、执行后丢回应、原生抛错、取消、读回失败、世界切换锁定及提交前 scope 变化。原生调用后异常仍保持未知，不重发。

未运行实机；没有启动、换档或修改真实对局，没有 push。费用和事件签名依据本机原版 Gameplay schema、Expansion2_Routes.xml、UnitPanel／TutorialUIRoot，离线 adapter 证明协议和分支，实机时序由最终验收单列。
