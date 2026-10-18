# 原生科研与市政资格 tracker

[规格](2026-10-19-native-civic-eligibility.md)，ready-for-agent。集成分支 codex/native-pillage。

| ID | 工作项 | 依赖 | 状态 | 验收 |
| --- | --- | --- | --- | --- |
| C1 | 原生候选资格及所有既有选择路径前置拒绝 | 无 | complete | 相关公共入口和 Lua 回归，无猜测解锁 |
| C2 | 独立合入、相关模块验证、双轴审查 | C1 | complete | 合入集成分支，修复发现，保留旧修改 |
| C3 | 同轮实机查询及合法路线续行 | C2 | complete | 不出现已证实非法候选，普通研究继续，保留存档 |

父任务拥有规格、tracker、验收与游戏；实施者只改职责内代码及测试，使用独立 WT，不实机、不 push，不撤销他人修改。测试边界沿用此前明确确认。

C1：50467f0；C2：独立 merge dfa7aff，3 模块 266 项通过，编译与差分检查通过，双轴审查无材料性问题。C3：同一 T196 世界 1616–1620，查询排除已证实 false 的未来市政，原生再次核对 false，当前全球化返回 ALREADY_SELECTED 零重复提交。详见[验收](../acceptance/2026-10-19-native-civic-eligibility.md)。
