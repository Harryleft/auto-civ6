# Windows 知识超时与游戏退出续查

## 结论

本机当前 Codex 会话的知识超时来自尚未重新加载的旧 MCP 进程。按实际 Codex 配置启动的新进程查询成功。另已复现并修复一种游戏退出机制：Windows MCP 客户端清理其作业对象时，会连同由服务直接启动的 Steam 和游戏一起结束。

修复后的实机从同轮第 11 回合检查点推进到第 21 回合；三次测试 MCP 会话依次关闭后，Steam 和游戏进程仍保持不变。具体过程、检查点和原始证据见[实机续测报告](../../games/2026-10-19-gold/runs/2026-10-20-windows-compatibility-t001-t011/reports/windows-stability-followup.md)。

**当前 Codex 已有连接仍需重载。** 磁盘代码和独立验证进程已经生效，不能据此宣称当前会话的旧服务已经更新。现有工具没有重载这条 Codex 宿主连接的接口；请重载 `civ6` MCP，或完全退出并重新打开 Codex，再继续本项目。无需为知识查询重启游戏。

## 知识查询诊断

| 对照 | 观测 |
| --- | --- |
| 当前会话工具 `search_knowledge(query="Writing", limit=1)` | 返回 `Knowledge query timed out` |
| 常驻进程 | `uv` PID 24592，最终 Python PID 39608；启动于北京时间 2026-10-20 15:06:16 |
| 磁盘修复文件 | `knowledge.py` 修改时间为北京时间 15:16:49，晚于服务启动 |
| 使用实际 `~/.codex/config.toml` 启动新服务 | 中文、英文标识搜索及 summary/rule/evidence 共 5 次成功，单次约 0.81～0.89 秒 |
| 游戏运行期间及再次重连后 | 6 次知识调用全部成功，日志耗时 2.875～5.953 秒 |

上一阶段的 `stdin=DEVNULL` 和显式 UTF-8 修复保留。本阶段没有扩大 10 秒 CLI 超时、跳过指纹校验或重建知识数据。默认查询不需要网络，也不依赖 FireTuner 连接。

本次因果判断依赖旧会话实际失败、新进程实际成功以及进程时间对照；不是仅凭文件内容推断修复有效。重载后若仍失败，应记录新进程 PID、查询参数和返回文本，按新证据继续定位。

## 退出机制与修改

已安装的 MCP Python SDK 1.26.0 为 Windows stdio 服务创建带 `KILL_ON_JOB_CLOSE` 的作业对象。受管理进程直接创建的后代会受此清理影响；普通 `Popen`、新进程组以及本机试验中的 breakaway 标志均不能保证隔离。

使用无游戏、无网络的空闲 Python 子进程复现：关闭真实 SDK stdio 会话后，原启动方式的子进程被终止。修复后的桌面启动子进程在同样的会话关闭后继续存活。原生回归测试验证的是进程句柄状态，随后只回收自己创建的子进程。

`game_launcher.py` 的 Windows 分支新增 `_launch_windows_application`：

- 从 ShellWindows 找到现有桌面，使用其 `Document.Application.ShellExecute` 启动应用。
- 注册表定位的 Steam、Steam URI 和直接 EXE 回退统一经过该入口。
- 在执行线程中初始化和释放 COM，并使用 Windows 参数转义保留中文与空格路径。
- 桌面启动失败时返回明确错误，不回退到受 MCP 管理的 `Popen`。

直接新建 `Shell.Application` 在部分 COM 上下文中仍会在服务内执行；本轮回归测试实际捕获了这一问题，因此最终实现明确使用现有桌面。macOS 启动路径保持原样，本轮未新增或运行 Mac 模拟检查。

此入口面向有交互式桌面的本地游戏会话。原生进程存活测试在无桌面的 Windows CI 中显式跳过，不能将该跳过等同于实机通过。

机制依据：[Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[MCP SDK 1.26.0 Windows 实现](https://github.com/modelcontextprotocol/python-sdk/blob/v1.26.0/src/mcp/os/win32/utilities.py)、[Microsoft FindWindowSW](https://learn.microsoft.com/en-us/windows/win32/api/exdisp/nf-exdisp-ishellwindows-findwindowsw)。

## 其他退出线索与边界

Windows 应用日志在北京时间 14:49:10 有一条与 `CivilizationVI_DX12.exe` 关联的 `RADAR_PRE_LEAK_64` 事件。所查最近两天没有找到关联的 Application Error 1000 或挂起 1002 事件，游戏 `dumps` 与 `packagedDumps` 目录为空。该记录不足以认定内存耗尽、显卡驱动故障或解释每一次历史退出。

本轮已修复并实测的是“关闭 MCP 导致游戏一起退出”。没有修改 DX12 设置、显卡驱动或游戏线程配置，也不声称已排除所有长局崩溃原因。

## 离线验证

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_windows_process_lifetime.py tests/test_windows_compatibility.py tests/test_knowledge_stdio.py tests/test_knowledge_mcp.py
.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_game_launcher.py -k windows
.venv\Scripts\python.exe -X utf8 -m compileall -q src tests
git diff --check
```

相关测试覆盖真实 MCP 关闭、桌面进程存活、COM 清理、带空格和中文参数、Steam/URI/EXE 启动路径，以及知识协议、编码、错误和超时清理。未运行全量测试，不以本记录替代其他模块验收。没有提交或推送代码。

组合检查曾有一次 43 项全部通过。游戏运行负载下复跑为 42 项通过、1 项失败：超时清理测试在 CLI 写出 PID 之前结束了它。该测试已补充就绪握手，兼容 Windows 换行，并断言就绪后的查询超时和回收在 5 秒内完成；修复后定向复跑通过，未放宽生产超时。另有 6 项 Windows 启动器测试通过，编译检查和 `git diff --check` 通过。最终覆盖采用这些分批结果，不宣称最后一次组合或全量测试全部通过。
