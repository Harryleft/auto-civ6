# 终局续测：当前外交会话与交易缓存分离

标签 ready-for-agent；本地 Markdown tracker；基点 `7c1e1bc`，沿用 `codex/architecture-deepening`。

## Problem Statement

第 138 回合瑞典请求建立大使馆，两个读取工具却附带第 128 回合已拒绝的巨作交易，并建议使用交易回应。普通外交 POSITIVE 实际得到“噢，今天真开心！”，再次查询仍附旧交易。模型可能回应错误的议题，甚至接受旧工作缓存。

## Solution

先判断当前可验证的外交上下文，再读取相应交易。普通对话不附缓存；只有可关联的当前 incoming 交易视图才展示条目并允许回应。无法关联时明确 UNKNOWN，允许重新查询，不猜测、默认关闭或提交。

## User Stories

1. 作为玩家，我希望大使馆请求只展示当前对话，以便正确回应。
2. 作为玩家，我希望拒绝后的旧交易不重新出现，以便不重复谈判。
3. 作为玩家，我希望合法当前交易仍能读取，以便修复不阻碍正常流程。
4. 作为玩家，我希望视图过渡时明确未知，以便不将其当作没有待办。
5. 作为玩家，我希望多会话时不把同一全局文案复制给所有玩家，以便不混淆发言方。
6. 作为玩家，我希望会话参与方必须匹配，以便旧数据不能冒充当前对方。
7. 作为玩家，我希望我方发出的草稿不被当作对方提案，以便不错误接受。
8. 作为玩家，我希望缓存即使非空且有效也不能单独授权回应，以便保护当前意图。
9. 作为玩家，我希望无法确认的交易回应在提交前拒绝，以便不发送旧工作交易。
10. 作为维护者，我希望两个读取入口与交易回应复用同一解释，以便资格判断一致。
11. 作为维护者，我希望查询没有清缓存、关会话或发送等副作用，以便反复读取安全。
12. 作为测试者，我希望正常 DealView 与 ActionView 根同时可见仍被支持，以便遵守本机原生布局。
13. 作为测试者，我希望 UNKNOWN 在解析和叙述中保留，以便不会静默变为空列表。
14. 作为使用者，我希望同一轮实机验证后继续到终局，并保留所有检查点，以便不丢失此前进度。

## Implementation Decisions

- 统一解释 native open session、会话参与方、根视图与当前 incoming 工作数据；在 Lua 事实与公开查询结果中保留确定性和原因，不新增后台监听、事件历史缓存或通用工作流。
- 仅在去重后唯一 open session、GetSessionInfo 双方恰为本方与该对方时，将全局 UI 文案关联给该会话。多会话或参与方未知／冲突不得扇出文案及按钮；明确 UNKNOWN。
- DealView 根可见优先；正常交易时 ActionView 根可能同时可见，不能据此排除。对话容器与根可见性须无矛盾。只有对方发起、可验证的当前交易视图，且原生 incoming 数据非空并 IsValid，才能展示该对方条目。
- DealView 明确隐藏、ActionView 根及对话有效可见时，展示普通外交对话，不读取工作交易。隐藏父视图下的子控件自身 hidden=false 不能授权读取。
- 本方发起、视图缺失／异常／过渡、多会话、无效 incoming 等状态明确区分；只有无 open session 才可表示没有会话。两个读取入口和 narration 不能吞掉未知行后宣称没有待办。
- respond_to_trade 在任何 SendWorkingDeal 或会话清理前应用同一当前提案资格规则，目标不匹配或未知则零提交。正常当前提案仍由模型显式决定 accept／reject；不得精确称为普通交易，因为原生 DealView 也用于 MAKE_DEMAND。
- 不猜测 SessionInfo 中未由本机官方源码证实的 type/state 字段，不使用任意 UI selected player 冒充 active sid。不通过 ClearWorkingDeal 消灭问题，不自动回应或切换会话。

## Testing Decisions

沿用已确认的 GameState／FakeConn 和现有 Lua 5.1 隔离执行边界，不连接游戏。执行真实生成 Lua，验证公开查询的状态、文本、条目，以及响应请求计数；不复制分类算法。

- 大使馆／普通对话附带有效旧缓存：两入口不读取／展示旧条目，交易响应零提交。
- 合法当前 incoming：DealView 与 ActionView 根同时可见、对话容器隐藏，参与方与方向一致，正常查询和一次显式回应。
- 交易结束转回对话但缓存保留；根隐藏而子文本可见；多个 session、重复 sid、双方不符／缺失、本方发起、视图读取失败、交易无效／缺失。
- NONE、DIALOGUE、当前交易及 UNKNOWN 的公开语义；无全局文案扇出，无查询副作用，无自动回应。所有响应拒绝发生在发送及关闭前。
- 全量离线、编译、diff、独立 Standards／Spec 审查后实机验证。当前第 140 回合检查点及后续进度保留；正常恢复菜单优先，诊断重放另计。验证当前对话、正常交易与读取未知边界；不能用旧缓存制造成功。

## Out of Scope

全部外交动作生命周期重构、自动谈判、缓存清理、不可验证的内部字段、后台观测器、城市攻击目标说明、晋升兼容实现、push。

## Further Notes

本机官方分类来自 DiplomacyStatement 的 SessionID 与 StatementType；当前未发现可信跨上下文 active-session getter。采用保守可见视图关联，覆盖不了的情况返回未知。

第 145 回合无未决时的实机纯读：DealView／ActionView 根均 hidden=true，ConversationContainer 自身 hidden=false；所有 open sid 均 nil，但瑞典 incoming 缓存仍 count=3、IsValid=true。根路径与 IsHidden 均已实机确认可读。证据及官方源码指纹位于 `~/.civ6-mcp/acceptance/20261018-turn-terminal/diplomacy-session-research.md`、`diplomacy-session-source-evidence.json`、`diplomacy-view-probe-request.json` 和 `session.jsonl`。
