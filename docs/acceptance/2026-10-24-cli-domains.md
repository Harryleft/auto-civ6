# CLI 领域化与自动实机只读验收

新增会话内 `unit/city/research/turn/save/knowledge` 子命令，以及一次性 `civ6 verify state`、会话内 `verify state` 和结构化 `state snapshot`。全部通过现有 MCP 调度和 Runtime，不增加另一份请求所有者。用法见[调试 CLI](../cli.md)。

## 边界

领域参数转换在 `devtools/commands.py`，共用真实 schema 校验、dry-run、offline 限制、传输未知门禁及证据记录。副作用命令只在明确的不带 dry-run 调用时发送一次；自动验收本身只有两个固定只读目标工具，不自动解决阻塞、加载、推进或重启。

`snapshot.py` 只采集公开领域数据并比较读取前后范围；工具锁防止同服务变更插入。由于普通实体查询可能确认已有动作，结构化快照在任何游戏 I/O 前拒绝全部 pending。单位列表限定已列的地图内单位，可能少于概览总数；城市须匹配原生身份与所有者。读取失败或不完整只能返回 unknown／blocked。

自动验收还识别旧概览工具以 `Error:` 文本表示的失败，避免因 MCP isError=false 漏报；成功判断依据结构化原生数据。服务启动失败、未知传输、身份不符和未决上下文不获得通过。

## 离线检查

- 相关离线集合共 260 项通过；最终 `civ6 test debug` 覆盖前台会话、领域子命令及服务协议，235 项通过。通用 CLI 另有 25 项通过。
- 覆盖严格参数转换、分层帮助、全部变更入口的预演、offline 不可绕过、同一原生动作未知后重复意图仅确认一次提交、pending 期间快照不消费确认、世界／回合变化及只读失败不授予通过、一次性验收的单 JSON 输出和原始证据。
- 最终领域证据在 `.scratch/civ6-domains-final-regression/`；完整集合输出 `.scratch/civ6-domain-final-tests.txt`，旧概览错误边界补强后的 31 项定向检查在 `.scratch/civ6-domain-session-final.txt`。
- compileall、差异空白和本地文档引用检查通过；wheel 构建成功，包内 CLI、领域解析、自动验收、服务和快照源码与最终源码逐字节一致。

## 实机检查

本轮原生 T136 瑞典现场的自动验收及 25 条领域会话命令已通过，详见[实机报告](../../games/2026-10-24-dual-ai-four-leaf/runs/20261024-084444-hotseat/continuations/20261024-151635-t101-end/reports/2026-10-24-cli-domain-verification.md)。实测 13 次查询和 8 次预演，快照一致，原存档不变；错误回合预期返回阻塞。实际游戏变更及保存／加载／推进未在该暂停现场执行，不计入实机通过范围。
