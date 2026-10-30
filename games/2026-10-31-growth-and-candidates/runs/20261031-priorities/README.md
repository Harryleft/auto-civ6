# 20261031-priorities

代码基线 `c2aef68007d8efe52768ca5b3a884476b6fb0d51`，同一工作树实施与验证。本轮 CLI run `895d0574b795427489988b9bc715168b`；各历史阶段加载后建立独立保存轮次，CLI run 与检查点轮次分别记录。

- [实施范围](changes/acceptance-plan.md)
- [独立审查与修正](changes/review.md)
- [验证结果、读取量及限制](reports/validation.md)
- [结构化结果](reports/results.json)
- [原生来源指纹](reports/native-source-sha256.json)

`evidence/live/` 保存持续命名 CLI 的请求与回读。全部阶段保留起点／结果检查点，先核对原生身份和实体；无原生确认不判完成，未知请求不重发。`changes/analyze-evidence.py` 仅离线整理已保存证据和核对指纹，不访问游戏或替模型决策。
