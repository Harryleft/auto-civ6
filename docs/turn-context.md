# 回合局面简报（turn context）

精简模式移走治理控制面之后，原先唯一稳定进入模型上下文的「每回合输入」
（治理快照）也随之消失。本页说明替代品：一份直接读 typed `GameState` 的固定局面简报。

代码落点：`src/civ_mcp/turn_context.py`（采集与呈现）、`server/tools/queries.py`
（`get_game_overview` 挂载）、`server/tools/end_turn_flow.py`（推进后返回下一回合简报）、
`server/pipeline.py`（写入门槛）。

## 目标与不做的事

目标是把决策必需的局面对**自动**送进回合输入，而不是等模型想起来去查。它：

- 只读 typed `GameState` 与既有 `facts`；不重新解析旧叙述文本；
- **不**调用治理快照来「顺便拿数据」，也不依赖 `BeliefEngine` 是否启用；
- 不是第二份权威世界状态，也不是第二份策略报告：没有打分、没有预测、没有第二个模型；
- 不新增知识图谱、向量库、历史数据库或任务 DAG；
- 不做跨回合变化计算（初版固定「暂无可比较基线」），因为那会引入新基线与分支问题。

## 字段白名单

只有 `ALLOWED_DECISION_FIELDS` 中的字段能进入简报：

| 字段 | 来源 | 覆盖语义 |
|---|---|---|
| `identity` | `get_game_identity` | `COMPLETE` |
| `rules` | `get_game_overview`（规则集/速度/难度/启用胜利条件/时代） | `COMPLETE` |
| `situation` | `get_game_overview`（产出、城市/单位数、研究/市政、探索度） | `COMPLETE` |
| `cities` | `get_cities`（含当前生产、增长、忠诚、被掠夺区块） | `COMPLETE` |
| `units` | `get_units`（含未行动/待晋升/可升级） | `COMPLETE` |
| `threats` | `get_threat_scan` + `get_barbarian_overview` | `CURRENTLY_VISIBLE` |
| `diplomacy` | `get_diplomacy`（只列已接触的文明） | `COMPLETE` |
| `victory` | `get_victory_progress` | `COMPLETE` |
| `notifications` | `get_notifications` | `COMPLETE` |
| `blockers` | `get_diplomacy_sessions` + `get_pending_deals` | `COMPLETE` |
| `game_over` | `check_game_over` | `COMPLETE` |

新增字段必须同时进入该白名单与采集器：`build_turn_context` 在白名单外多采集或漏采集
字段时会直接抛错，而不是悄悄多送或少送信息。

**不进入简报的信息**：`get_diary_snapshot` 的全玩家统计、`map/` 离线全量导出、
`get_rival_snapshot` 之类的评估侧产物。它们是评估工具，只能用于 C5 的分析，
不能成为精简臂相对原路径的信息优势——否则对照实验测的就不是治理删减，而是额外的隐藏信息。
空间信息只覆盖已知城市、当前可见单位与已揭示营地，细节留给既有地图工具展开。

## 缺失语义：不补零、不判安全

每个字段都有明确状态：

- `ok` — 采集成功；
- `unavailable` — 查询失败，`detail` 里带异常类型与「缺失不补零、不视为安全」；
- `unknown` — 查询成功但值无法确认（例如对局身份读到 `unknown`）；
- `not_applicable` — 该机制在本局不存在（例如未启用的胜利条件）。

关键点：**威胁扫描失败不会被渲染成「附近没有敌人」**，而是 `unavailable` 加一行
「军事行动前先补查」。单项可选查询失败不会锁死整个循环，但「待处理事项」会提示
依赖该证据的选择先补查。

## 一致性

采集被两次回合号读取夹住：若采集期间回合发生变化（AI 推进/读档），重试一次；
仍不一致则整份简报标为 `unknown`，并置 `write_allowed=no`，而不是把两个回合的字段
拼成「当前局面」。

`get_game_overview` 的简报在 `_logged` 已开启的 collection 作用域内采集，因此复用
现有缓存与失效规则；`end_turn` 之后的简报则在动作发生后的独立请求里重新读取动态状态。

## 文明规则材料

运行时不再把 `docs/paper/scenario-spec.md` 的 Cry Havoc 场景攻略当作文明规则。
其中的固定战略、未核验的能力数值与难度加成已移出自动简报。当前文明能力材料
明确标为缺失；规则集、时代、难度与速度仍从本局实时 `GameOverview` 读取。
后续能力材料只有核验来源和适用条件后才可收录，文明名称本身不能推出开战等战略选择。

## 接进循环的方式

1. **开局/恢复**：每回合第一条 `get_game_overview` 会附带简报（`CIV_MCP_PLAY_PROFILE=lean`
   时默认开启）。这就是该对局的「入口材料」。
2. **首次变更前的门槛**：精简模式下，若本会话尚未取得当前对局的入口材料，
   或上一份简报未能确认对局身份/本国实时状态，则**先只读返回局面，不执行原变更**，
   并带 `GATE:TURN_CONTEXT_REQUIRED`。拿到材料后重新调用同一工具即可继续。
   门槛只作用于写操作：
   - 声明 `readOnlyHint` 的只读工具永不被拦；
   - `load_save` / `load_game_save` / `load_save_from_menu` / `kill_game` / `launch_game` /
     `restart_and_load` / `list_saves` / `dismiss_popup` / `get_diary` 永远放行——
     否则游戏还没进入对局时门槛永远无法满足，就成了永久锁死；
   - 未注册的工具名不设门槛（那是路由层的错误，不该被简报掩盖）。
   
   稳定状态下的开销是 O(1)：`GameState._cache_epoch` 在每次读档/换局时自增，
   epoch 未变且上次简报允许写时直接放行，不再重复采集。
3. **end_turn 之后**：确认推进且不是 `blocked` / 未知 / 对局结束时，外层返回**下一回合简报**。
   这份简报即视为完成下一回合入口，无需模型机械重复查询。
   若 `end_turn` 确认推进到 T*N*，而简报读到别的回合号，会显式提示以只读复核为准。
4. **只推进成功、简报失败**：推进一经确认就保存独立回执；之后日志、地图或简报
   发生异常或耗尽单次调用预算，都保留已确认回合。回执明确写「推进已确认，简报待重取」并带
   `BRIEF_PENDING:RE_READ_OVERVIEW_ONLY`，只允许重新读 `get_game_overview`，
   **禁止重发 `end_turn`**，也禁止重复本回合已发出的改动。
5. **blocked / unknown / game_over 不伪造新回合**：不追加简报。

## 采集成本

简报自己报告成本，便于按实测决定是否需要降低慢变字段频率：

```
[采集元数据]
  calls=… elapsed_ms=… chars=… attempts=…
```

`calls=unavailable` 表示未采集往返次数（兼容数据字段 `query_calls=-1`）。
简报不再替换共享连接的执行方法，避免并发或取消后计数包装泄漏。
`elapsed_ms` 是本次采集耗时，`chars` 是简报正文长度。
离线夹具渲染的一份完整简报约 2.8 KB / 59 行。真实耗时与体积必须在现场读取，
不能拿离线数字当作现场实测。

## 对照实验用的临时开关

`CIV_MCP_TURN_CONTEXT=on|off` 可覆盖开关，默认仍由 play profile 决定。它**只**服务于
C5 的对照夹具（B 臂 = 原路径 + 同一份简报），不是面向用户的产品模式——
产品契约只有 `CIV_MCP_PLAY_PROFILE`，避免组合出未经验证的状态。

注意：写入门槛只由 **play profile** 决定，不受该开关影响。因此 A 臂与 B 臂只差
「是否多收到这份信息」，门槛不会混进对照变量。

## 验证

```bash
uv run pytest tests/test_turn_context.py -q
uv run pytest tests/ -q
uv run ruff check src tests
```

离线测试能证明：字段覆盖与状态语义、白名单与不泄漏、身份/实时状态缺失时停写、
单项缺失不锁死、只读与恢复工具不被拦、`end_turn` 各分支不伪造新回合、
`BRIEF_PENDING` 不诱导重发。

## 仍需现场只读确认

- 真实存档下的 `elapsed_ms` / `chars`，以及是否需要在保持胜利态势的前提下
  降低慢变字段频率；
- 关闭治理后，简报是否覆盖了原本由治理快照提供的全部关键阻塞；
- 同一回合动作之后的下一份简报确实读到新状态（而不只是缓存）；
- 模型是否真的因此减少「忘记查威胁/胜利进度」的错误——这属于 C5 的行为观察。
