# T56 陆地战斗单位与支援单位堆叠修复

同一 T56 局面中，旧代码将破城槌进入弓箭手所在格误判为同类堆叠；修复后，破城槌进入弓箭手所在格、弓箭手返回破城槌所在格均由原生移动确认。验收期间没有推进回合。这证明该操作兼容性修复有效，不构成两倍速度或最终获胜证据。

## 修复与离线验证

原预检只区分平民与非平民，将 `FORMATION_CLASS_LAND_COMBAT` 和 `FORMATION_CLASS_SUPPORT` 都归入非平民，提前返回 `STACKING_CONFLICT`。修复仅允许已知双方均为 `DOMAIN_LAND`、且恰为这两个 formation 的配对继续执行原有原生目标资格与路径检查；同类、未知、宗教和飞机规则保持原行为，未修改提交、确认或 pending 释放逻辑。

新增 18 项真实 Lua 回归覆盖双向配对、原生拒绝／未知、路径未知、同类及信息不全零提交，以及未确认动作保留门禁、同意图确认和不同意图拒绝均不重发。其中 10 项先失败再通过。`test_move_target_lua.py`、`test_religious_movement.py`、`test_action_lua_execution.py` 合计 **177 项通过**；独立审查另复跑新增 **18 项通过**，未发现实质问题。`git diff --check` 通过。

## 同场景实机回执

本次沿用 `benchmark_turn_1` 派生的当前优化轮次：苏美尔、seed `719665280`、player 0、T56。不是重新加载基准或历史存档。场景为拉格什 `(15,17)` 内的弓箭手 `589829`，以及相邻 `(15,16)` 的破城槌 `1048588`。

旧会话 CLI run `dc22f2d823164c3fb7205e55e8ab46e5`，证据位于 `evidence/optimized-relay-t039-final/cli/debug.jsonl`：

| sequence | 观察或动作 | 结果 |
| --- | --- | --- |
| 1091 | `game decision` | 身份为 T56；弓箭手在 `(15,17)`、破城槌在 `(15,16)`；所有 pending=false。 |
| 1092–1095 | `unit move 1048588 15 17 --execute` | 静态预演后调用一次，返回 `STACKING_CONFLICT`，outcome=`not_submitted`；没有移动提交。 |
| 1099 | 保存 `speed_20261101_optimized_t056_stack_before` | 原生完成与文件确认，802283 字节，T56。 |
| 1101 | `pending` | 所有 pending=false，transport_unknown=null。 |
| 1105 | 预演后的正常 `quit` | unresolved=false，transport_unknown=null。 |

随后正常开启加载修复源码的新 CLI，保持游戏现场，没有执行读档或重启游戏命令。新 CLI run `46cc2386665541f8bbf8c59c72b292f8`，Runtime session `b872a45a67b242f4b82603c52720cfa3`，证据位于 `evidence/optimized-relay-t056-stack/cli/debug.jsonl`：

| sequence | 观察或动作 | 结果 |
| --- | --- | --- |
| 4 | `game decision` | 身份仍为 T56、相同单位起点，所有 pending=false。 |
| 5–8 | `unit move 1048588 15 17 --execute` | 破城槌从 `(15,16)` 到 `(15,17)`，剩余移动力 1，outcome=`confirmed`。 |
| 11 | `map area 15 17 --radius 1` | `(15,17)` 同格显示 `ARCHER, BATTERING_RAM`。 |
| 12–15 | `unit move 589829 15 16 --execute` | 弓箭手离城到 `(15,16)`，剩余移动力 1，outcome=`confirmed`。 |
| 16–19 | `unit move 589829 15 17 --execute` | 弓箭手返回破城槌所在的 `(15,17)`，剩余移动力 0，outcome=`confirmed`。 |
| 23、26 | 破城槌休眠；地图回读 | 休眠任务确认；地图仍显示两者同格。 |
| 30 | 保存 `speed_20261101_optimized_t056_stack_after` | 原生完成与文件确认，802335 字节，T56。 |
| 32 | `pending` | 所有 pending=false，transport_unknown=null。 |

三次移动均有各自静态预演、一次工具调用与确认回执；旧请求明确为未提交，正常关闭会话后才以新意图执行同一目标，没有将结果未知请求重发。两份检查点分别保留，原始基准和既有验收不变。

## 结果范围

本次实机验证了破城槌与弓箭手在陆地城市格的双向堆叠。其他支援兵种、非城市格及 Windows 实机未在本段测试；同类、未知、宗教、飞机及未决门禁边界由上述离线回归覆盖。T56 的诊断、修复和正常会话交接时间须单列，本段没有整回合速度对照，也没有原生胜利证据。
