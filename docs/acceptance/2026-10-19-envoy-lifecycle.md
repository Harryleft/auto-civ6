# 使者生命周期离线验收

2026-10-19；基线 `b24fd1d`。对应[全量审计与分批工作项](../specs/2026-10-19-mutation-audit.md)。本次完成全量静态审计和使者试点，其他操作的结果所有者迁移仍为后续工作项。

## 结果

- 覆盖 82 个已声明工具、其中 43 个变更工具；展开单位、城市、间谍、外交等多分支入口，列出隐藏 UI／草案／后台／存档副作用和非 MCP 入口。以 Python AST 提取工具名核对文档，遗漏为 0。
- 真实 MCP 函数 → GameState → ActionLifecycle → 实际生成的 Lua 5.1 链路中，注入“原生已执行但回应丢失”：首次保留 OUTCOME_UNKNOWN，重复同目标仅确认；原生提交数保持 1，可用使者从 3 变 2，目标从 2 变 3。
- 新增 60 个参数化测试实例。覆盖延迟完成、原生加成、错目标、单侧变化、多次消费、空回应／冲突回应、超时、取消、原生抛错、断线回读恢复、资格缺失、非法参数、世界失效锁存和门禁。
- 额外测试捕获并修复调度漏洞：unit_action 的自确认豁免只适用于单位所有者，不能绕过未决使者。sleep、heal、automate、alert、spread_religion、activate 均经过使者门禁。

## 实际验证

```bash
.venv/bin/python -B -m pytest -q \
  tests/test_envoy_confirmation.py tests/test_pending_dispatch.py \
  tests/test_action_scope.py tests/test_royal_contribution.py tests/test_server.py \
  tests/test_game_state.py tests/test_unit_readiness.py \
  tests/test_city_focus.py tests/test_spy_escape.py
```

结果：**690 passed in 6.52s**。其中使者新增 60 项，其余 630 项验证公共动作所有者、MCP 调度及相关既有动作。旧的两个“发送回执＋剩余计数即成功”测试由实际 Lua 行为测试替代。

补充检查：108 个 Python 文件内存编译通过；本次修改的 Markdown 本地链接检查通过；`git diff --check` 通过。

红灯证据：首个丢回应测试在旧实现返回 `Error: native request ran; response lost` 时失败；补充的 6 个单位门禁测试在修复前全部失败，其中 sleep 实际返回 SLEEPING。均在对应实现修复后转绿。

本次没有连接 FireTuner、启动／关闭游戏、换档或修改真实存档。Lua 原生引擎由测试 adapter 模拟；本机原版 CityStates.lua 只用于核对调用方式。未验证当前游戏版本、DLC／模组的真实回调时序；后续用户明确排除人工或第三方并发；MCP 进程重启后的意图恢复不在本次保证内。

## 实现取舍

复用现有 ActionLifecycle，仅增加 envoy 分支；领域 Lua 放在 `envoys.py`，替换原有直接提交 builder。没有增加新状态机或后台任务。原生记录在提交块内采集两侧基线，确认块不发游戏操作。只接受明确的提交前拒绝释放 pending，普通错误文本不足以证明零提交。

重复调用的含义受未决状态约束：有同目标未决时为确认，确认后下一次独立调用为新派遣。`get_city_states` 可以查看当前事实，但不擅自退役原意图。本轮后续规格不扩展跨服务重启的意图持久化。
