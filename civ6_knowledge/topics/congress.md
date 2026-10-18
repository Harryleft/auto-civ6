# 世界议会：议题与投票资格

比较决议、识别阶段或核对禁投原因时读取。

## 议题与阶段

`Resolutions` 保存两个效果方向、目标类型、时代范围和验证条件；`ResolutionEffects` 指向效果 Modifier。提案类型仅说明类别，不代表本轮已有提案或玩家有资格参与。

依据原生阶段与本地请求状态判断是否需要投票。进入 `STAGE2` 后重新读取选择，不复用上一阶段决议；静态效果定义也不能证明该效果正在生效。

## 提案资格

提案可投需要未被 `Disabled` 禁用，且至少一个方向允许。`VotingRestriction` 对应 `VotingBlockers`：

| `NoUpvote` | `NoDownvote` | 该定义的限制 |
| --- | --- | --- |
| 0 | 0 | 两个方向均未限制 |
| 1 | 0 | 仅限制支持 |
| 0 | 1 | 仅限制反对 |
| 1 | 1 | 没有可投方向 |

任一字段或定义未知时保留未知。核对所有提案后再判断没有选择，不能用已知可投项掩盖其他未知资格。项目查询的 `proposal_status`：`CHOICES` 为存在可投提案，`EMPTY` 为已核实无合格提案，`UNKNOWN` 为未核实。`EMPTY` 仍可能存在决议；无选择须同时核对本阶段提案与决议。

## 本局核对

- `get_world_congress`：只读获取阶段、请求状态、议题、目标、资格与外交支持。
- `open_world_congress`：在可投阶段显式打开投票界面。
- `queue_wc_votes`：提交明确票单。现有决议计划不能隐式处理合格提案；参数、空计划及确认时序按工具契约执行。

## 继续查阅

示例 ID：`WC_VOTING_BLOCKER_HAVENT_MET_TARGET`，按[统一查询命令](../README.md#查询)展开。议题涉及区域或研究时，再读[区域建设](districts.md)、[市政](civics.md)或[科技](technologies.md)。

证据表：`Resolutions`、`ResolutionEffects`、`ProposalTypes`、`VotingBlockers`；原生片段 `congress-voting-blockers`、`congress-submit-eligibility`、`congress-intro-open`、`congress-stage-flow`。
