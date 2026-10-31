# 四叶草 T1→T100 调用审计结果

已完成：从受信原始 T1 到达 T100，原生回执连续确认 99 次推进。独立复验结束后恢复原 T100，另存 `callaudit_20261031_t100_verified`，未结束 T100；CLI 正常关闭，全部 Runtime pending 为 false、transport unknown 为 null。

本局由模型逐回合决策，没有通用策略或自动对局脚本。T49、T92 两次源码部署先保存并关闭原 CLI，再在同一游戏现场续推；主局没有重启或读档。四个 CLI 分段分别使用 `c6fc5e0`、`c6fc5e0`、`4bbf6d7`、`ab02f53`。最终 129 个 source／knowledge 文件与离线受测版本逐一 SHA 相同。

| 验收范围 | 结果 |
| --- | --- |
| 连续主局 | T1→T100，99 次推进；正常 play 2,456 命令、2,074,190 正文 UTF-8 字节、1,040.359359 秒工具 RPC；额外验收与加载另计 |
| 读取量 | 两次搜索实际正文减少 84.21%／81.57%；三领袖规则卡实际合计减少 41,534 字节／14.68%，保留完整展开与原指纹；1,857 卡离线双向还原 |
| 事实补缺 | 原生政府当前窗口、晋升候选、逐城多数宗教；邻城传教、移动／商人 post-MP、交易收尾提示、协议名称、升级目标及地图来源 |
| 原生复验 | T57 政府拒因与无攻击物理伤害预估；T80 四城道教／新教／none 与独立宗教扫描 4/4 一致；T88 转城 MP=0 与独立回读一致，升级目标 Man-at-Arms、165 金币与实扣一致 |
| 和平来约支线 | 额外 T60→T64 共 4 次推进；自然来约显示「和解 9 回合」，单次接受后同回合实收 6 金币，战争解除；按原请求完成外交与议会收尾 |
| 离线门槛 | 最终 `ab02f53`：`civ6 test debug` 434 passed／52.60 秒，`verify knowledge` PASS；相关定向测试和双路独立审查通过，重叠集合不相加 |
| 恢复与保全 | 原 T100 与复验另存均保留；恢复后七个 decision sections 文本、精确帝国读值及原队列一致，overview 仅同数量兵种的枚举顺序变化；不声称整个世界或 RNG 相等 |

终点为本方 4 城／29 人口、四城道教、得分 273，金币 97.46875、信仰 344.6796875。仍处于黑暗时代，科技落后；本局真实损失和模型错误均保留。一局不能证明整体策略质量不退化或胜率提高。物理预估不支持神学／空袭，真实小数地图收益本例未覆盖，map／workforce 整数差异与升级战略资源时序仍未定因。

- [调用分析与验证边界](reports/call-analysis.md)
- [实施、测试和独立审查](reports/implementation-review.md)
- [存档及登记指纹](reports/checkpoints.json)
- [关键证据指纹](reports/evidence-manifest.json)
- [T6→T30 记录](reports/t005-t030.md)、[T36→T49 记录](reports/t036-t049.md)、[T49→T100 记录](reports/t049-t100.md)
- [历史进度与范围](changes/tracker.md)、[有限复验配方及结果](changes/verification-plan.md)
- [无策略的离线证据分析器](changes/analyze_calls.py)与[主局阶段边界](changes/analysis-phases.json)

原 T100：`callaudit_20261031_t100_final`，855,101 B，SHA-256 `6bd873cca4f18ece147f1dff22fc4a0c36056e9660f7e1c7d3e83661c2c9b779`。最终核验另存：`callaudit_20261031_t100_verified`，855,146 B，SHA 见存档清单。

证据分别保存在本轮本地 `evidence/baseline/`、`evidence/play-t006-t100/`、`evidence/play-t049-t100-fixed/`、`evidence/play-t092-t100-repaired/` 与 `evidence/verification-ab02f53-pty/`，后者 151 条命令和 4 次推进全部单独计量。`evidence/offline-fixes/` 保留修前失败、修后通过和审查记录；`evidence/checkpoints/` 保留存档与登记副本；`evidence/worktree-preserved/` 保全隔离树的 3,693 个证据文件并逐一核对 SHA。大文件和原始证据依项目约定留在本地，轻量报告与指纹随 Git 提交；本轮没有 push。

隔离实施树清理受阻：应用两次拒绝归档，原因均为 `Another active task still uses this worktree.`。源码已合入，所需忽略证据已保全；保留 worktree，未强行删除或改变其他任务。此项不影响 T100 现场、修复提交与验证结果。
