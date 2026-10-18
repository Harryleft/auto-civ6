# 议会结果按原生所选标签展示

状态：ready-for-agent。起点 `96caf494`；证据 `~/.civ6-mcp/acceptance/20261018-turn121-scheduling/live/303–313`。

## Problem Statement

T117，模型对两项决议显式投 B；T118 外交胜利点 3→5。工具却将两项结果展示为 A。原生记录两项均为 Winner=0、ChosenLabel=B、RejectedLabel=A，ChosenOption 分别是禁止所选宗教及生产军事单位减费。现有展示误把 Winner 当作 A/B 数字序号，导致后续战略读取相反效果。

## Solution

会后结果使用原生明确的 ChosenLabel（A／B）解释所选效果，保留未知，不从 Winner 猜测 A／B。只读查询修正，不重投、不恢复议会，也不改变投票策略或提交路径。

## User Stories

1. 作为 Agent，我读取实际通过的方案及效果，不因 Winner 数字值错误翻转。
2. 作为 Agent，我在原生标签缺失或非法时看到未知，不得到默认 A。
3. 作为用户，当前已确认投票、消耗、外交胜利点和存档不被改变。
4. 作为用户，会后近期／非近期展示复用一致解释，会中候选继续显示 A、B 原有效果。

## Implementation Decisions

- 在现有议会原生查询、类型与展示职责内传递明确所选标签；Winner 仅作保留的原始诊断事实，不能用作方案序号。
- 原生标签仅接受确切 A 或 B；缺失、非法或读取未知时展示未知结果，不猜测、默认或补投。
- 对应效果与本次查询的 A／B 定义一致；目标仍取原生 ChosenThing。会中候选、投票预算与单次提交门禁保持现状。
- 在同一展示位置集中结果解释，避免近期／普通会后两处分叉。

## Testing Decisions

沿用已授权 GameState／FakeConn、原生 Lua 5.1 与既有展示边界。先红绿复现 Winner=0 且 ChosenLabel=B；覆盖 A、B、缺失／非法标签、不同 Winner、近期及普通会后展示、会中议题不变。只跑直接相关议会查询／展示测试，不连接游戏或全量测试。

## Out of Scope

投票行为、外交胜利算法、议会回档和重投、间谍说明及其他顾问查询改造、push。

## Further Notes

T118 原生事实已由只读查询确认；父任务可据此完成 T121 续测。修复版本在无未决操作时更新 MCP，实机回读本次已通过结果及最终保存恢复。现有存档和历史验收保留。
