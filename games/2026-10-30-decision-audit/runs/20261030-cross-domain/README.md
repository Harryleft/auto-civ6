# 20261030-cross-domain

范围基线：`b619fe3fd9df59683c2f1e45cff5a621d93a4747`。测试跨越 2026-10-30 至 2026-10-31，使用同一代码工作树，原始阶段存档与来源见[验证报告](reports/validation.md)。

| 资料 | 内容 |
| --- | --- |
| [验收要求](changes/acceptance-plan.md) | 全部接口、四阶段、质量与效率边界 |
| [接口登记](changes/tool-coverage.json) | 93 个工具的参数、实现位置、审计状态与实际请求索引 |
| [领域审计](changes/domain-audit.md) | 已修正事实与剩余信息缺口 |
| [独立审查](changes/review.md) | 两轴审查、四项问题及修复复核 |
| [验证报告](reports/validation.md) | 离线／实机结果、读取量、扣款、限制与下一优先项 |
| [机器可读结果](reports/results.json) | 四阶段原生事实、请求字节、耗时、动作回执与指纹检查 |

`evidence/live-*` 保留各次 CLI 会话原始输出；代码修复后显式结束旧客户端并启动新版本，版本间结果不合并为同一代码的通过。`live-verified` 是独立审查修复后的最终四阶段只读复核；实际购买回执单独注明对应版本。不存在未决结果时才换档或关闭会话。

`changes/analyze-evidence.py` 只分析保存的 CLI 证据并核对本地指纹，不访问游戏、执行动作或替代策略。`changes/reproduce-before.py` 将固定基线的指定构造／解析函数放入隔离夹具，保留修复前失败；它不是旧完整版本的集成测试。`changes/probe-governor-gap.py` 对比固定基线与当前总督查询，不提交动作。
