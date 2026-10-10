# 当前架构

目标：本机让模型通过 MCP 玩 Civilization VI，保留游戏操作能力。

```text
MCP 客户端
  │ stdio JSON-RPC
  ▼
server.py：参数、工具入口、结果文本
  ▼
game_state.py / end_turn.py：查询、动作验证、回合推进
  ▼
lua/：按游戏领域构造与解析 Lua
  ▼
connection.py / tuner_client.py：FireTuner 通信
  │ TCP 127.0.0.1:4318
  ▼
Civilization VI
```

游戏规则由引擎执行。GameCore 提供状态读取，InGame 发出操作，FrontEnd 提供主菜单读档。领域查询和动作保持独立，工具返回必要事实和回读结果供模型决策。

`end_turn` 只检查阻塞、发起推进并确认结果；需要生产、科研、外交、占城或议会决策时，由模型调用相应工具。无需反思 payload 或日记前置步骤。

生命周期由 `game_launcher.py` 与 `game_lifecycle.py` 管理。读档和恢复是显式操作，恢复后重建游戏状态，重新确认身份与回合。通信失败不触发隐藏的游戏重启或动作重放；动作结果未知时先回读确认。

日志仅用于本地问题追踪，不参与玩法决策或回合放行。服务不启动网页、云同步、后台镜头、弹窗轮询或终局监测任务。

离线测试只收集 `tests/`。`integration_turns.py` 通过 `mcp.client.stdio` 和 `ClientSession` 启动同一 MCP 服务，复用实际工具的执行、回读与生命周期入口，不另建底层控制路径。驱动的固定策略只处理常见阻塞，异常和结果未知时停止；总时间与每回合处理次数有上限。实机验收需要固定存档并检查动作、阻塞、回合推进和显式恢复。
