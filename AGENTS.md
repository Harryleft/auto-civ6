# 本机 Civ6 MCP 协作规则

## 目标与职责

本项目只负责模型通过本机 MCP 玩 Civilization VI，保留真实游戏查询、操作、验证、回合推进和显式生命周期控制。

不重新引入网页、云同步、批量评测、后台观测服务、强制反思或日记协议。策略判断由模型负责，Runtime 不替模型选择占城、议会或外交策略。

## 唯一测试基准

- 所有实机测试只从 `benchmark_turn_1` 原始存档开始，原件与 SHA-256 清单位于 `fixtures/saves/`。不加载旧示例地图、旧自动存档或其他历史基准。
- 该基准实际为苏美尔／吉尔伽美什、风云变幻、不朽难度、快速、第 1 回合；用户已确认使用它，不能沿用更早讨论的地图设置。
- 本机另保留 `benchmark_after_20_turns`，它是本次净推进 20 回合后的第 21 回合结果，仅用于查看验收成果，不是新测试起点。
- 原始基准不得覆盖。需要保存过程状态时使用独立名称；恢复检查点必须来自本次从该基准开始的同一对局，且核对种子和回合。不得仅按文件修改时间挑选不明来源的自动存档。

## 游戏操作

- 先用 `get_game_overview`、`get_units`、`get_cities` 等工具取得当前回合的真实信息，再使用坐标、单位 ID 和城市 ID。
- 动作返回后的回读数据是后续决策依据；结果未知时先查询，不盲目重复动作。
- 明确区分不可见、未知和不存在；不编造迷雾内的敌人、地图或协议。
- `end_turn` 无 reflection 参数，也无需先写反思。返回阻塞后，调用对应工具处理，再推进回合。
- 保存和读档使用生命周期工具；主菜单读档走原生 FrontEnd API，不做 OCR 或鼠标点击替代。
- 正常换档使用 `reload_game_save(save_name)`，返回主菜单后加载并保持游戏进程；未决动作、保存、加载或推进须先确认。旧菜单入口不自动退出对局。
- 连接失败或挂起不会透明重启、重放。`restart_and_load` 是显式完整重启的故障恢复入口，菜单换档失败不自动调用它；恢复后重新读取当前游戏状态。

## 开发与验证

- 核心调用链：MCP → GameState → Lua 构造与解析 → FireTuner → Civ6。优先删除非必要职责，保持领域工具稳定。
- 修改时保留已有工作树内容，只提交本次改动。默认沿用当前分支；未经明确授权不 push、不改写提交历史。
- 离线验证：`uv sync --locked`、`uv run --locked pytest -q`。pytest 默认仅收集 `tests/`。
- `scripts/test_connection.py`、`test_game_state.py`、`test_queries.py` 与 `integration_turns.py` 连接真实游戏，不能作为离线测试运行。
- `integration_turns.py` 是 stdio MCP 客户端。`--save` 显式授权加载指定存档并替换已加载会话；省略时也加载该唯一基准。CLI 默认并仅允许 `benchmark_turn_1`；其固定策略只属于测试驱动，不能移入 Runtime。遇到挂起、结果未知、未知阻塞或时间限制立即退出。
- 实机测试必须报告使用的存档、实际执行动作和结果；未实测不得声称连续回合或恢复验收通过。
- 随附存档在 `fixtures/saves/`。历史日志在 `fixtures/archive/`，历史研究在 `docs/archive/`；它们不是当前运行时依赖。

当前架构见 [docs/architecture.md](docs/architecture.md)，安装与启动见 [README.md](README.md)。
