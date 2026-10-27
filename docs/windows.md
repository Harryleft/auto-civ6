# Windows 运行说明

本页补充 Windows 上 Steam 库、中文编码、桌面进程及存档目录的处理。通用依赖、FireTuner 设置和连接验收见[安装与连接](installation.md)。

## 安装与连接

`uv sync --locked` 在 Windows 上安装桌面启动所需的 `pywin32`；macOS 不安装这项平台依赖。

启用游戏配置中的 `EnableTuner 1`，在用户选项中设置 `AutoEndTurn 0`，重启游戏再加载存档。当前版本的 Windows 配置通常在 `%LOCALAPPDATA%\Firaxis Games\Sid Meier's Civilization VI\AppOptions.txt` 和 `UserOptions.txt`；先检查实际存在的文件，不直接新建或覆盖整份配置。

FireTuner 应监听 `127.0.0.1:4318`。查询命令：

```powershell
Get-NetTCPConnection -LocalPort 4318 -State Listen
```

在 PowerShell 中从项目目录启动 CLI（将路径替换为本机实际绝对路径）；CLI 会自行启动内部服务，不需要也不要把它注册成 MCP：

```powershell
uv run --locked --directory C:\projects\auto-civ6 civ6 doctor
uv run --locked --directory C:\projects\auto-civ6 civ6 debug
```

`civ6 debug` 是持续会话：在同一个进程的 stdin 输入命名命令，例如 `state overview`、`unit list`、`turn end`。已有会话时不要为每个动作重启 CLI；普通知识查询在 `debug --offline` 或同一会话内完成，不需要启动游戏。

如果代码已更新但当前会话仍报 `Knowledge query timed out`，它的 `civ6` 会话仍在使用旧的已导入函数：在无未决请求时正常退出，再显式启动新的 `civ6 debug`。更新磁盘文件不会热替换正在运行的 CLI；不要通过增大查询超时来掩盖这一差异。

首次连接先执行 `state overview` 核对文明和回合，再读取单位、城市。只查离线知识时可在 `debug --offline` 会话使用 `knowledge search`、`knowledge show`，无需启动游戏。

## Steam 与存档位置

Windows 启动器会先从注册表查询 Steam 的实际位置，再读取 `steamapps/libraryfolders.vdf` 发现外部游戏库，也保留默认目录回退。支持 Steam 安装在 S 盘、游戏安装在 D 盘这类布局；macOS 的 Steam URI 启动逻辑保持原样。

Windows 会委托现有桌面 Shell 启动 Steam 和游戏，不把它们作为 CLI 的后台子进程启动。部分客户端会用 Windows 作业对象在会话结束时清理其子进程；普通 `Popen` 或仅设置进程分组标志不能可靠隔离游戏。桌面启动失败时明确返回错误，不回退到上述受管理进程树。此路径需要用户登录后的交互式桌面，不适用于无桌面的 Windows 服务会话。重启 `civ6 debug` 不应退出游戏；主动结束游戏仍使用显式 `game stop`。

Windows 存档默认位于系统“文档”目录下的 `My Games\Sid Meier's Civilization VI\Saves\Single`；“文档”重定向到 OneDrive 或其他盘符时，按注册表登记路径定位。

需要指定其他实际存档目录时，在启动 CLI 的同一个 shell 中设置环境变量，再启动会话：

```powershell
$env:CIV6_SAVE_DIR = 'D:\GameSaves\CivilizationVI\Saves\Single'
uv run --locked civ6 debug
```

变量指向完整 `Single` 目录。macOS 也可在启动 CLI 前显式设置该变量；不设置时保留原来的 macOS 默认目录。启动器不会自动迁移或覆盖存档。

## 编码与知识查询

2026-10-27 在 Windows 11 Build 26200、Python 3.12.2、cp936 环境验证：完整路径只含 ASCII 的检出可以运行离线 CLI；中文目录的 editable 安装虽然完成，Python 仍无法导入 `civ_mcp`。原因是 `.pth` 的 UTF-8 路径被解释器按 locale 解码，`PYTHONUTF8=1` 不能修复该读取路径。此组合先使用如 `C:\projects\auto-civ6` 的 ASCII 检出目录；中文参数、日志文件名和证据输出目录已分别通过验证。完整结果及未通过项见 [Windows 11 CLI 验收](acceptance/2026-10-27-windows11-cli.md)。

- 仓库 `.gitattributes` 要求知识目录保持 LF，防止 Windows 的 CRLF 转换破坏指纹校验。已有检出被转换时应恢复为版本库原始字节，不修改清单中的指纹来掩盖差异。
- 知识 CLI 子进程显式使用 UTF-8，并将输入隔离为 `DEVNULL`，避免继承当前会话的 stdio 管道而超时。
- 项目元数据和日志文件显式使用 UTF-8；客户端环境仍建议设置 `PYTHONUTF8=1`，确保辅助脚本及控制台中文一致。
- 开发测试时将 `PYTHONUTF8=1` 传递给测试及其子进程：

```powershell
$env:PYTHONUTF8 = '1'
uv run --locked python -m pytest -q tests/test_windows_compatibility.py tests/test_knowledge_stdio.py
```

## 验证边界

当前 Windows／UU 终端的离线会话已复现：空闲时按 Ctrl+C，打印 `Session retained` 后仍退出；不能将这条提示当作原 owner 仍在的证据。已有真实未决请求的会话不要用该按键做兼容性实验。共享 Unix socket 转接器和既有 macOS 热座控制器也未通过 Windows 验收；普通单人前台 CLI、平台夹具和原生游戏路径分别判定。

离线测试不操作真实游戏。实机测试必须明确选定原始基准，并单独保存结果；当前基准说明见 [受信原始存档](../fixtures/saves/README.md)。Windows 实测报告单独记录实际回合和结果，不把跨平台 CI 配置等同于 Mac 真机验收。
