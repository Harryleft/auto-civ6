# 本机 Civ6 MCP 协作规则

## 目标与职责

本项目只负责模型通过本机 MCP 玩 Civilization VI，保留真实游戏查询、操作、验证、回合推进和显式生命周期控制。

不重新引入网页、云同步、批量评测、后台观测服务、强制反思或日记协议。策略判断由模型负责，Runtime 不替模型选择占城、议会或外交策略。

## 游戏操作

- 先用 `get_game_overview`、`get_units`、`get_cities` 等工具取得当前回合的真实信息，再使用坐标、单位 ID 和城市 ID。
- 动作返回后的回读数据是后续决策依据；结果未知时先查询，不盲目重复动作。
- 明确区分不可见、未知和不存在；不编造迷雾内的敌人、地图或协议。
- `end_turn` 无 reflection 参数，也无需先写反思。返回阻塞后，调用对应工具处理，再推进回合。
- 保存和读档使用生命周期工具；主菜单读档走原生 FrontEnd API，不做 OCR 或鼠标点击替代。
- 连接失败或挂起不会透明重启、重放。确认需要恢复后显式调用 `restart_and_load`，恢复后重新读取当前游戏状态。

## 开发与验证

- 核心调用链：MCP → GameState → Lua 构造与解析 → FireTuner → Civ6。优先删除非必要职责，保持领域工具稳定。
- 修改时保留已有工作树内容，只提交本次改动。默认沿用当前分支；未经明确授权不 push、不改写提交历史。
- 离线验证：`uv sync --locked`、`uv run --locked pytest -q`。pytest 默认仅收集 `tests/`。
- `scripts/test_connection.py`、`test_game_state.py`、`test_queries.py` 与 `integration_turns.py` 连接真实游戏，不能作为离线测试运行。
- `integration_turns.py` 是 stdio MCP 客户端。`--save` 显式授权加载指定存档并替换已加载会话；省略时使用当前游戏。其固定策略只属于测试驱动，不能移入 Runtime。遇到挂起、结果未知、未知阻塞或时间限制立即退出。
- 实机测试必须报告使用的存档、实际执行动作和结果；未实测不得声称连续回合或恢复验收通过。
- 随附存档在 `fixtures/saves/`。历史日志在 `fixtures/archive/`，历史研究在 `docs/archive/`；它们不是当前运行时依赖。

当前架构见 [docs/architecture.md](docs/architecture.md)，安装与启动见 [README.md](README.md)。
