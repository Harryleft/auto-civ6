# 独立审查与修复

基线：`b619fe3fd9df59683c2f1e45cff5a621d93a4747`，工作树审查；起始 diff 固定于忽略的 `evidence/review.patch`，包含新源文件和测试。规格为本轮 `acceptance-plan.md`，标准来源为 `AGENTS.md`、`docs/development.md`、`docs/architecture.md`、`GLOSSARY.md`。两个独立只读审查者分别核对标准和规格，未操作游戏或修改文件。

## Standards

初次报告两项需修复问题：城市攻击提示只核对目标格而未核对逐单位可见性；文化伟人槽位接口失败被解释为 0/0。另指出选址的邻接丘陵汇总会读取未揭示地形。

修复后复核原文：No material issues found.

## Spec

初次报告：已揭示但当前有迷雾的外国城市仍会影响选址；城市目标格内隐藏单位仍会被披露；文化伟人槽位失败不能变为零。

修复后复核原文：No material issues found.

## 修复证据

四个问题对应六个隔离原生适配用例：修复前 6 failed，修复后相关集合 51 passed。城市目标逐单位检查 `IsUnitVisible`，未知使目标板不完整；外国城市距离过滤只用当前可见城市，邻接丘陵只用已揭示地形；文化槽位按 `GetNumGreatWorkSlots` 读取全部有效槽位，数量／类型／内容任一未知都保留未知，并在简报明确显示。

初次 findings 按 Standards 3 项、Spec 3 项分别保留；有两项跨轴重复，不将六条报告当成六个独立缺陷。修复后两轴均未报告实质问题。原始回归日志为 `review-regressions-before.log`、`review-regressions-after.log`；最终版本另执行四阶段实机查询，实机覆盖不扩展到历史阶段不存在的文化伟人或隐藏单位场景。
