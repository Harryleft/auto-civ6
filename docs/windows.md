# Windows 运行说明

项目保留 macOS 现有运行方式，同时支持 Windows 上的 Steam 版《文明 VI：风云变幻》。需要 Python 3.12+、uv 和 stdio MCP 客户端。

## 安装与连接

```powershell
git clone https://github.com/Harryleft/auto-civ6.git
Set-Location auto-civ6
uv sync --locked
```

此命令会在 Windows 上安装桌面启动所需的 `pywin32`；macOS 不安装这项平台依赖。

启用游戏配置中的 `EnableTuner 1`，在用户选项中设置 `AutoEndTurn 0`，重启游戏再加载存档。当前版本的 Windows 配置通常在 `%LOCALAPPDATA%\Firaxis Games\Sid Meier's Civilization VI\AppOptions.txt` 和 `UserOptions.txt`；先检查实际存在的文件，不直接新建或覆盖整份配置。

FireTuner 应监听 `127.0.0.1:4318`。查询命令：

```powershell
Get-NetTCPConnection -LocalPort 4318 -State Listen
```

配置 Codex 时使用实际项目绝对路径；如果 uv 不在客户端 PATH 内，`command` 填 uv.exe 的绝对路径：

```toml
[mcp_servers.civ6]
command = 'uv'
args = ['run', '--locked', '--directory', 'C:\projects\auto-civ6', 'civ-mcp']
tool_timeout_sec = 300

[mcp_servers.civ6.env]
PYTHONUTF8 = '1'
```

macOS 的客户端使用同一个 `civ-mcp` 入口，将目录替换为 Mac 上的实际绝对路径。没有共用 Windows 盘符的要求。修改配置或更新正在运行的 Python 服务后，重新加载 MCP 服务；普通知识查询不需要重启游戏。

如果代码已更新但当前会话仍报 `Knowledge query timed out`，先重新加载客户端中的 `civ6` MCP；客户端没有单独重载入口时，完全退出并重新打开 Codex，再继续当前项目对话。更新磁盘文件不会替换常驻 Python 进程中已经导入的函数。单独启动一个新验证进程成功，也不代表旧会话已经更新。不要通过增大查询超时来掩盖这一差异。

首次连接先调用 `get_game_overview`，再读取单位、城市。只查离线知识时可直接使用 `search_knowledge`、`get_knowledge`，无需启动游戏。

## Steam 与存档位置

Windows 启动器会先从注册表查询 Steam 的实际位置，再读取 `steamapps/libraryfolders.vdf` 发现外部游戏库，也保留默认目录回退。支持 Steam 安装在 S 盘、游戏安装在 D 盘这类布局；macOS 的 Steam URI 启动逻辑保持原样。

Windows 会委托现有桌面 Shell 启动 Steam 和游戏，不将它们作为 MCP 的后台子进程启动。部分 MCP 客户端会用 Windows 作业对象在会话结束时清理子进程；普通 `Popen` 或仅设置进程分组标志不能可靠隔离游戏。桌面启动失败时明确返回错误，不回退到上述受管理进程树。此路径需要用户登录后的交互式桌面，不适用于无桌面的 Windows 服务会话。重载 MCP 不应退出游戏；主动结束游戏仍使用显式 `kill_game`。

Windows 存档默认位于系统“文档”目录下的 `My Games\Sid Meier's Civilization VI\Saves\Single`；“文档”重定向到 OneDrive 或其他盘符时，按注册表登记路径定位。

需要指定其他实际存档目录时，可在 MCP 服务环境中添加：

```toml
CIV6_SAVE_DIR = 'D:\GameSaves\CivilizationVI\Saves\Single'
```

此行应放在 `[mcp_servers.civ6.env]` 下，指向完整 `Single` 目录。macOS 也可显式配置该环境变量；不设置时保留原来的 macOS 默认目录。启动器不会自动迁移或覆盖存档。

## 编码与知识查询

- 仓库 `.gitattributes` 要求知识目录保持 LF，防止 Windows 的 CRLF 转换破坏指纹校验。已有检出被转换时应恢复为版本库原始字节，不修改清单中的指纹来掩盖差异。
- 知识 CLI 子进程显式使用 UTF-8，并将输入隔离为 `DEVNULL`，避免继承 MCP 协议管道而超时。
- 项目元数据和日志文件显式使用 UTF-8；客户端环境仍建议设置 `PYTHONUTF8=1`，确保辅助脚本及控制台中文一致。
- 开发测试时将 `PYTHONUTF8=1` 传递给测试及其子进程：

```powershell
$env:PYTHONUTF8 = '1'
uv run --locked python -m pytest -q tests/test_windows_compatibility.py tests/test_knowledge_stdio.py
```

## 验证边界

离线测试不操作真实游戏。实机测试必须明确选定原始基准，并单独保存结果；当前基准说明见 [受信原始存档](../fixtures/saves/README.md)。Windows 实测报告单独记录实际回合和结果，不把跨平台 CI 配置等同于 Mac 真机验收。
