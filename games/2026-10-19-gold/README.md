# 神级苏美尔：benchmark_gold_tun_1

完整获胜原局的[互动编年史与年表数据](runs/2026-10-20-windows-full-game-01/chronicle/README.md)已归档；[在线阅读](https://empire-chronicle.dongchao.chatgpt.site)。原始日志、逐回合OODA与三份关键存档沿同一轮次保存，独立巨作测试另行标注。

最新Windows轮次已完成：第243回合原生苏美尔外交胜利（20/20），4城66人口保留，见[终局与香港收益](runs/2026-10-20-windows-full-game-01/reports/final.md)及[原生证据](runs/2026-10-20-windows-full-game-01/reports/terminal-evidence.json)。下方第141回合宗教失败属于较早独立轮次，保留历史结果。

用户于 2026-10-19 授权从新建神级存档逐步推进至原生终局；遇到工具阻塞时按 to-spec → implement-spec 修复，保留发生回合和验证证据。

已完成：T141结束阶段，瑞典克里斯蒂娜取得宗教胜利，苏美尔失败。27个阻塞均已修复并记录实机验证；最后确认存档 gold_20261019_t141_progress。参见[终局证据](runs/2026-10-19-first/reports/terminal-evidence.json)与[原生结果截图](runs/2026-10-19-first/evidence/t141-native-defeat.png)。

复盘：[宗教失败的策略、工具缺口与下一局改法](runs/2026-10-19-first/reports/religious-defeat-postmortem.md)。

- 用户输入名称：`benchmark_gold_turn_1`；唯一匹配的实际文件名：`benchmark_gold_tun_1.Civ6Save`。
- 原生菜单元数据：T1、CIVILIZATION_SUMERIA / LEADER_GILGAMESH、DIFFICULTY_DEITY、GAMESPEED_QUICK、MAPSIZE_TINY、四叶草、RULESET_EXPANSION_2。
- 原件 SHA-256：`0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`，396382 bytes。
- 文件配置标签提取的预期 game seed：`-1896272000`；map seed：`-1896271999`。须加载后原生回读核对。
- [本轮过程](runs/2026-10-19-first/reports/progress.md)；[B01 规格](runs/2026-10-19-first/changes/gold-baseline/spec.md)。

旧不朽地图及其检查点保留；本局不使用旧局结果存档恢复。

后续工具验证：[宗教防御原局检查点实机验收](runs/2026-10-19-first/religion-native-validation-2026-10-20/acceptance.md)，T139–144，查询／宗教层移动／和平神学战／三次清除及最后charge原生消耗通过。保留原T141终局，未新开下一局。

## 2026-10-20 Windows 完整对局

用户再次授权从原始神级第1回合开始，持续推进至原生终局；后续明确授权类似阻塞自行诊断、修复、测试并继续，无需等待implement-spec。本轮独立于上述历史终局与兼容性测试，已于243回合原生外交获胜，入口见[Windows完整对局01](runs/2026-10-20-windows-full-game-01/README.md)。
