# 安装与连接

首次安装、配置 MCP 或排查启动问题时读取。可直接交给 AI 的安装提示词见 [README](../README.md#安装与启动)；已有对局的保存、重启和换档见[保存与恢复](agent-recovery.md)。

## 依赖与客户端

日常操作入口为[项目 CLI](cli.md)：安装依赖后运行 `uv run --locked civ6 doctor`，连续游戏操作运行 `uv run --locked civ6 debug`。CLI 自行启动底层 MCP，日常使用无需额外启用客户端内置 MCP。下方配置保留给 MCP 协议接入；它与 CLI 不能同时控制一个游戏。

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Steam 版《文明 VI：风云变幻》。项目 CLI 内置 stdio MCP 客户端，额外客户端仅用于下方协议接入。已有项目目录时直接复用；首次下载和安装依赖：

```sh
git clone https://github.com/Harryleft/auto-civ6.git
cd auto-civ6
uv sync --locked
```

客户端启动命令为 `uv run --locked --directory <项目绝对路径> civ-mcp`，环境变量设置 `PYTHONUTF8=1`。使用实际绝对路径；客户端找不到 uv 时，填写 uv 可执行文件的绝对路径。

Codex 配置示例：

```toml
[mcp_servers.civ6]
command = "uv"
args = ["run", "--locked", "--directory", "/absolute/path/to/auto-civ6", "civ-mcp"]
tool_timeout_sec = 300

[mcp_servers.civ6.env]
PYTHONUTF8 = "1"
```

Claude Code 的项目配置参考仓库 [.mcp.json](../.mcp.json)；其他客户端使用同一 stdio 入口。合并当前客户端配置，保留已有服务。更新代码或配置后重新加载 `civ6` MCP；常驻进程不会自动载入磁盘上的新代码。

## 游戏配置

先定位实际存在的 `AppOptions.txt` 和 `UserOptions.txt`，仅修改对应选项，保留其他内容：

| 文件 | 设置 | 用途 |
| --- | --- | --- |
| `AppOptions.txt` | `EnableTuner 1` | 启用 FireTuner |
| `UserOptions.txt` | `AutoEndTurn 0` | 由模型显式结束回合 |

配置变更需要重启游戏才能生效。已有对局先按[保存与恢复](agent-recovery.md)核对未决状态并保全现场，再选择显式重启入口。

### macOS

当前本机配置位于 `~/Library/Application Support/Sid Meier's Civilization VI/Firaxis Games/Sid Meier's Civilization VI/`；其他安装先定位现有文件，不能通过新建同名文件推定配置生效。

当前启动器通过 Steam URI `steam://run/289070` 启动游戏，并等待游戏进程及 FireTuner。若 Aspyr 启动器仍要求点击 PLAY，先完成该界面操作，再核对连接；历史[启动器绕过调研](research/bypassing_aspyr_launcher.md)仅供排查参考，其中未验证的脚本不作为默认安装步骤。

### Windows

配置通常在 `%LOCALAPPDATA%\Firaxis Games\Sid Meier's Civilization VI\`。Steam 库、中文编码、桌面启动和存档重定向的具体处理见 [Windows 运行说明](windows.md)。

## 连接验收

1. 在同一 `civ6 debug` 会话中，游戏未运行时按任务授权使用 `call launch_game {}`；已有游戏时保留现场，先只读核对连接。
2. 确认 FireTuner 在 `127.0.0.1:4318` 可连接；端口打开只证明连接可用，不证明已加载正确对局。保持单一活动控制客户端。
3. 主菜单中只加载用户指定存档；尚未指定时先询问。已有对局不为测试连接重新读档。
4. 在该会话运行 `state overview`（`get_game_overview`），核对文明、回合和当前状态，再按需读取城市、单位。安装任务到此完成；推进回合另按用户指令执行。

缺少 Steam、游戏、权限或客户端重载能力时，报告具体所需操作。离线规则知识查询不需要启动游戏，见[知识 MCP](../civ6_knowledge/README.md#mcp-查询)。
