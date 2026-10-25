# 本轮暴露的 CLI 问题修复与验证

来源：`20261025-1137` 实机推进（T50→T100）中记录的错误回执与故障过程。只修复可归因于 CLI／Runtime 的问题；原生资格拒绝和“必须由模型显式决策”的阻塞保持原样。

## 修复项

| # | 问题（证据） | 修复 |
| --- | --- | --- |
| 1 | 外交回应已生效但确认查询只看单帧，返回 `OUTCOME_UNKNOWN`；随后一次“只确认”的调用被回报成 `OK:RESPONDED`，实际并未提交请求的选项（证据：`relay3/requests` 04:18:41–04:18:52 的 POSITIVE/EXIT 回执序列） | `src/civ_mcp/diplomacy.py`：`confirm_response` 增加有界轮询（2s 内每 0.15s 复查）；`respond` 在仅有待确认回应时返回 `OK:CONFIRMED`，明确“请求的选项未提交，本次只确认了原响应”，不再伪装成新提交 |
| 2 | `game recover AutoSave_0060/0100` 的拒绝信息只有 `Checkpoint completion and expected identity are unavailable`，看不出原因与可用目标 | `src/civ_mcp/checkpoints.py` 新增 `registered_names()`（按回合排序列出本轮登记检查点）；`src/civ_mcp/game_state.py` 新增 `_unregistered_checkpoint_message()`，恢复/加载都用 `Error: UNREGISTERED_CHECKPOINT` 说明“原生 AutoSave 不可用、只接受 save create 登记的检查点”，并列出本轮名称 |
| 3 | 会话被替换后新会话连接 FireTuner 失败，只提示“确认游戏在运行”，实际是上一会话的 MCP 仍独占单一连接槽（证据：`relay2` 的 `[Errno 61]`） | `src/civ_mcp/connection.py`：连接失败信息补充“FireTuner 只接受一个客户端；结束其他 civ6 debug 会话及残留 civ-mcp 进程后重试” |
| 4 | 文档未说明检查点登记范围与单客户端限制 | `docs/cli.md`、`docs/agent-recovery.md` 各补一段 |

未修改（设计如此，仅记录）：`turn end` 的 `NOT_SUBMITTED (QUEUED_MOVE_CURRENT)`、同格军事单位的 `CANNOT_OPERATE`、无墙城市的 `city attack` → `NO_WALLS`、终局 `save create` 拒绝、原生资质拒绝（`NOT_AT_WAR`、`NO_PATH` 等）。

## 离线验证

新增测试：

- `tests/test_diplomacy_response_confirmation.py`：按实机回执顺序重放“提交后首查未确认 → 轮询到语句推进”；“只确认不提交”返回 `OK:CONFIRMED` 且不带提交调用；会话关闭；仍未知时保留 pending 门禁。
- `tests/test_checkpoint_messages.py`：登记名称按回合排序；原生 AutoSave 永不登记；`restart_and_load`/`load_game_save` 的错误含原因、`no restart performed`／`no load sent` 与本轮检查点列表。
- `tests/test_connection.py`：连接被拒时包含单客户端与残留 `civ-mcp` 提示。

结果：

| 检查 | 结果 |
| --- | --- |
| 直接 pytest（新增/改动文件） | 49 passed |
| 受影响模块直接 pytest（外交、交易、检查点、GameState、生命周期、连接、服务端、CLI 等 18 个文件） | 938 passed |
| `uv run --locked civ6 test diplomacy` | PASS（pytest exit 0） |
| `uv run --locked civ6 test checkpoints` | PASS（pytest exit 0） |
| `uv run --locked civ6 test connection` | PASS（pytest exit 0） |
| `python -m compileall -q src tests`、`git diff --check` | 通过 |

新增测试文件已登记到 `src/civ_mcp/devtools/testing.py` 的 `diplomacy`、`checkpoints` 领域，`civ6 test <domain>` 会执行。

## 实机验证（2026-10-25，终局后的 T101 现场）

| 验证 | 结果 |
| --- | --- |
| 正常会话健康：`state overview`、`turn status`、`diplomacy list`、`diplomacy action 2 DECLARE_FRIENDSHIP`、`pending`、`quit` | 通过；无 pending、`unresolved=false` |
| `game recover AutoSave_0100`（两次，含最终代码复核） | 通过：返回 `Error: UNREGISTERED_CHECKPOINT|… no restart performed …`，列出本轮可用的 8 个登记检查点（含 `fourleaf_1137_t100`）；**未执行重启**，游戏仍在原现场 |
| 单客户端连接占用（会话 A 持连接，会话 B 读状态） | 通过：B 返回包含“FireTuner 只接受一个客户端/结束其他会话与残留 civ-mcp 进程”的连接错误；A 继续正常工作 |
| 释放连接后重试（A `quit` 后 B 再读状态） | 通过：B 成功读取 T101 现场，验证提示中的处置建议有效 |
| `run` 前后游戏进程 | 未重启、未换档；终局现场保留 |

## 未实机验证

- 外交“仅确认不提交”的新返回：实机重现需要一次原生回应丢失（故障注入）；本轮用离线测试按实机回执顺序覆盖，未在真实对局触发。
- `game recover` 的成功路径：本轮修复只改了拒绝信息，未改动加载流程；为避免覆盖终局现场（终局不可保存），本轮没有执行恢复。
- 原生菜单加载、热座、Lua 路径：不在本次修复范围。

## 变更文件

- `src/civ_mcp/diplomacy.py`、`src/civ_mcp/checkpoints.py`、`src/civ_mcp/game_state.py`、`src/civ_mcp/connection.py`、`src/civ_mcp/devtools/testing.py`
- `tests/test_diplomacy_response_confirmation.py`（新增）、`tests/test_checkpoint_messages.py`（新增）、`tests/test_connection.py`
- `docs/cli.md`、`docs/agent-recovery.md`
