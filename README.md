# civ6-mcp

本地 Civilization VI MCP 服务，让 AI 读取真实游戏状态、操作游戏并确认结果。通过 stdio 连接 MCP 客户端，通过 FireTuner 连接游戏。

## 如何使用

### 安装与启动

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 MCP 客户端；操作游戏需要 Civilization VI（Steam 版，Gathering Storm）。

1. 启用游戏的 FireTuner，关闭自动结束回合，重启游戏并加载存档。macOS 启动设置见[启动说明](docs/research/bypassing_aspyr_launcher.md)。
2. 下载项目并安装依赖：

```bash
git clone https://github.com/Harryleft/auto-civ6.git
cd auto-civ6
uv sync --locked
```

### 配置 MCP 客户端

以 Codex 为例，在配置中填写项目的绝对路径：

```toml
[mcp_servers.civ6]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/auto-civ6", "civ-mcp"]
```

其他 stdio MCP 客户端使用相同命令与参数，也可参考 [.mcp.json](.mcp.json)。客户端会启动服务；手动启动可运行 `uv run civ-mcp`。

Windows 安装、FireTuner 配置、中文编码和存档目录设置见 [Windows 运行说明](docs/windows.md)。macOS 继续使用上面的安装方式和原有启动设置。

### 开始使用

连接后，可向模型发送：

> 读取当前概览、单位和城市，根据真实状态完成当前回合；先解决阻塞，再推进回合。动作结果不明确时先查询确认。

以获胜为目标开局或续局时，先读[策略检查清单](docs/agent-checklist.md)。常规动作见[游戏操作指南](docs/agent-playbook.md)，议会、交易和外交等交互见[特殊操作指南](docs/agent-special-operations.md)，读档与故障处理见[保存与恢复](docs/agent-recovery.md)。只查[离线规则知识](civ6_knowledge/README.md#mcp-查询)时无需启动游戏。

代码、文档修改及验证见[开发与验证](docs/development.md)；完整入口见[文档导航](docs/README.md)。
