# 20261030 四阶段规则知识验证

新增 `knowledge show ID --level brief`，完整保留规则、条件及来源，通过公共字段和父级 ID 分组减少重复；`civilization abilities` 默认读取 brief。完整 rule／evidence 继续可用。

| 项目 | 结果 |
| --- | --- |
| 离线 | 1,857 张卡精确还原；原 snapshot 和十份规则 JSON 字节不变；CLI、可移植知识包及真实 stdio 均通过 |
| 实机 | 历史四叶草 T50／T100／T150／T200 均加载确认；11 组精简先行与完整展开对照；四个实际决策动作有回读 |
| 读取量 | 同卡知识 text 响应 211,789 → 79,326 UTF-8 bytes，减少 62.54% |
| 质量范围 | 覆盖条件无遗漏、完整展开未改变判断；未测完整回合推进、后续经济收益或长期胜率 |
| 收尾 | 八份独立起点／结果存档均确认；原历史文件指纹不变；无未决，关闭本轮启动的游戏和 CLI |

来源为[四叶草信仰对局](../../../2026-10-25-clover-faith/README.md)，原登记轮次 `3f9996c1b4734780b0aa1e581bde1fd7`；四份来源文件与原登记一致。基础原件 `benchmark_gold_tun_1.Civ6Save` 的 SHA-256 为 `0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`，本轮没有覆盖原件。

详细结论见[验证报告](reports/validation.md)，逐卡数据、原来源与八份新检查点见[指标 JSON](reports/reading-metrics.json)。[验收约定](changes/acceptance-plan.md)在实测前形成；各阶段的 `changes/tNNN-before-full-audit.md` 与 `tNNN-full-audit.md` 保留判断顺序。

本地原始证据位于 `evidence/`，不纳入 Git：`live-cli*` 与 `live-four-stages` 为启动故障前后证据，`live-stage-validation/debug.jsonl` 是实际四阶段单会话；`final-knowledge-tests`、`final-checkpoint-tests`、`final-verify-knowledge` 是最终离线验收。
