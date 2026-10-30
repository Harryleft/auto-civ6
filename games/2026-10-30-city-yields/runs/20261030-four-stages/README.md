# 四阶段验证轮次

来源为原轮次 `3f9996c1b4734780b0aa1e581bde1fd7` 的 `clover_faith_20261025_t050`、`clover_faith_20261025_t100`、`clover_faith_20261025_t150` 和 `clover_faith_resume_t200`。文件和原登记均在前后重新计算指纹，完整清单见[结构化结果](reports/reading-results.json)。

主验收入口为命名 CLI `city yields`，原路径为 `city list`、`policy show`、`knowledge show POLICY_RATIONALISM --level brief`。默认简报先行判断，再展开 `--view evidence`；没有策略脚本、任意 Lua 或直接 MCP 调用。

- [预定验收口径](changes/acceptance-plan.md)
- 简报先行判断：[T50](changes/t050-before-expansion.md)、[T100](changes/t100-before-expansion.md)、[T150](changes/t150-before-expansion.md)、[T200](changes/t200-before-expansion.md)
- [验证报告](reports/validation.md)、[原生读数与指标](reports/reading-results.json)

原始 CLI JSONL、各请求完整响应、来源指纹及离线回归日志位于本轮 `evidence/`，默认不提交 Git。`live-cli`／`live-diagnostic` 记录 GameInfo 行缓存差异；`live-scalar-fix`／`live-accepted` 记录两种相邻 getter 的语义差异；`live-four-stages`、`live-native-engine`／`live-native-counts` 保留引擎条件接入前的实测与诊断；最终四阶段以 `live-complete` 为准。没有覆盖先前证据或历史存档。

最终八份起点／结果采用 `yields_tNNN_final_start/result_20261030` 独立名称；前期排查的 `yields_tNNN_*`、`yieldsv2_tNNN_*` 也保留。原生总分在历史加载后会变化，但正权重分项未变，本轮不将总分变化当作决策收益。
