# 神级四叶草 T50 → 终局：单会话实机推进

2026-10-25，神级、快速、极小四叶草；苏美尔／吉尔伽美什，seed `-1896272000`。从上一轮（`games/2026-10-25-unit-cli-live/runs/20261025-094255`）**已确认的 T50 检查点**继续，推进到原生终局。原始基准 `fixtures/saves/benchmark_gold_tun_1.Civ6Save`（SHA-256 `0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`）未改动。

| 项目 | 结果 |
| --- | --- |
| 终局 | **T100 失败**：瑞典（克里斯蒂娜）宗教胜利（`VICTORY_RELIGIOUS`） |
| 我方终局 | 4 城、23 人口、总分 198、科技 40.2／文化 25.8、军力 223、金币 702 |
| 起点 | T50：2 城、12 人口、总分 97、科技 14.3 |
| 推进 | 50 次确认的 `turn end`（T50→T100），含暂停后的原请求确认 |
| 未决 | 终局时无 pending、无 transport_unknown；`quit` 正常（`unresolved=false`） |
| 交付 | [最终效果与运行观察](reports/final-report.md) |

## 使用方式

- 单一持久 `civ6 debug` 会话，经前台转接器 `scripts/civ6_cli_relay.py` 以 socket 驱动；未注册直接 MCP，无并行客户端，未用 Lua。
- 所有变更命令均先 `--dry-run` 预演再执行；每次推进以 `turn end` 回执的 `Turn X -> Y` 为确认依据。
- 长线推进使用本地受控脚本 `.scratch/autopilot.py`（不属于仓库代码，仅调用命名命令）：每回合读 `turn status`、`state overview`、`unit list`、`city list`、`diplomacy list`、`technology status`，按固定优先级处理生产/科技/市政/单位/外交暂停，异常即停并由人复核。遇到外交、议会、强制选择、未知阻挡项时停下处理。
- 每 5 回合 `save create fourleaf_1137_t<N>` 留检查点。

## 证据

| 内容 | 位置 |
| --- | --- |
| 逐回合摘要（43 条） | [turns.jsonl](reports/turns.jsonl) |
| 自动驾驶状态（定居点、已拒绝交易等） | [autopilot-state.json](reports/autopilot-state.json) |
| 最终效果与 CLI 观察 | [final-report.md](reports/final-report.md) |
| 本轮暴露问题的 CLI 修复与实机验证 | [changes/cli-fixes.md](changes/cli-fixes.md) |
| 第一段 T50→T61 请求/回执（含故障） | `evidence/relay/` |
| 失败恢复尝试 | `evidence/relay2/` |
| 最终 T50→T100 请求/回执（39MB） | `evidence/relay3/` |

`evidence/` 为本地原始日志与回执，默认不纳入 Git。

## 检查点（Single 目录，属于本轮登记记录）

> 2026-10-25 从原始基准重开新轮次后，检查点归属已轮换；下列文件保留备查，但不再是当前恢复来源。

| 存档 | 字节 | SHA-256 |
| --- | ---: | --- |
| `fourleaf_1137_t051` | 708275 | `b1bb15e0…c8286d` |
| `fourleaf_1137_t60` | 727629 | `dc273509…df345007` |
| `fourleaf_1137_t75` | 768850 | `00e99e0d…f1ab68c57117e6e2` |
| `fourleaf_1137_t80` | 781247 | `ba5bd41f…8f2d00998829` |
| `fourleaf_1137_t85` | 798531 | `9f5d3e1f…b42db4dc79637` |
| `fourleaf_1137_t95` | 817418 | `5a7a6d5e…b277d7db67cc29` |
| `fourleaf_1137_t100` | 820365 | `c643ddad…e955393a8ceb` |

完整哈希见 [最终报告](reports/final-report.md#证据与复现)。

## 过程事故（已处理，证据保留）

1. 第一段推进中，`unit attack`（侦察兵对被 89 HP 剑客）返回 `ERR:OUTCOME_UNKNOWN`：攻击者随后阵亡、目标不可见，原 Runtime 无法确认；同会话重复原调用仍 `OUTCOME_UNKNOWN`，未决动作门禁保留。按恢复流程尝试 `game recover AutoSave_0060`（原生自动存档，正是 T60 开局）被拒：**未登记检查点不可用于恢复**。
2. 随后 `game recover unitcli_20261025_094255_t050` 重启游戏但 180 秒内未确认加载，返回 `OUTCOME_UNKNOWN`；会话 RPC 超时后锁存 `transport_unknown`，本地只允许只读。按文档保留证据、显式结束该会话。
3. 新会话一度无法连接 FireTuner（4318）：上一会话的 `civ-mcp` 进程仍独占连接。显式结束该孤儿进程后，新会话通过 `game launch` + `save menu-load unitcli_20261025_094255_t050` 从主菜单加载已确认 T50，身份校验通过（苏美尔、seed −1896272000、T50、分数 97），随后完成 T50→T100。

上述 3 点是本轮最重要的 CLI 覆盖观察，详见 [最终报告](reports/final-report.md#着色点本轮暴露的问题与-cli-覆盖缺口)。

## 验证范围

- 实机通过：T50→T100 全部回合推进、生产/科技/市政/政策/总督/使者/伟人/议会/外交/弹窗/存档/终局检测。
- 离线未重跑：本回合未修改仓库代码，未运行测试集；脚本在 `.scratch/`，不在交付代码内。
- 未验证：`game recover` 的成功路径（本轮两次都未确认）；终局存档（原生拒绝）；热座与 Lua 路径（本任务未授权）。
