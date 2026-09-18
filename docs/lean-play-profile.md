# 精简游玩配置（play profile）

本页说明 `CIV_MCP_PLAY_PROFILE` / `--play-profile` 的契约：精简路径到底改变了什么、
哪些保证**没有**改变、如何回退，以及哪些结论仍需现场验证。

## 这是什么，不是什么

`play-profile` 是一次**部署选择**，不是一组可自由组合的 feature flag。
两个取值：

| 取值 | 含义 |
|---|---|
| `legacy`（默认） | 沿用原有 belief mode、治理门禁、五个反思字段与完整 112 工具面。旧用户配置不受影响。 |
| `lean` | 治理关闭（belief `off`）、移走信念/治理控制面工具、精简角色提示词、反思字段可选。 |

精简模式**不**新增知识图谱、向量库、多智能体部门、任务 DAG 或第二套游戏状态库；
也**不**重命名或删除任何游戏领域工具。

## 配置

```bash
./scripts/civ6_agent --play-profile lean --turns 1 --dry-run   # 预览实际生效配置
./scripts/civ6_agent --play-profile lean --turns 1              # 操作游戏，需现场授权
./scripts/civ6_agent --play-profile legacy --turns 1           # 回退到原路径
CIV_MCP_PLAY_PROFILE=lean ./scripts/deepseek_harness check     # 只做安装/配置检查
```

传递链路：`scripts/civ6_agent` → 环境变量 `CIV_MCP_PLAY_PROFILE` →
`integrations/deepseek-harness/civ6.cordis.yml` 的 `env:` → MCP 子进程。

**冲突即报错。** `--play-profile lean` 与显式 `CIV_MCP_BELIEF_MODE=observe|enforce`
不能同时成立：`scripts/civ6_agent` 在启动前以退出码 2 报错，MCP 子进程内的
`PlayProfile.effective_belief_mode()` 也会抛出 `PlayProfileConflictError`。
不静默覆盖任何一侧。`CIV_MCP_BELIEF_MODE=off`（或留空）与 lean 并存是允许的。

空字符串按“未配置”处理（shell 的 `VAR=` 与 launcher 的空回退都会产生空值），
因此 `CIV_MCP_BELIEF_MODE` 的存在本身不会导致启动失败。

## 工具面：可见性与执行权限是两件事

DSH 的 MCP 客户端**没有任何 allowlist/denylist 字段**：它把服务端广告的每个工具都注册给
模型（`@deepseek-ai/dsh-mcp-client` 的 `syncTools()` 无条件遍历 `response.tools`）。
因此“不要使用治理工具”无法靠提示词或 overlay 实现，必须在 MCP 服务端完成。

精简模式移走的是 `belief_tools` 与 `world_model` 两个模块导出的全部 29 个控制面工具：

```
assess_route_combat_risk      cancel_routed_action
delete_belief_entity          get_belief_metrics
get_belief_state              get_belief_trace
get_calibration_report        get_governance_brief
get_turn_brief                rebalance_hypotheses_bayesian
rebalance_hypothesis_pool     recompute_failure_attribution
record_action_verification    record_observation
resolve_governance_council    resolve_prediction
review_belief_engine          review_governance_proposal
route_belief_decision         run_trend_forecast
set_plan_status               submit_governance_proposal
update_belief_entity          upsert_belief
upsert_dynamic_plan           upsert_failure_attribution
upsert_hypothesis             upsert_prediction
upsert_strategic_goal
```

`112 - 29 = 83`。游戏领域工具（宗教、外交、文化、总督、商路、世界议会、间谍等）
一个都不少，`run_lua` 的禁用仍由 `CIV_MCP_DISABLE_LUA` 单独决定。

两条保证分别成立、分别测试：

1. **可见性** — `apply_play_profile()` 在 `server/__init__.py` 的工具注册边界之后
   从 FastMCP 注册表移除这些工具，`tools/list` 不再返回它们。
2. **执行权限** — `pipeline._hidden_tool_refusal()` 在 `_logged` 与 `_belief_tool`
   的最前面独立拦截手写工具名，因此即使有人直接按名字调用（绕过 MCP 路由）
   也拿不到控制面路径，且不会触碰游戏或账本。

`LEAN_HIDDEN_CONTROL_TOOLS` 是显式名单而不是运行时从注册表推导，
因为执行拦截需要在工具已被移除之后仍能认出这些名字。
`tests/test_lean_profile.py` 断言该名单与实际由这两个模块导出的工具集合完全相等，
所以往控制面新增工具会让测试失败，而不会悄悄进入精简工具面。

## belief `off` 下的行为

精简模式固定 `belief_mode=off`。在该模式下：

- `get_game_overview` 不采集治理快照（`captures_governance_snapshot` 为 False）；
- 不绑定/重放信念账本（`records_events` 为 False，`_bind_belief_engine` 不会被调用）；
- 动作预检不做议会审批或路由（`enforces_actions` 为 False，`route` 为 `routine`）；
- 工具返回值不追加信念上下文（`appends_context` 为 False）。

`RUNTIME POLICY` 因此显示 `governance=disabled`、`action_routing=bypassed`、
`belief_events=disabled`、`belief_context=disabled`，与 `--dry-run` 预览来自同一份
解析结果（`play_profile_report()` / `play_profile_summary()`）。

关闭治理后进入模型的每回合输入由独立的局面简报承担，字段白名单、缺失语义与
首次变更门槛见 [回合局面简报](turn-context.md)。

## end_turn 的反思字段

五个反思字段（`tactical`/`strategic`/`tooling`/`planning`/`hypothesis`）的**签名保持不变**。
`legacy` 仍要求五个都非空；`lean` 不再强制，且**不代填**任何“无问题”“已完成”之类的
占位文本——空就是空，日记不会记录模型并未做出的观察。

## 精简角色提示词

`integrations/deepseek-harness/civ6-lean.cordis.yml` 在 `civ6-agent.cordis.yml`
**之后**应用（DSH 的 `config` 补丁是整体替换，顺序决定生效者）。相对旧角色，它移除了：

- 治理提案 / `action_intents` 的 JSON 协议与逐字段校验规则；
- 强制上报 `success` 概率与置信度；
- “阅读源码/文档反推参数格式”这类开发任务说明；
- “参数或验证错误一次就永久停止本次会话”。

取而代之的是按结果分档的处理规则：

| 情况 | 允许的动作 |
|---|---|
| 执行前被明确拒绝，且未发生改动 | 按真实 schema 有界修正后重试同一动作 |
| 动作可能已发出、结果未知 | 只读核验（如 `get_game_overview` 核对回合号），绝不重发 |
| 权限或边界被拒绝 | 停止并报告，不换路径绕过 |
| 工具名不存在 | 它不在精简工具面内；不猜参数 |

**AGENTS.md 不会被 DSH 精简 overlay 自动读取。** `agent-instructions` 模块在
`civ6-agent.cordis.yml` 中被禁用，AGENTS.md 是以 durable user message 形式注入的，
该注入因此不发生。精简角色所需的一切都写在 overlay 的 `persona` 里。
（`persona` 也只是 order 0 的一个 section，工具插件还会追加各自的 100–199 band 提示段落。）

## 回退

不需要改代码或 `git reset`：`--play-profile legacy`（或取消 `CIV_MCP_PLAY_PROFILE`）
即回到原路径。旧模式仍然可选，旧数据不迁移。

## 验证

```bash
uv run pytest tests/test_lean_profile.py tests/test_tool_surface.py \
  tests/test_civ6_agent_entrypoint.py tests/test_belief_mode_server.py -q
uv run pytest tests/ -q
uv run ruff check src tests
CIV_MCP_PLAY_PROFILE=lean ./scripts/deepseek_harness check
./scripts/civ6_agent --play-profile lean --turns 1 --dry-run
```

这些全部是**离线**检查：不连接 FireTuner、不启动游戏、不调用模型。

## 仍需现场只读确认

离线测试只能证明配置、工具面与执行拦截正确，不能证明以下事项：

- DSH 实际发给模型的 `tools/list` 与系统提示词内容（需在真实会话中读取并核对精简
  工具面不含控制面工具）；
- 真实游戏中 `get_game_overview` 的 `RUNTIME POLICY` 与 `--dry-run` 预览一致；
- 关闭治理后是否存在只有治理路径才能解开的游戏内阻塞；
- 精简路径在真实对局中的决策质量与稳定性——这需要 C5 的对照实验，本页不预判结果。
