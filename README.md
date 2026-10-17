# civ6-mcp

本机 Civilization VI MCP 工具服务，让模型读取真实游戏状态、操作游戏并确认结果。通过 stdio 连接 MCP 客户端，通过 FireTuner 的 TCP 4318 端口连接游戏。

## 能力与边界

提供 78 个工具，覆盖单位、城市、地图、科研文化、政策总督、宗教、伟人、外交贸易、世界议会、胜利进度，以及启动、保存、读档和显式恢复。

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

`save_game(save_name)` 将当前单人游戏保存为本机普通存档。工具同时确认原生 `SaveComplete` 事件、对应的新鲜非空文件，以及保存前后文明、种子和回合一致后才报告成功。文件短暂不变不能单独证明保存完成；能否恢复仍需实际加载验证。检查点使用独立名称，原始基准不得覆盖。

结果为 `OUTCOME_UNKNOWN` 时，先检查实际文件和游戏状态，不立即重发保存。动作或回合推进尚未确认时，工具拒绝建立检查点；保存或加载结果未知时，暂停依赖其结果的玩法操作。

正常换档调用 `reload_game_save(save_name="已确认的检查点名称")`：返回主菜单并加载，保持游戏进程。工具在退出前验证检查点，拒绝未决动作、保存、加载或推进；确认菜单后清理旧缓存和保存列表索引，再核对目标指纹、文明、种子、回合、可操作状态及完整新快照。返回菜单最多 30 秒，包含在加载总预算 180 秒内；退出和加载各只提交一次。

[本机配对验收](docs/acceptance/2026-10-17-menu-save-reload.md)中，菜单路径26.89～28.80秒，完整重启62.13～62.93秒；两个源／目标组合各测一次，菜单路径PID均保持不变。

进程挂起或失联时，显式调用 `restart_and_load(save_name="已确认的检查点名称")` 完整重启。菜单换档失败不会自动升级为完整重启。换档会丢失保存后的操作，检查点必须来自当前轮次，不按「最新自动存档」推断来源。`load_game_save`、`load_save_from_menu` 和索引入口 `load_save` 仍只接受已在主菜单的调用。

每次成功加载并核实 `benchmark_turn_1` 原件，都会建立新的测试轮次。检查点记录绑定该轮次标识，MCP 重启和本轮检查点恢复会保留标识。以前轮次的结果存档仍可保留，但不能作为当前轮次的恢复兜底；缺少轮次记录的旧检查点同样不可用。后续测试始终从原始基准开始。

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
uv run python scripts/integration_turns.py --save benchmark_turn_1 --turns 10 --timeout 900

# 省略 --save 同样从唯一原始基准 benchmark_turn_1 开始
uv run python scripts/integration_turns.py --turns 3
```

CLI 只允许 `benchmark_turn_1`，运行会替换当前对局。驱动采用固定测试策略跳过单位、选择首个可用科研文化和生产项；其他阻塞明确中断。只有确认回合增加才计数，挂起、结果未知或超出时间/阻塞次数时退出，不自动恢复或重发动作。

[修复与 20 净回合复验](docs/acceptance/2026-10-17-confirmation.md) · [修复前基准记录](docs/acceptance/2026-10-17-benchmark.md) · [架构与文档](docs/README.md) · [模型操作规则](AGENTS.md) · [存档和历史数据](fixtures/README.md)

历史研究、游戏报告及旧架构保存在 `docs/archive/`，仅作参考。

## License

MIT

世界议会由 `open_world_congress` 显式开始投票界面，再用 `get_world_congress` 读取议题、`queue_wc_votes` 提交明确选择，最后 `end_turn` 确认原推进。读取不打开界面；无选择阶段用明确的空计划 `[]`，不触发可能自动提交的 Intro 开始事件。已提交、状态未知或丢应答时不重复开始／投票。

第50至100回合实机结果与已知限制见[调度验收](docs/acceptance/2026-10-17-turn-100-scheduling.md)。
