# 世界议会：阶段、议题与投票资格

遇到议会阻塞、比较决议效果或核对禁投原因时读本页。[议会规则卡](../rules/congress.json) 保存决议、提案类型和禁投定义；本轮议题、目标、资格及请求状态需要实时读取。

## 先识别阶段，再选择议题

`Resolutions` 描述决议及两个效果方向、目标类型、时代范围和验证脚本等条件；`ResolutionEffects` 指向效果的 Modifier。目标类型是筛选入口，当前可以选择的目标仍由游戏验证。提案类型描述提案类别，不说明本轮已经出现该提案，也不证明本地玩家有资格参与。

资料中的效果文本可能引用参数或条件。需要精确数值时读取完整规则及原始记录；本期不执行验证 Lua、不完整求值 Modifier，也不从静态定义推断活动决议已经生效。决议影响区域、生产或研究时，分别查 [区域建设](districts.md)、[市政](civics.md) 与 [科技](technologies.md)，并读取当前结果。

## 资格要保留方向

原生提案界面用 `Disabled` 和 `VotingRestriction` 对应的禁投定义控制可投方向。`VotingBlockers` 的两个字段需要分别解释：

| 原始条件 | 判断 |
| --- | --- |
| `NoUpvote=0`、`NoDownvote=0` | 该禁投定义未限制两个方向；仍需核对 `Disabled` 和其他实时条件 |
| 仅 `NoUpvote=1` | 禁止支持方向，不能据此判断整项无选择 |
| 仅 `NoDownvote=1` | 禁止反对方向，不能据此判断整项无选择 |
| 两者均为 `1` | 该提案没有可投方向 |
| 资格字段／定义未知 | 保留未知，不按无选择处理 |

提案可选的条件是：没有被禁用，并且至少一个方向允许。因此「禁止支持或禁止反对」不等于「完全禁投」；完全禁投需要两个限制同时成立。遍历本轮所有提案后才能判断是否没有选择，不能让一条已知可投项掩盖另一条未知数据。

## 当前工具的三个入口

| 意图 | 现有 MCP 工具 | 核对内容 |
| --- | --- | --- |
| 只读观察 | `get_world_congress` | 原生阶段、本地请求状态、决议／提案资格、目标与外交支持；该查询不打开投票 |
| 显式进入投票 | `open_world_congress` | 先确认是可投阶段及有原生合格选择，打开后再读取议题；打开不等于投票 |
| 提交明确选择 | `queue_wc_votes` | 使用当前查询返回的决议 hash、方向、目标与票数，输入为 JSON 数组字符串；每项字段齐全 |

当前阶段为 `STAGE1`、`STAGE2`、`RESOLUTION` 或未知时，按实际阶段和请求状态处理；「议会正在开会」不足以证明本地仍需要投票。进入 `STAGE2` 后重新读取选择，不能重用上一阶段决议。

项目查询的 `proposal_status` 中，`CHOICES` 表示存在可选提案，`EMPTY` 表示已核实没有合格提案，`UNKNOWN` 表示无法确认；`EMPTY` 并不代表没有决议。当前决议投票计划不能隐式处理合格提案，被工具拒绝时保留能力缺口，显式处理后再推进。

空数组 `votes="[]"` 只适用于已核实整个原生阶段确实没有选择的情况。它不能替代进入投票、略过尚未读取的议题或处理未知资格。结果未知时先只读检查原请求，不重发投票；恢复与推进由调用方的生命周期流程负责。

## 需要深查时

```sh
python3 query.py search 禁投 --topic congress --limit 10
python3 query.py show WC_VOTING_BLOCKER_HAVENT_MET_TARGET --level evidence
```

命令从知识目录执行。[快照](../sources/snapshot.json) 的 `Resolutions`、`ResolutionEffects`、`ProposalTypes`、`VotingBlockers` 以及 `code_evidence` 中的 `congress-voting-blockers`、`congress-submit-eligibility`，保留方向限制与提案可选判断的原生依据。`congress-intro-open`、`congress-stage-flow` 用于核对进入投票与阶段流转。卡片可解释禁投定义，只有本轮原生状态能说明该限制是否适用于当前提案。
