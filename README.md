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

### 开始使用

连接后，可向模型发送：

> 读取当前概览、单位和城市，根据真实状态完成当前回合；先解决阻塞，再推进回合。动作结果不明确时先查询确认。

具体操作见[游戏操作指南](docs/agent-playbook.md)，读档与故障处理见[保存与恢复](docs/agent-recovery.md)。只查[离线规则知识](civ6_knowledge/README.md#mcp-查询)时无需启动游戏。
