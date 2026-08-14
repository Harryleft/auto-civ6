# Civ 6 智能体回合规则

本文描述 DSH/civ6-belief-engine 会话中的查询顺序、治理门禁、日志和周期检查。MCP 适配层仍使用 `civ_mcp` 与 `civ-mcp`。机制细节见 [信念引擎](belief-engine.md) 与 [治理系统](governance-system.md)。

## 信息边界与坐标

智能体只能知道明确查询过的信息。人类能被动看到小地图、分数、宗教镜头、单位血条和迷雾边界；智能体必须通过工具主动取得对应数据。

六边形坐标使用 `(X, Y)`：Y 越大，画面位置越靠南；X 越大，位置越靠东。从 `(9,24)` 到 `(9,26)` 是向南，不是向北。

## 第一回合

进入游戏后的第一回合先完成三件事：

1. 读取文明的独特能力、单位和建筑，明确文明定位。
2. 找出独特单位所需科技/市政，规划研究路线。
3. 建立一个可修正的胜利路线假设，不要把它当作定论。

早期选择会在 20、40、60 回合后产生复利。侦察、平民护送、城市数量和圣地准备都不能被“以后再说”替代。

## 每回合强制顺序

1. **唯一入口：**调用 `get_game_overview`，阅读返回的 `RUNTIME POLICY`。不要从本文或旧记忆推断 `CIV_MCP_BELIEF_MODE`。
2. **先查蛮族：**调用 `get_barbarian_overview`。它返回已揭示的营地、当前可见的蛮族军事单位，以及到己方城市/军队的距离；空结果不代表未探索区域安全。
3. **先断源：**营地是持续刷兵源。近城营地（到最近城市 ≤10 格）或正在威胁城市的蛮族单位，必须在本回合优先分配军事单位处理；有可行动兵力时不要先扩张或直接结束回合。
4. **量化战斗：**攻击前调用 `get_combat_estimate`；近战负责接战/补刀，远程优先安全集火。攻击/移动在 `enforce` 下若返回 `BELIEF_GATE_REQUIRED`，按返回的精确 action intent 路由后只重试一次。
5. **常规查询与执行：**再按决策需要调用 `get_units`、目标区域的 `get_map_area`、`get_cities`，执行动作并确认结果。
6. **复核：**只有出现重要新证据、动作结果或上下文恢复后才调用 `get_turn_brief`。
7. **结束回合：**调用 `skip_remaining_units`，再调用 `end_turn`。服务器的蛮族告警、游戏规则和阻塞项高于旧状态或提示词。

## 信念与治理规则

- `CIV_MCP_BELIEF_MODE` 是唯一事实来源；`get_game_overview` 报告当前能力。
- 成功查询自动生成规范化 Observation；不要把原始工具结果复制到 Belief 或 diary。
- 只有增加解释、可证伪预测、计划或未来承诺时，才创建/修改 Belief、Hypothesis、Prediction 或 Plan。
- `get_governance_brief` 是刷新/恢复工具，不是每回合 `get_game_overview` 之后的例行重复调用。
- `enforce` 模式下，战略、高影响、不可逆、建国、占城和宣战意图需要遵守治理/路由要求；详细状态机见 [治理系统](governance-system.md)。
- 原始工具历史归 DSH transcript/telemetry；信念引擎只保留规范化事实、指纹及决策/动作关联。

## Diary 记录

Diary 是跨会话恢复依据。反思必须在 AI 处理前记录本回合实际观察和动作；`end_turn` 之后才出现的外交提议、AI 单位和事件，写入下一回合。

每回合五个字段都必须非空：

- `tactical`：具体单位、地块和结果。
- `strategic`：城市数、产出、对手态势和胜利路线数字。
- `tooling`：工具问题；没有问题写 `No issues`。
- `planning`：未来 5–10 回合的具体生产、移动和研究计划。
- `hypothesis`：可检验预测、时间点和主要风险。

## 周期检查

### 大约每 10 回合

检查 `get_empire_resources`、城市数量、金币/信仰、闲置商路、政府等级、时代分数和伟人。多余奢侈品先用 `propose_trade(mode="test")` 检查报价，再决定是否交易。

### 大约每 20 回合

检查 `get_diplomacy`、`get_victory_progress` 和 `get_religion_spread`。宗教胜利和对手外交胜利不能依赖通知被动发现。

### 大约每 30 回合

检查 `get_strategic_map`、`get_global_settle_advisor`、最佳城市的奇观选项和当前胜利路线。确认是否仍在使用当前文明的独特能力。

## 相关文档

- [策略与胜利路线](agent-strategy.md)
- [工具与动作参考](agent-tools.md)
- [游戏恢复](agent-recovery.md)
- [信念引擎](belief-engine.md)
- [治理系统](governance-system.md)
- 返回 [AGENTS.md](../AGENTS.md)
