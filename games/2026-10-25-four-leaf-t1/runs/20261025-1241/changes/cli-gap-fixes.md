# CLI 真实缺口修复与实机验证

来源：`20261025-1241` 实机推进（T1→T129）与更早的 T50→终局轮次中暴露的“真正未被 CLI 覆盖”的问题清单。只修复能在现有 Runtime/CLI 边界内安全落地、可离线回归且可实机验证的项；其余保留并记录原因。

## 修复项

| # | 缺口（原分析） | 修复 | 实机验证 |
| --- | --- | --- | --- |
| 1 | 主菜单/加载中/模态界面阻塞时，游戏侧只读查询可能挂到 CLI 300 秒 RPC 超时，随后锁存 `transport_unknown`，会话只能读、无法继续 | `server.py`：只读工具（`get_*`/`list_*`/`search_*`）加 45 秒总预算，超时返回 `ERR:TOOL_TIMEOUT`，明确“未提交、未确认、未重放”，不改变 pending | 主菜单态 `state overview` 在 0.04 秒内快速失败（不再 300 秒），会话不受影响 |
| 2 | 终局/暂停后没有“回主菜单”的命名出口，只能 `game stop` 杀进程 | 新 MCP 工具 `return_to_main_menu` + `game menu` 命名命令：提交一次原生退出并用 FrontEnd 事实确认；未确认时保留替换门禁 | 游戏内 T125 → `game menu` → `MAIN_MENU` 确认；主菜单再次执行幂等确认；随后 `save menu-load fourleaf_t1_t125` 恢复 T125 可玩状态 |
| 3 | 无法判断 FireTuner 连接为何被拒、是否有残留 MCP 占用单客户端槽，只能手工 `ps`/`lsof` | 新顶层命令 `civ6 tuner status`：只读探测端口、`lsof` 占用者、`ps` 本地 CLI/MCP 进程；输出 `occupied`／`available`／`available_with_stale_processes`／`stale_session_likely`／`game_offline` 与处置提示 | 活动会话时输出 `occupied` 并给出 MCP/游戏 PID；`quit` 后输出 `available_with_stale_processes` 并列出残留进程 |
| 4 | 传输未知时如何收尾不够明确（普通 `quit` 被拒是设计如此） | `debug.py`：`TRANSPORT_UNRESOLVED` 错误附 hint，指向 `quit --abandon` 显式收尾；`quit --abandon` 帮助文本与 `docs/cli.md` 说明 `context_lost=true`／`exit_reason=abandoned`、不退役游戏侧请求 | 离线回归覆盖（既有 `test_debug_cli` 参数化 abandon 用例全部通过）；本轮无法安全制造传输未知，未做破坏性实机复现 |

实机验证中发现并顺带修复的新 bug：`tuner status` 最初把“已有活动连接、端口拒绝新探测”误判为 `stale_session_likely`；已改为优先看 `ESTABLISHED` 连接对，判定 `occupied`，并加回归测试。

## 未修复项（保留，附原因）

| 缺口 | 原因 |
| --- | --- |
| 原生 `AutoSave_*` 不能作为加载/恢复目标 | 需要从 `.Civ6Save` 头解析文明/种子/回合并绑定登记，再定义“显式收养”授权语义；涉及存档格式解析与安全设计，超出本次修补范围 |
| 回合阻挡项没有结构化“可行动作列表” | 需要为每类 blocker 建立原生候选查询与动作映射，属于新能力而不是修补 |
| 单位叠放/移动队列的原子修复入口 | 组合式变更会绕过“单次提交、同一意图确认”的既有纪律，需新规格设计 |
| `city_range_attack` 不区分具体城市/是否已开火 | 需扩展原生就绪查询（逐城远程攻击资格），并与已有 blocker 语义协调 |
| `unit tasks` 是建议值、区域生产需另查坐标 | 属于原生候选与提交契约，当前错误信息已可操作；改为自动选择会替模型做策略决定 |
| `era show` / `civic status` 呈现未分化 | 纯展示层，优先级低于恢复与阻断链路 |

## 离线验证

| 检查 | 结果 |
| --- | --- |
| 新增测试 `tests/test_read_timeout.py`、`tests/test_return_to_main_menu.py`、`tests/test_tuner_status.py` | 16 passed |
| 受影响模块直接 pytest（server、game_state、dev CLI、debug、session、connection、lifecycle、end_turn 等 16 个文件） | 862 passed |
| 最终受影响回归（含最新连接提示与 tuner 判定） | 292 passed |
| `civ6 test debug` / `connection` / `turns` | 全部 PASS（pytest exit 0） |
| `python -m compileall -q src tests`、`git diff --check` | 通过 |

新测试已登记到 `src/civ_mcp/devtools/testing.py` 的 `debug`、`connection` 领域。

## 实机验证（2026-10-25 14:15–14:19，单一前台会话，socket `/tmp/civ6-gap.sock`）

| 步骤 | 结果 |
| --- | --- |
| 会话连接（游戏当时仍在主菜单） | `turn status` 报连接错误；确认游戏进程在、端口 LISTEN 于主菜单（无 GameCore/InGame 上下文） |
| 主菜单态读取 | `state overview` **0.04 秒**返回明确连接错误（未挂起、未锁存） |
| `game menu`（主菜单，幂等） | `MAIN_MENU|Native main menu confirmed; no save or load was submitted.` |
| `save menu-load fourleaf_t1_t125` | 加载成功：sumeria、seed −1896272000、turn 125，身份与分数证据一致 |
| `turn status` | T125 可玩、无 pending |
| `civ6 tuner status`（会话连接中） | `occupied`，列出 MCP 30726 与游戏 38935 的 ESTABLISHED 连接 |
| `game menu`（游戏内 T125 → 主菜单） | `MAIN_MENU` 确认；随后读取快速失败 |
| 恢复 | 再次 `save menu-load fourleaf_t1_t125` 成功，回到 T125 可玩状态 |
| `quit` | `unresolved=false`、无 transport_unknown；游戏保留在 T125 |
| `civ6 tuner status`（quit 后） | `available_with_stale_processes`，端口可接受连接、无 established、列出残留 MCP 进程 |

原始会话证据：`.scratch/fix-gaps/live-a/`（scratch，不入库）。游戏状态：T125 已加载、无未决；仓库内对局与检查点未改动。

## 变更文件

- `src/civ_mcp/server.py`（读取预算、`return_to_main_menu` 工具）
- `src/civ_mcp/game_state.py`（`return_to_main_menu` 门禁与确认）
- `src/civ_mcp/devtools/commands.py`（`game menu`）
- `src/civ_mcp/devtools/tuner.py`（新增 `tuner status`）
- `src/civ_mcp/devtools/debug.py`（传输未知 hint、quit 帮助）
- `src/civ_mcp/devtools/testing.py`（新测试登记）
- `src/civ_mcp/connection.py`（连接失败提示补充主菜单处置）
- `tests/test_read_timeout.py`、`tests/test_return_to_main_menu.py`、`tests/test_tuner_status.py`（新增）
- `tests/test_server.py`（工具数 90→91）、`tests/test_dev_cli.py`（顶层命令列表含 `tuner`）
- `docs/cli.md`（顶层命令、生命周期、读取预算、退出说明）
