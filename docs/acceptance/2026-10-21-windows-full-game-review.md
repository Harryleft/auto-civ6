# Windows 神级完整对局修复与提交复核

## 结果与范围

本轮从受信原始基准开始，苏美尔在第 243 回合取得原生外交胜利，保留四城、66 人口。第 242 回合达到 20 点，第 243 回合才确认获胜队伍和胜利类型，公开 `end_turn` 随后停止推进。终局、检查点指纹和香港收益判断见[终局报告](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/final.md)及[结构化终局证据](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/terminal-evidence.json)。

本次复核覆盖此前未提交的 Windows 兼容、知识查询、进程生命周期，以及本轮实际遇到的运行层问题。沿用 `main`，保留 GitHub 新增的技能安装和资料归档提交；本地 `.agents/` 技能副本不混入本次提交。原始日志、截图、存档与临时环境保留本地，提交轻量报告和必要指纹。

## 问题、修复与实机结果

以下回合报告均属于同一[Windows 完整对局轮次](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/README.md)。

| 问题 | 修复边界 | 验证依据 |
| --- | --- | --- |
| 知识 MCP 超时 | CLI 输入隔离为 `DEVNULL`，显式 UTF-8；已加载旧代码的服务仍须重连，不靠放大超时掩盖 | 真实 stdio 查询、错误与超时清理测试；新 MCP 中文、英文及各层级查询成功，见[续查](2026-10-20-windows-stability-followup.md) |
| Windows 路径和编码 | 注册表发现重定向文档目录、Steam 与外部库；存档绝对路径可配置，知识内容保持 LF，辅助脚本使用 UTF-8 | [兼容报告](2026-10-20-windows-compatibility.md)，独立轮次净推进 10 回合 |
| MCP 关闭连带退出游戏 | 委托已有交互式桌面 Shell 启动，COM 在线程内初始化和释放；失败不回退到受作业对象管理的子进程 | 原生进程句柄和真实 stdio 关闭测试；游戏同轮 11→21、三次断开后存活 |
| T22 多个外交会话无法识别当前对象 | 仅在原生领袖界面核对选中的参与者；证据不足保留未知，交易入口核对对象 | [T22](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t022-diplomacy-blocker.md)，实际推进 22→23 |
| T23 派遣被拒后无法结束确认 | 要求精确请求、会话和世界中的原生明确拒绝，并核对未产生派遣或扣费 | [T23](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t023-access-refusal-blocker.md)及反例测试 |
| T38 信条候选包含已占用项 | 查询使用原生占用及宗教类别限制，与提交资格一致 | [T38](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t038-belief-availability.md)，原生完成职业道德、什一税 |
| T63 隐藏外交会话阻止推进 | 新增显式 `close_diplomacy_session`；要求原会话关闭事件及缺席，不代选回应 | [T63](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t063-hidden-diplomacy.md)，实际推进 63→64 |
| T67 传教后界面压力缓存未刷新 | 仅在原请求消耗与原生见证齐全时，使用本方原城市中心 GameCore 信徒增加作为补充证据 | [传播记录](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t063-spread-pressure-blocker.md)；不能反推早先无效果的 T63 操作成功 |
| T98 已结束议会重复查询登记未来请求 | 保留刚完成的同计划回执；新原生投票阶段仍可登记 | [T98](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t098-congress-recheck.md)，实际推进 97→98 |
| T131 友谊被拒后悬挂 | 正确关联原生 `DECLARE_FRIEND` 拒绝事件，并核对双边状态未变、会话已闭合 | [T131](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t131-friendship-refusal.md)，实际推进至 132 |
| T138 动画期间保存无法完成 | 提交保存的同一原生调用中检查已知信息弹窗与动画；已知零提交返回明确拒绝，未知仍保留门禁 | [T138](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t138-save-popup.md)，关闭弹窗后确认新鲜存档 |
| T154 外交支持收入漏算 | 使用原生 `GetFavorPerTurn()` 净收益，包含城墙、奇观和惩罚 | [过程](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/progress.md)，156 回合核对 +12 及余额增长 |
| T159 进入平民格不消耗攻击次数而悬挂 | 原近战请求进入目标格且移动力减少时，仅确认进入；目标命运保持未知 | [T159](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t159-civilian-entry.md)，实际推进 159→160 |
| T179 无法提交破坏火箭任务 | 补上原生 `DISRUPT_ROCKETRY` 映射及公开说明，沿用目标与回执确认 | [T179](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t179-rocketry-operation.md)，189、209、214 原生任务成功；后续失败照实保留 |
| T214 控制区内原地结束移动悬挂 | 同时要求原请求身份、结束段事件、原位置、控制区和精确队列；确认受阻并释放原观察器 | [T214](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t214-stationary-zoc.md)，显式取消路径后推进 214→215 |
| T228 战略资源收支漏算与重复进口 | 两处查询收入包含进口和奖励，消耗包含单位与发电；回合报告不再重复加进口 | [T228](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t228-resource-income.md)，原生顶栏及 229、230 回合报告一致 |

## 本次提交前审查

- 核对动作保持单次提交；缺失事件、错误世界或实体、忙碌、错误队列仍不能释放未决门禁。成功结果只描述实际证据，不推断击杀、俘获或奖励。
- Windows 桌面启动直接依赖 `pywin32`，现补充平台条件声明及锁文件。当前 MCP 依赖已间接安装该包，不能将其描述成已复现的缺包故障。
- 发现知识捕获 CLI 的快照写入仍使用 Windows 默认代码页，现显式读写 UTF-8，并新增关闭继承 UTF-8 模式的 Windows 子进程回归。
- 知识查询测试的 CLI 子进程显式采用 UTF-8，避免仅给父解释器传 `-X utf8` 造成读写编码不一致。完整测试仍按项目约定传递 `PYTHONUTF8=1`。
- 更新 MCP 协议测试的工具数量为 84，并核对新增关闭会话工具的必需参数。
- 检查提交候选中的常见密钥模式、JSON 可解析性和文件大小；未发现匹配密钥或超过 1 MB 的新增提交候选。此检查不等同于完整安全审计。

## 当前验证

使用 Python 3.12.2、锁定依赖和独立 `.scratch/review-venv`，避免改动被现有 MCP 进程占用的环境。`uv sync --locked` 在该干净环境完成，包含 Windows 的 `pywin32 311`；本次未重新操作游戏。

最终复核结果：

| 检查 | 结果 |
| --- | --- |
| 干净环境锁定安装 | `UV_PROJECT_ENVIRONMENT=.scratch/review-venv uv sync --locked` 完成，38 个包 |
| Windows 全量离线回归 | `PYTHONUTF8=1` 下运行 `python -X utf8 -m pytest -q --tb=short --junitxml=.scratch/review-tests-final.xml`：**5376 passed, 1 skipped，230.09 秒** |
| 跳过项 | `test_public_query_preserves_native_utf8_names_in_control_sensitive_locale`：本机没有符合要求的 libc UTF-8 locale；不是桌面进程存活测试被跳过 |
| 本次编码、协议与 Windows 专项 | 知识捕获、知识查询、公开 MCP、路径及桌面进程五组测试：230 passed，19.08 秒；其后的保存夹具调整已包含在最终全量中 |
| 编译 | `python -X utf8 -m compileall -q src tests civ6_knowledge scripts/parse_save.py scripts/split_game_log.py` 通过 |
| 文档及数据 | 90 个候选文档本地链接均可解析；配置及终局 JSON 可解析 |
| 差异检查 | 提交前逐组 `git diff --cached --check` 通过；大型原始证据与临时环境被忽略 |

首轮检查曾失败，原因包括知识 CLI 子进程的代码页、旧工具数量断言；后续全量还暴露保存测试夹具仅 30 毫秒的预算不足。现已修正编码、断言与 Windows 测试调度余量，最后重新启动完整测试进程得到上述全量通过结果，没有修改游戏运行层超时来通过测试。原始 JUnit 文件保留本地 `.scratch/review-tests-final.xml`。

Windows 修复提交为 `cc75ac8`，运行层修复与测试提交为 `5094806`；终局与本报告单独归档。GitHub 的 `77e325a` 及此前技能、资料归档更新已通过快进合入，没有改写历史。

## 遗留问题与证据边界

- **T110 原生 APPCRASH 根因未明。** 已有 Windows 错误事件和异常信息，不能据此断言驱动、DX12 或某条 MCP 查询是原因，见[原生崩溃报告](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/t110-native-crash.md)。
- 本次复核时，艺术家／作家普通 `activate` 返回 `NO_CHARGES` 的创作路径尚未修复；后续已完成[创作修复及实机验收](../../games/2026-10-19-gold/runs/2026-10-20-windows-full-game-01/reports/2026-10-21-great-work-creation.md)。间谍被俘、逃离后下落不明及战略损失不属于已修复收益。
- 对局中发生过明确记录的技术恢复，胜利不能证明 243 回合无中断运行。
- Windows 实机证据与离线测试分别记录；没有新增 macOS 模拟验收或重测 Mac 实机。跨平台 CI 的配置也不代表其运行已通过。
- 原始证据路径在其他电脑上可能不存在；仓库保留轻量回执和指纹，完整原始日志与存档仍在本机。
