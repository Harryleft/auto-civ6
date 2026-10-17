# civ6-mcp

本机 Civilization VI MCP 工具服务，让模型读取真实游戏状态、操作游戏并确认结果。通过 stdio 连接 MCP 客户端，通过 FireTuner 的 TCP 4318 端口连接游戏。

## 能力与边界

提供 76 个工具，覆盖单位、城市、地图、科研文化、政策总督、宗教、伟人、外交贸易、世界议会、胜利进度，以及启动、保存、读档和显式恢复。

- 动作工具回读游戏状态；模型基于返回结果决定下一步。
- `end_turn` 检查阻塞、推进回合并报告结果，无 reflection 参数，也不强制反思。
- 生产、研究、外交、占城、议会等决策由模型明确调用工具完成。
- 无网页、云同步、批量评测、后台镜头或弹窗轮询服务，也不提供 `get_diary`。
- 连接异常或回合挂起时报告问题，不透明重启游戏或重放动作。需要恢复时显式调用 `restart_and_load`，再读取当前状态。

## 安装与启动

需要 Civilization VI（Steam 版，Gathering Storm）、Python 3.12+、uv 和一个 MCP 客户端。

1. 在游戏设置或 `AppOptions.txt` 中启用 FireTuner，并关闭自动结束回合。更改后重启游戏。
2. 手动加载存档，或通过生命周期工具从主菜单加载。macOS 的 Aspyr 启动器设置见 [启动说明](docs/research/bypassing_aspyr_launcher.md)。
3. 安装依赖并验证连接：

```bash
uv sync --locked
uv run python scripts/test_connection.py
```

服务启动命令：

```bash
uv run civ-mcp
```

仅本机 MCP 服务需要 `mcp`；离线测试依赖 `pytest`。启动和原生读档不依赖 OCR。

## 配置 MCP 客户端

以 Codex 为例，将项目绝对路径填入配置：

```toml
[mcp_servers.civ6]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/civ6-mcp", "civ-mcp"]
```

其他 stdio MCP 客户端使用相同命令与参数。仓库 `.mcp.json` 提供对应 JSON 配置。

连接后可要求模型：

> 读取当前概览、单位和城市，根据真实状态完成当前回合；先解决阻塞，再推进回合。动作结果不明确时先查询确认。

## 保存与恢复

`save_game(save_name)` 将当前单人游戏保存为本机普通存档。工具确认文件已更新、非空且短暂稳定，并核对文明、种子和保存前后回合一致后才报告成功。它不证明存档能加载；恢复能力需实机验证。同名存档会被覆盖，检查点应使用唯一名称。

结果为 `OUTCOME_UNKNOWN` 时，先检查实际文件和游戏状态，不立即重发保存。回合推进结果尚未确认时，工具拒绝建立检查点。

恢复时显式调用 `restart_and_load(save_name="已确认的检查点名称")`，随后用 `get_game_overview` 核对文明、种子和回合。读取检查点会丢失保存后的操作；只有已确认的检查点满足允许的回退范围时才能恢复。省略名称会选择游戏自动存档，该路径不等同于已验证的命名检查点恢复。

## 验证与资料

```bash
# 离线测试；默认只收集 tests/，不会执行实时游戏脚本
uv run --locked pytest -q

# 检查随附存档安装位置，不写入游戏目录
uv run python scripts/install_saves.py --dry-run
```

`integration_turns.py` 通过真实 stdio MCP 服务诊断游戏；显式运行后会操作游戏，不属于离线 CI：

```bash
# --save 显式加载该存档；已加载其他游戏时会重启并替换当前会话
uv run python scripts/integration_turns.py --save 0C_CRY_HAVOC --turns 10 --timeout 900

# 省略 --save，仅使用已经加载的游戏
uv run python scripts/integration_turns.py --turns 3
```

驱动采用固定测试策略跳过单位、选择首个可用科研文化和生产项；其他阻塞明确中断。只有确认回合增加才计数，挂起、结果未知或超出时间/阻塞次数时退出，不自动恢复或重发动作。

[本次保存与巴比伦验收记录](docs/acceptance/2026-10-17-babylon.md) · [架构与文档](docs/README.md) · [模型操作规则](AGENTS.md) · [存档和历史数据](fixtures/README.md)

历史研究、游戏报告及旧架构保存在 `docs/archive/`，仅作参考。

## License

MIT
