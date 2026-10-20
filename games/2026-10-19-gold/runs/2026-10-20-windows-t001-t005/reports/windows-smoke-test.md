# Windows 神级第 1 至第 5 回合实机测试

日期：2026-10-20。代码版本：`34887fc`。

## 结论

核心游戏链路实机通过：通过 stdio MCP 连接 FireTuner，加载受信神级原件，从第 1 回合连续净推进 4 次，到达第 5 回合，并确认独立检查点保存完成。

知识 MCP 查询仍超时，尚不能认定全部功能通过。最后改用 Codex 配置启动新会话时，游戏进程一度退出，连接返回拒绝；退出原因尚未定位。随后用该配置入口显式启动成功，并成功加载本轮第 5 回合检查点，文明、种子、回合与分数均通过原生核对。

## 环境与配置

- Python 3.12.2，uv 0.7.13。
- `uv sync --locked` 成功，安装 38 个包，包含 `civ6-mcp 1.1.11`、`mcp 1.26.0`、pytest、lupa 和 pywin32。
- 实际游戏配置位于 `%USERPROFILE%\AppData\Local\Firaxis Games\Sid Meier's Civilization VI`。
- 已确认 `AppOptions.txt` 为 `EnableTuner 1`，`UserOptions.txt` 为 `AutoEndTurn 0`；无需改动这两个设置。
- 实机期间确认 TCP `127.0.0.1:4318` 已监听。
- 已在 `%USERPROFILE%\.codex\config.toml` 新增 `mcp_servers.civ6`，使用本机 uv 的绝对路径、项目绝对路径、`--locked` 和 `civ-mcp`，并设置 `PYTHONUTF8=1`。
- 配置已通过 TOML 解析。当前聊天的工具列表不会因配置文件修改而自动增加工具；实机使用标准 MCP ClientSession 连接相同服务。最后一次会话直接读取并使用已保存的 Codex 配置，初始化、显式启动游戏和检查点加载均成功。

## 基准与保护

- 原件：`benchmark_gold_tun_1.Civ6Save`。
- SHA-256：`0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`，与登记清单一致。
- 文明／领袖：苏美尔／吉尔伽美什；规则：风云变幻；难度：神级。
- MCP 加载确认：`Loaded benchmark_gold_tun_1: sumeria, seed -1896272000, turn 1.`
- 基准使用缺失才复制的安装方式，没有覆盖已有同名文件。
- 原有自动存档备份到本轮 `evidence/pretest-autosaves/`。备份在加载原件后、首次推进前完成，不宣称它早于游戏启动或加载。
- 本轮工具请求和原始回应：`../evidence/mcp-calls.jsonl`。

## 实际动作与回读

| 步骤 | 已确认结果 |
| --- | --- |
| 初始只读检查 | MCP 能响应；游戏未运行时明确返回连接拒绝；启动后识别主菜单并列出存档 |
| 加载基准 | 确认苏美尔、种子与第 1 回合；概览明确显示神级 |
| 读取单位 | 开拓者 65536 位于 (40,25)，勇士 131073 位于 (39,25) |
| 落点评估 | 建城建议将当前 (40,25) 排第一，淡水；可见地块查询与坐标一致 |
| 建城 | `FOUNDED|40,25`；随后回读乌鲁克，城市 ID 65536 |
| 科研 | `RESEARCHING|TECH_ANIMAL_HUSBANDRY`，后续回合持续累积进度 |
| 防御 | 勇士返回 `FORTIFIED|unit:131073|ready:false` |
| 生产 | `PRODUCING|UNIT_SLINGER|8 turns`，城市查询确认队列 |
| 第一次推进 | `Turn 1 -> 2` |
| 第二次推进 | `Turn 2 -> 3` |
| 第三次推进 | `Turn 3 -> 4`，乌鲁克增长至人口 2 |
| 第四次推进 | `Turn 4 -> 5` |
| 终点保存 | 原生完成及文件均确认，562164 字节，文明、种子和第 5 回合一致 |

本次为短程运行验证，采用守备与建设动作，没有修改资源、地图或胜利状态。未将本次测试结果描述为获胜能力验证。

## 第 5 回合终点

- 苏美尔／吉尔伽美什，神级，Score 8。
- 乌鲁克 1 城、人口 2；城市坐标 (40,25)。
- 金币 26，每回合 +5；科研 3.0，文化 1.6。
- 畜牧业研究中，工具显示剩余 2 回合；法典研究中。
- 投石兵生产中，工具显示剩余 4 回合。
- 勇士仍位于 (39,25)。
- 第 4 回合总分与分项读数曾短暂不一致，工具如实标记；第 5 回合总分与分项已一致。

检查点名称：`windows_20261020_deity_t005_verified`。

文件位置：`%USERPROFILE%\Documents\My Games\Sid Meier's Civilization VI\Saves\Single\windows_20261020_deity_t005_verified.Civ6Save`。

保存确认原文：`Saved: windows_20261020_deity_t005_verified; native completion and file confirmed (562164 bytes). Civilization sumeria, seed -1896272000, turn 5.`

保存后文件复核 SHA-256：`f300da592cbe96febbf13813e01b590b33591134c41ba8603c18a7821ffe534e`。

重新加载确认：`Loaded windows_20261020_deity_t005_verified: sumeria, seed -1896272000, turn 5.` 官方分数 `8 -> 8`，正权重分项未变；分数一致本身不证明全部世界状态相同。

## 发现与处理

1. Windows 默认 GBK 导致读取项目元数据时出现 UnicodeDecodeError。Codex civ6 服务环境增加 `PYTHONUTF8=1` 后，服务可初始化；测试用子进程也显式启用 UTF-8。
2. Git Windows 自动换行转换导致知识快照指纹不一致。将知识目录恢复为 Git HEAD 的原始 LF 字节，并在本地 `.git/info/attributes` 为该目录设置 `text eol=lf`。逐文件比对确认其内容与 HEAD 一致，没有改写规则数据或重建快照。
3. 换行恢复后，知识 CLI 搜索及独立 Python 异步知识查询成功。但实际 MCP 会话内 `search_knowledge`、`get_knowledge` 仍返回 `Knowledge query timed out`，尚未定位原因。
4. Steam 在 S 盘、游戏在 D 盘，项目 Windows 备用程序查找只覆盖默认 Steam 根目录。第一次通过实际 EXE 路径辅助启动后，原 MCP 启动调用确认 FireTuner 可用。该启动路径的健壮性未修复到源代码。
5. 测试中的一次地图调用使用了错误参数名，被参数校验拒绝；改用接口规定的 `center_x`、`center_y` 后查询成功。一次城市生产查询使用未核实 ID 得到空候选，随后按 `get_cities` 返回的真实城市 ID 65536 查询成功。这两次查询没有改变游戏状态。

## 验证范围

通过：依赖安装、服务初始化、FireTuner 通信、原始神级存档加载、真实状态读取、建城、科研选择、生产选择、驻防、四次连续推进、独立存档原生保存确认，以及 Codex 同配置下的第 5 回合检查点重新加载。

未通过或未验证：知识 MCP 查询、游戏中途退出原因、长期运行、战斗、交易、宗教、议会等其他玩法。本次未执行全量离线测试，未提交或推送 Git。

后续继续时应从本轮已确认的第 5 回合检查点恢复，先确认游戏进程与 FireTuner，再用原生加载流程核对身份；不要加载其他轮次的旧结果替代本轮检查点。
