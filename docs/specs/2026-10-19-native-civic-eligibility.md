# 原生科研与市政资格

状态：complete。工作项见[本地 tracker](2026-10-19-native-civic-eligibility-tickets.md)。

## Problem Statement

T195 的市政查询将全球变暖减缓学等未来市政列为可选，原生 CanProgress 却返回 false。查询只检查静态 CivicPrereqs，不能表达本局随机未来树。现有选择入口及 GameCore 回退没有再次核对原生资格，可能把错误候选变成越过前置的选择。此次只读查明，未选择这些不合法市政。

## Solution

可选列表以本局原生资格为准，静态前置只辅助解释。任何既有选择路径提交前均核对原生资格；拒绝与未知都零提交，不强行改变研究目标。

## User Stories

1. 作为游戏 Agent，我要看到原生允许选择的市政，以免把静态缺少前置误解为已解锁。
2. 作为游戏 Agent，我要在随机未来树中按本局资格规划，以免提前选择锁定市政。
3. 作为游戏 Agent，我要在资格未知时得到明确失败，以免把读取缺失当作允许。
4. 作为游戏 Agent，我要让原生合法但静态前置不完整的候选仍可出现，以免错过合法路线。
5. 作为游戏 Agent，我要在选择科技或市政前核对尚未完成及真实资格，以免同类入口绕过前置。
6. 作为游戏 Agent，我要让已有 GameCore 回退也执行相同资格检查，以免 UI 拒绝后越过限制。
7. 作为游戏 Agent，我要在回退前资格发生变化时停止，以免使用旧资格写入新状态。
8. 作为游戏 Agent，我要对已选中的同一目标仅确认现状，以免不必要地重复选择。
9. 作为维护者，我要保留当前工具名称与数据结构，以免为单项资格修复迁移无关领域。

## Implementation Decisions

- 市政候选调用原版 CivicsTree／CivicsChooser 使用的 GetCulture():CanProgress(index)。不能以静态前置全部满足或无行代替原生允许；原生 false 不进入 Available，未知明确失败，不编造随机前置。
- 科技候选沿用 CanResearch；静态前置只用于已有解释，不模拟规则或未来树。
- 现有原生 UI 选择及 GameCore 兼容回退，在各自实际写入前检查 HasTech／HasCivic、当前目标与 CanResearch／CanProgress。已选中且未完成的同一目标只确认；新目标只有精确 true 才能写入，缺失、异常和非布尔值都拒绝。
- 不用直接设定或增加科研／文化进度使选择通过；不放宽原生资格。候选与两条写路径使用一致判断语义，保留完成／拒绝／未知区别。
- 本次不迁移整个选择生命周期，不新增自动重试、后台观察、策略或新工具。GameCore 回退的全面替换另行处理，本次只堵住资格绕过。

## Testing Decisions

沿用已确认的 GameState／FakeConn、内存 MCP、执行 Lua5.1 边界，不连接游戏。验证公共候选与选择行为：静态满足但原生 false、未来无静态前置但原生 false、合法候选、未知资格、UI 与回退零提交拒绝、回退时资格变化、当前目标只确认、已完成拒绝。只运行直接相关模块、编译与差分检查。

## Out of Scope

科技树重建、未来前置猜测、新回退路径、研究选择 ActionLifecycle 全面迁移、其他游戏动作、全量测试、push 或合并 main。

## Further Notes

现场证据：1573 查询列为可选，1576 原生 GlobalWarmingMitigation=false、NearFutureGovernance=false、Globalization=false，RapidDeployment=true。游戏同轮 T195；没有调用不合法目标。修复后实机查询应排除错误候选，并继续普通合法研究，不为验证消耗解锁或改写进度。
