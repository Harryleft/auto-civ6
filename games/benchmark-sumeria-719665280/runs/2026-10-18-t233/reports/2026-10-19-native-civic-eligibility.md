# 原生科研与市政资格验收

[规格](../changes/2026-10-19-native-civic-eligibility.md)与[工作项](../changes/2026-10-19-native-civic-eligibility-tickets.md)。集成 dfa7aff，实施50467f0，分支 codex/native-pillage；未合 main、未 push。

T195 静态 CivicPrereqs 把原生 CanProgress=false 的未来市政列为可选。查询现改用精确布尔原生资格；科技与市政 UI 选择和既有 GameCore 回退均在实际写入前重查完成状态、当前目标和资格。false、缺失、异常、非布尔均不写入，同一未完成目标仅确认。未新增回退或进度改写。

| 验证 | 结果 |
| --- | --- |
| 相关离线 | civic_progress、research_eligibility、game_state 三模块266项通过；编译与差分检查通过 |
| 双轴审查 | Standards、Spec 各0项材料性问题 |
| 同轮身份 | 1616：T196 苏美尔／吉尔伽美什，未重新加载游戏 |
| 候选与原生核对 | 1617 不再列出全球变暖减缓学、近未来治理为可选；1618 两者 CanProgress=false、全球化=true |
| 当前目标 | 1620 重复全球化返回 ALREADY_SELECTED，没有重复提交 |
| 保全 | 1615 修复前 T196 档1,245,806字节；28个原生文件归档 t196-before-civic-fix；旧存档与证据保留 |

证据位于 ~/.civ6-mcp/acceptance/20261018-turn-terminal121/ 的 native-civic-impl、native-civic-merge、native-civic-review、live/1573、1576、1615–1620。合法全球化此前已由原生允许后选择；本次未尝试选择禁止市政。整轮游戏继续，不以此次查询验证代替终局验收。
