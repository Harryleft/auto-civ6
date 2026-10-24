# Civ6 验证功能地图

先选择本次范围，再读取对应配方。每个文件中的功能 ID 分别验收；一项通过不代替其他入口。本文档面向 MCP 和 CLI 的实际使用路径，离线夹具只证明隔离场景中的行为。

| 功能 | 配方 | 运行前提 |
| --- | --- | --- |
| `knowledge.cli`、`knowledge.mcp` | [知识查询](knowledge.md) | 项目依赖及当前知识包；无需游戏 |
| `state.overview`、`state.entities` | [真实状态](state.md) | 已授权查询的正确对局、唯一控制客户端 |
| `action.move`、`action.pending` | [动作与确认](actions.md) | 已授权动作、本次查询所得单位和地块 |
| `turn.advance`、`turn.wait` | [推进回合](turns.md) | 已授权推进、本地可行动回合 |
| `checkpoint.save`、`checkpoint.reload` | [保存恢复](checkpoints.md) | 明确的保存／换档授权及本轮归属 |

运行约定：

- 离线操作按 [SKILL.md](../SKILL.md) 的 Launch、Doctor、Evidence 和 Cleanup 执行。
- 新实机测试必须使用当前任务指定的[原始基准](../../../../fixtures/saves/README.md)；续测只用当前轮次已确认检查点。已有现场需要替换时核对任务授权。
- 实机先调用 `get_game_overview`；单位、城市、玩家 ID 与坐标取自当前查询，不照抄旧证据。`get_turn_status` 是只读诊断，不能解除未决门禁。
- 同一游戏仅一个控制客户端。可并行的代码检查与实机控制分开；worktree 不提供游戏隔离。
- 前提不足时保留未满足条件和原始返回，判 `BLOCKED`；未选入口记 `NOT_RUN`。不缩减预期行为换取通过。
- 地图中的离线测试不启动游戏；实机结果和离线测试分别报告。基准身份、测试轮次、原生证据的术语见[术语表](../../../../GLOSSARY.md)。
