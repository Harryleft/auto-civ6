# 历史高频操作 CLI 覆盖检查

2026-10-24 统计当前保留的 3 份真实热座 `agent-tools.jsonl`，合计 **4,293 次工具调用**。命名 CLI 的对应调用频次从 2,867 条增至 3,473 条（66.8% → 80.9%），新增对应 606 条；领域快捷命令从 16 个增至 43 个。通用 `call` 原先即可访问现有 MCP，这里统计的是可发现的命名快捷入口，不是新增游戏能力。

## 统计口径

主样本是本对局初始热座轮次及 T2—T100、T101—T136 两段续局的日志。计数单位为记录的工具调用，不等于独立成功动作；重复确认、重载后的调用也计入。每个源文件的路径、字节数、记录数及 SHA-256 保存在本地 `usage-audit.json`；原始日志不提交。

本机 MCP 日志初始另有 20,754 条记录，混有离线测试、诊断及与对局日志重叠的调用，只作辅助参考，没有与主样本合并。该样本集中在同一热座对局，不能外推为所有项目使用习惯。

| 既有操作／频次 | 新命名入口 |
| --- | --- |
| 地图区域 154 | `map area` |
| 跳过剩余单位 91 | `unit skip-remaining` |
| 胜利进度 75 | `victory show` |
| 城市射击 35 | `city attack`，实际调用 `city_action(action=attack)`，按原日志名 `city_attack` 归一化 |
| 政策读取 27、设置 20 | `policy show/set` |
| 城邦读取 21、派使者 16 | `city-state list/envoy` |
| 区域选址 20 | `city district` |
| 单位升级 17、晋升读取 14、晋升 8 | `unit upgrade/promotions/promote` |
| 总督读取 16、任命 6、指派 9、晋升 8 | `governor list/appoint/assign/promote` |
| 城市焦点 15、购买 12 | `city focus/buy` |
| 议会读取 14、通知 10 | `congress show`、`notifications show` |
| 路径估算 9、建造者任务 9 | `unit path/tasks` |

另为已有 `unit_action` 增加 `attack/skip/fortify/heal/improve` 五项明确参数入口，不重复计入新增频次。含原有 `move`，这些动作在日志中合计 1,133 次，占 unit_action 的 1,203 次调用中的 94.2%。低频动作继续使用 `unit action` 或 `call`。

热座专用 `begin_hotseat_turn` 300 次、`end_hotseat_turn` 403 次仍由原控制器管理，不混入普通回合工具。扣除这 703 条后，命名快捷入口对应主样本中 3,473／3,590 条调用（96.7%）。其余外交、交易、时代选择等仍可经 `call` 使用，本批未改写这些多阶段协议。

## 实现与验证

- 所有入口在现有前台参数解析层转换为公开 MCP 调用，继续使用同一进程、pending、真实 schema、offline 限制和单次提交路径。没有修改 Runtime 或加入自动动作策略。
- 变更全部支持 dry-run；政策对明确槽位做规范化，并在发送前拒绝格式错误或重复槽位；地图半径越界直接拒绝，避免服务端静默截断。
- `tool_result` 识别协议错误以及服务显式 Error:/ERR: 文本边界。is_error 表示两类错误的并集，protocol_is_error 保留原协议标志，error 给出来源、代码和建议；原 result 不改写，不从普通展示文案推断成功或释放门禁。
- 最终 `civ6 test debug`：286 项通过，证据在 `.scratch/cli-frequency-validated-regression/`。新增命令逐项与正式 MCP schema 核对，隔离 stdio 验证全部预演零提交、旧式错误和未知结果不重放；原 pending、身份、信号及退出测试保持通过。
- compileall、差异空白和本地引用检查通过；wheel 构建成功，包内相关源码与最终源码逐字节一致。

最终实机重放见[本轮报告](../../games/2026-10-24-dual-ai-four-leaf/runs/20261024-084444-hotseat/continuations/20261024-151635-t101-end/reports/2026-10-24-cli-frequency.md)：36 条命令、17 次查询、16 次预演，已验证的原生 NO_TILES 拒绝正确显示，状态及原存档不变。实际游戏变更没有在该暂停现场执行，不列为实机通过。
