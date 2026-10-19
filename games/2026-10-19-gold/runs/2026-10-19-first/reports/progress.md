# 本轮推进与阻塞记录

起点：`benchmark_gold_tun_1`。起始代码：`b8ddb0268e56c2920e2f3bb3907d2b9cebdac3e9`（main）。

| 编号 | 游戏回合 | 触发／实际表现 | 原因／处理 | 验证 |
| --- | --- | --- | --- | --- |
| B01 | 加载前，目标 T1 | `load_save_from_menu` 返回 `Checkpoint completion and expected identity are unavailable; no load sent.` | checkpoint 仅接受旧基准固定指纹及当前旧轮次的确认存档；补充新基准身份与轮次隔离 | 已修复：f3f919a／集成 ffd3f06；425 项离线通过；T1→T2、保存与原生菜单恢复通过 |

当前已确认 T2，身份 sumeria / seed -1896272000；已在 (40,25) 建立乌鲁克，研究采矿业，生产侦察兵，勇士向西探索。用户已确认现有 checkpoint / GameState / lifecycle 验证边界。

B01：实施 f3f919a，集成 ffd3f06。相关离线 425 passed，compileall / diff --check 通过；Standards 与 Spec 两项独立审查均无实质发现。原始新旧基准不覆盖、不混用轮次。

实机证据：evidence/tool-calls.jsonl 中 #2 加载原件，#19 确认 T1→T2，#20 保存，#22 原生菜单恢复同一 T2 检查点，#23–24 核对单位位置、城市人口、增长与生产队列。检查点 gold_20261019_t002_b01_verified SHA-256 80b2abac7fd93492d2c9bb68e3af1d2627ad306fce0cca481eae4e08060ea724。轮次 e8b4d6a62dca49bbb256e8264beb18f2。此恢复是技术验证，未改变游戏决策结果。

原件逐字节归档到 evidence，SHA-256 一致。游戏继续，尚未终局。

## T3–T11

完成采矿业、制陶术、法典；首都人口 4，生产战车，研究畜牧业／对外贸易。发现香港、非斯和珠穆朗玛峰。

- E01，T8：自然奇观弹窗暂停勇士移动，dismiss_popup 后原请求确认通过，无代码修复。证据 #55–60。
- B02，T11：侦察兵 automate 后实际移动，但任务 started 缺失；关闭弹窗后原请求仍未知（#87、#90）。已保留 Lua 任务见证 b02-native-task.txt，进入 to-spec → implement-spec，未重发动作。为读取原生诊断并刷新修复代码，关闭了原 MCP 客户端；接下来必须显式恢复同轮确认的 T2 检查点，不因 MCP 重启把旧未知视为成功。
