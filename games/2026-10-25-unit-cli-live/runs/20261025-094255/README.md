# T1—T50 CLI 实机验收

2026-10-25，神级、极小、四叶草，苏美尔／吉尔伽美什，seed `-1896272000`。原始基准为 `fixtures/saves/benchmark_gold_tun_1.Civ6Save`，SHA-256 `0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`，验收后指纹未变。

主对局到达 T50 时为 2 城、12 人口、5 单位、178.2 金币，科技 14.3／回合、文化 17.1／回合。原生总分当时为 94，重载同一 T50 检查点后为 97，全部分项向量保持不变。游戏最终停留在恢复后的 T50，侦察兵及全部 5 个原单位均在，无未决请求。

| 内容 | 记录 |
| --- | --- |
| 成功操作、拒绝路径、缺陷及限制 | [实机报告](reports/live-validation.md)、[能力结果 JSON](reports/capability-results.json) |
| 全部命令与回执索引 | [命令审计 JSON](reports/command-audit.json) |
| 逐回合耗时 | [耗时 JSON](reports/turn-timing.json) |
| 科技／市政搜索和全部筛选 | [检索 JSON](reports/research-search.json) |
| 原始基准及 9 个独立保存文件 | [存档清单 JSON](reports/saves.json) |

原始证据位于本轮 `evidence/relay*/requests/*.json`、`stdout.jsonl`、`cli/` 和 `evidence/saves/`，本地保留，不纳入 Git。relay-v2 完成全部 49 次主对局推进；relay-v3/v4 仅补做恢复后只读验收。服务更换前均正常退出并核实无 pending；未关闭或重启游戏进程。
