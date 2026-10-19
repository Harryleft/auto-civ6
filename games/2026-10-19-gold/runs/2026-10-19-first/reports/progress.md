# 本轮推进与阻塞记录

起点：`benchmark_gold_tun_1`。起始代码：`b8ddb0268e56c2920e2f3bb3907d2b9cebdac3e9`（main）。

| 编号 | 游戏回合 | 触发／实际表现 | 原因／处理 | 验证 |
| --- | --- | --- | --- | --- |
| B01 | 加载前，目标 T1 | `load_save_from_menu` 返回 `Checkpoint completion and expected identity are unavailable; no load sent.` | checkpoint 仅接受旧基准固定指纹及当前旧轮次的确认存档；补充新基准身份与轮次隔离 | 已修复：f3f919a／集成 ffd3f06；425 项离线通过；T1→T2、保存与原生菜单恢复通过 |

| B02 | T11 | 自动探索实际移动，Added 见证一直不匹配 | 区分事件参数：Added 第四参匹配原生行 Index | 78a4358／895c642；307 项离线、T11→T12 与保存通过 |
| B03 | T16 | 警戒 started=true，但活动 OPERATION 不等于 SENTRY | 区分警戒原生开始与既有目标活动 | e040a6f／968213b；314 项离线、T16→T17 与保存通过 |

当前已确认 T2，身份 sumeria / seed -1896272000；已在 (40,25) 建立乌鲁克，研究采矿业，生产侦察兵，勇士向西探索。用户已确认现有 checkpoint / GameState / lifecycle 验证边界。

B01：实施 f3f919a，集成 ffd3f06。相关离线 425 passed，compileall / diff --check 通过；Standards 与 Spec 两项独立审查均无实质发现。原始新旧基准不覆盖、不混用轮次。

实机证据：evidence/tool-calls.jsonl 中 #2 加载原件，#19 确认 T1→T2，#20 保存，#22 原生菜单恢复同一 T2 检查点，#23–24 核对单位位置、城市人口、增长与生产队列。检查点 gold_20261019_t002_b01_verified SHA-256 80b2abac7fd93492d2c9bb68e3af1d2627ad306fce0cca481eae4e08060ea724。轮次 e8b4d6a62dca49bbb256e8264beb18f2。此恢复是技术验证，未改变游戏决策结果。

原件逐字节归档到 evidence，SHA-256 一致。游戏继续，尚未终局。

## T3–T11

完成采矿业、制陶术、法典；首都人口 4，生产战车，研究畜牧业／对外贸易。发现香港、非斯和珠穆朗玛峰。

- E01，T8：自然奇观弹窗暂停勇士移动，dismiss_popup 后原请求确认通过，无代码修复。证据 #55–60。
- B02，T11：侦察兵 automate 后实际移动，但任务 started 缺失；关闭弹窗后原请求仍未知（#87、#90）。已保留 Lua 任务见证 b02-native-task.txt，进入 to-spec → implement-spec，未重发动作。为读取原生诊断并刷新修复代码，关闭了原 MCP 客户端；接下来必须显式恢复同轮确认的 T2 检查点，不因 MCP 重启把旧未知视为成功。

B02 复现：显式 restart_and_load 同轮 T2（#92），依照原日志重放 T2→T11（#93–125），#126 确认保存 gold_20261019_t011_before_b02；#127 安装临时事件记录器，#128 仅提交一次 automate，再次未知。只读捕获 Added(0,196608,0,1)、MoveComplete(0,196608,43,28)，未出现 Started。原生行 Index=1 对应 AUTOMATE_EXPLORE（hash -1676983098）；旧实现误把 Added 第三参数当操作hash。临时诊断监听器已移除。修复验证从该 T11 检查点开始，无需再回退到 T2。

B02 已完成：实施 78a4358，集成 895c642；相关离线 307 passed，compileall / diff-check 通过，两项独立审查无实质发现。#130 显式恢复 T11 后，#132 单次 automate 返回 AUTOMATED（原生任务开始，不代表后续探索完成），#134 确认 T11→T12，#135 保存 gold_20261019_t012_b02_verified。未用位移代替见证。当前检查点更新到 T12。

B03，T16：战车262146警戒已开始，r.started=true、原生非就绪，但实际活动 OPERATION1225574625 与工具期待SENTRY-793848223不同，#163/#166持续未知。保留 b03-native-alert.txt，进入 to-spec → implement-spec。原MCP已关闭供只读诊断；下一步从同轮T12确认检查点显式恢复，按原操作复现T16后另存。

B03复现恢复：#168显式恢复同轮T12；#169–184按原动作重放；#185保存 gold_20261019_t016_before_b03，#186核对单位与原T16一致。当前无未决操作，已正常关闭MCP；修复合入后只刷新MCP代码即可实机复验，无须再次重载世界。

B03 完成：e040a6f／集成968213b，314相关离线通过，两项独立审查无实质发现；#188警戒原生任务开始确认，完成加拿大首次会面后#206确认T16→T17，#207保存gold_20261019_t017_b03_confirmed。#196曾保存名为t017_b03_verified的文件，但实际为T16（元数据正确），不作为T17结果；后续以t017_b03_confirmed为恢复点。当前T17：首都pop5，开拓者生产中，写作完成，加拿大已会面。
