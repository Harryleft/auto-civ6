# civ6-mcp

本机 Civilization VI MCP 工具服务，让模型读取真实游戏状态、操作游戏并确认结果。通过 stdio 连接 MCP 客户端，通过 FireTuner 的 TCP 4318 端口连接游戏。

## 能力与边界

工具覆盖单位、城市、地图、科研文化、政策总督、宗教、伟人、外交贸易、世界议会、胜利进度，以及启动、保存、读档和显式恢复。

- 动作工具回读游戏状态；模型基于返回结果决定下一步。
- `end_turn` 检查阻塞、推进回合并报告结果，无 reflection 参数，也不强制反思。
- 生产、研究、外交、占城、议会等决策由模型明确调用工具完成。
- 无网页、云同步、批量评测、后台镜头或弹窗轮询服务。
- 连接异常或回合挂起时报告问题，不透明重启游戏或重放动作。正常换档使用 `reload_game_save`，完整重启使用显式 `restart_and_load`；完成后重新读取当前状态。

## 安装与启动

运行服务需要 Python 3.12+、uv 和一个 MCP 客户端。仅查[离线规则知识](civ6_knowledge/README.md#mcp-查询)时不需要启动游戏，可跳过下面的游戏连接步骤；操作游戏另需 Civilization VI（Steam 版，Gathering Storm）。

1. 在游戏设置或 `AppOptions.txt` 中启用 FireTuner，并关闭自动结束回合。更改后重启游戏。
2. 手动加载存档，或通过生命周期工具从主菜单加载。macOS 的 Aspyr 启动器设置见 [启动说明](docs/research/bypassing_aspyr_launcher.md)。
3. 安装依赖并验证连接：

```bash
uv sync --locked
# 此命令连接真实游戏；只在已授权检查连接时运行
uv run python scripts/test_connection.py
```

服务启动命令：

```bash
uv run civ-mcp
```

本机 MCP 服务依赖 `mcp`；离线测试依赖 `pytest`，隔离执行 Lua 5.1 使用开发依赖 `lupa`。启动和原生读档不依赖 OCR。

## 配置 MCP 客户端

以 Codex 为例，将项目绝对路径填入配置：

```toml
[mcp_servers.civ6]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/civ6-mcp", "civ-mcp"]
```

其他 stdio MCP 客户端使用相同命令与参数。仓库 `.mcp.json` 提供对应 JSON 配置。

知识查询与非源码安装的服务器目录配置见[知识入口](civ6_knowledge/README.md#mcp-查询)。

连接后可要求模型：

> 读取当前概览、单位和城市，根据真实状态完成当前回合；先解决阻塞，再推进回合。动作结果不明确时先查询确认。

## 游戏操作与恢复

开局识别本方文明与领袖、相遇后理解对手能力，并结合当前事实选择路线，见[获胜决策](docs/agent-strategy.md)。动作、外交和世界议会的合法执行与回读见[游戏操作](docs/agent-playbook.md)。

正常换档使用 `reload_game_save`，完整重启作为显式故障恢复入口。结果未知时先核对原请求；基准、检查点轮次、保存与加载确认见[保存与恢复](docs/agent-recovery.md)。

## 开发与资料

- Agent 协作从 [AGENTS.md](AGENTS.md) 开始，按任务选择材料。
- 修改、离线测试、实机脚本和提交约束见[开发与验证](docs/development.md)。实机脚本会操作或替换当前对局，独立于离线检查。
- 实现关系、工程规格与离线验收见[文档导航](docs/README.md)；逐局过程、阶段验收与复盘见[对局资料](games/README.md)。
- 安装唯一基准或分析旧日志时读[存档与历史数据](fixtures/README.md)。

## License

MIT
