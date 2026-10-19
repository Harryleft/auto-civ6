# B01-01：登记新基准并维护轮次边界

Status: resolved
Blocked by: none

Spec: ../spec.md

交付：受信基准注册表；新旧加载核对；按所选基准建立轮次；保护所有原件；新档存档清单及指南更新。沿用 checkpoint / GameState / lifecycle seams 做 TDD，保持未知文件零提交和旧调用兼容。只改本票相关内容。

## 验收

- 新档 exact SHA 与预期身份可用，变化文件与未知文件拒绝。
- 新旧基准创建独立轮次，当前轮次的完成检查点可恢复；上轮检查点不可恢复。
- 原件名称大小写与尾随空格变体均在原生保存前拒绝。
- 相关离线测试、代码审查通过；T1→T2 和独立检查点实机结果由主代理记录。

## Answer

实施 f3f919a，合入 ffd3f06。425 项相关离线通过；独立 Standards / Spec 无实质发现。T1→T2、独立保存和原生菜单恢复核对通过，见 ../../../../reports/progress.md 及本轮 evidence/tool-calls.jsonl。
