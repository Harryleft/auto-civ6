# 9500245 二次评审修复与验证

评审基线：`9500245c8e352dddbdaadf7b8536e820a309eb3b`。2026-09-19 从
`Harryleft/auto-civ6` 同步核对，远端 `main` 与本地基线一致。

## 逐项处理

| 问题 | 修复结果 | 主要回归证据 |
|---|---|---|
| R1 命令提前超时 | 接收等待遵守完整 deadline，一次只读重连重试共享剩余预算 | `test_connection_framing.py`：真实静默 3 秒后返回，30 秒预算内成功 |
| R2 半帧取消串流 | 半帧被取消后 reader 标记失步，超时/断线/取消废弃旧连接；迟到 sentinel 不进入下一命令 | `test_connection_framing.py`：分段 header/body、取消、排空半帧、迟到响应 |
| R3 隐藏自动重启 | 通用管道只记录和分类；异常计数不能启动恢复；明确恢复先检查存档 | `test_runtime_recovery_contract.py`：连续六次未知结果，重启次数为零 |
| R4 重复结束回合 | 整条外层调用与核心操作分别合并重叠请求；发送前保留 pending；取消、弹窗、HANG 不重发 | `test_end_turn_budget.py`、`test_turn_context.py`：回执丢失、并发错序、取消、慢日记、重复等待 |
| R5 InGame 避让遗漏 | pending 直接进入观察；发送锁内及排空后复核阶段；外交/议会使用窄例外；换局后拒绝旧世界排队写入 | `test_connection_framing.py`、`test_turn_in_progress.py`、`test_end_turn_budget.py`：零普通 InGame/零旧世界动作发送 |
| R6 清缓存丢失在途状态 | 缓存失效与确认换局分离；明确未提交保留原请求；未知加载停写，核验新 Lua 世界后才释放 | `test_runtime_recovery_contract.py`、`test_load_cache_lifecycle.py`：缺失存档、无效索引、发送前拒绝、并发加载、同回合读档、明确恢复 |
| R7 等待计时与议会无界循环 | monotonic 起点包含查询和跨调用间隔；预计召开不能证明会话开放；无会话驱动有界，已确认或未知投票不重复提交 | `test_end_turn_budget.py`、`test_lua_golden.py` |
| R8 成功被后处理覆盖 | 推进一经确认立即保存独立回执；核心快照、管道日志、地图、简报的超时或异常均保留确认结果 | `test_turn_context.py`：确认结果加 `BRIEF_PENDING`，并发回执不串 |
| R9 计数器污染共享方法 | 删除运行时方法替换；保留兼容字段 `query_calls=-1`，展示 `calls=unavailable` | `test_turn_context.py`：交错调用、取消、查询失败与后台查询不修改连接方法 |
| R10 场景策略注入 | 移除未核验的苏美尔场景攻略和机制数值；文明能力材料明确缺失，实际时代/难度/规则集仍来自游戏 | `test_turn_context.py`：不同阶段、难度和规则集不注入固定开战策略 |

整链路复查还修复了两个被模拟对象掩盖的实际路径：GameCore 游戏结束查询和世界议会
驱动器的 Lua builder 没有从统一出口导出。现在显式导入，并通过真实 `GameState`
搭配离线连接夹具验证。pending 议会提交内部隐藏的第二次 `ACTION_ENDTURN` 也已移除。

## 实际验证

- `uv run pytest tests/ -q`：**1157 passed**。
- `uv run ruff check src tests`：通过。
- `uv run python -m compileall -q src/civ_mcp src/civ6_belief_engine`：通过。
- `git diff --check`：通过。
- 隔离基线反向验证：旧版静默命令约 2 秒失败、正文被误读为帧长度、排队 InGame
  仍被发送；将修复前源码放到临时目录，新增的无自动重启和读档保留 pending 用例
  出现预期失败。回合取消、共享请求及后处理回执也做了隔离反向验证。

## 验证边界

本次没有连接 FireTuner、没有加载真实存档、没有推进真实回合。上述结果证明离线执行
契约与故障处理，不代表已完成真实游戏长局稳定性验收，也不能证明原生游戏崩溃的根因
已经消失。读档使用的 InGame 临时标记在真实加载中的生命周期仍需现场验证；未能取得
证据时会保守停写。没有游戏端幂等 ID，不声明跨服务进程崩溃的绝对 exactly-once。

恢复与等待语义见 [游戏恢复](agent-recovery.md)，输入材料与简报成本语义见
[回合局面简报](turn-context.md)。
