# 20261031 完整收尾轮次

实施基线：`c9c79aa5c02b244fc61023d9b8e849e96f451a2a`。沿用原工作分支，仅本地提交。

[要求与验收范围](changes/acceptance-plan.md)定义有限的待办清单；[验证报告](reports/validation.md)记录已完成事项、实机读数、读取成本及剩余原生边界。`changes/analyze_live.py` 只分析已有证据，不连接或操作游戏。

证据目录：

- `evidence/*before*.log`：隔离失败复现；不对应独立缺陷数量。
- `evidence/integration-v4.log`、`evidence/debug-final/`：最终受影响模块及真实 stdio CLI 契约回归；后者对应的公共接线未再变动，市民查询的实机补修另有定向回归。
- `evidence/live-final/`：首次实机，暴露 MANAGE 工作数组不可用；会话无未决请求后正常关闭。
- `evidence/live-v2/`：确认显式 MANAGE 参数有效，发现城市中心 1/0 被错误判为异常。
- `evidence/live-v3/`：T50 复验通过，随后补齐简报可见性字段；未作为最终四阶段验收。
- `evidence/live-v4/`：冻结版本的四阶段复验、T200 市民偏好和 T201 政策 A→B→A 实验。
- `evidence/live-analysis.json`、`evidence/experiments-analysis.json`：已有证据的独立复算。
- `evidence/closing-integrity.json`：结束时复核源文件、原始文件、登记与 12 份新增检查点。
- `evidence/source-before.json`：受信历史文件与登记指纹；结束时另存复核，不覆盖原记录。

本轮自行启动游戏，结束前单独检查未决状态、保存和进程退出。正文 UTF-8 字节、模型命令数和原生查询数分别统计；没有整局对照，不推断胜率或模型思考时间。
