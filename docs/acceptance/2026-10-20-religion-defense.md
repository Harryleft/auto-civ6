# 宗教防御工具链离线验收

2026-10-20；对应[规格与四项工作单](../specs/2026-10-20-religion-defense/spec.md)。本次补齐只读观测、原生清除异教和神学攻击入口，使用公开 MCP／GameState → 现有 ActionLifecycle → 实际生成的 Lua 5.1 → 原生 API adapter 验证。没有连接 FireTuner，没有启动、重启、加载或推进游戏；原局 T141 终局和存档保持原状。

## 验证范围

| 能力 | 已执行的离线证据 | 结果边界 |
| --- | --- | --- |
| 宗教观测 | 同回合 InGame／GameCore／最终 InGame 范围；自有城市压力存量与流入分列；和平可见宗教单位；隐藏单位过滤；当前谴责资格；原生统计与本方计数分离；冲突及未知字段 | 查询不提交动作、不关闭 UI、不释放 pending；未知信息不变成零值、无权限或安全结论。 |
| 清除异教 | 绑定原始审判官、唯一原生自有城市目标、全部异教压力；原生无坐标提交；单 charge 和最后 charge；精确 started／removed 事件及双查找；实际压力下降 | 不要求固定 75% 变化，不推定本教压力或多数宗教变化。目标证据不足时保守零提交 `NATIVE_TARGET_UNCONFIRMED`。 |
| 神学攻击 | 和平可见宗教目标、宗教预演精确双方、同回合原生路径、无宣战变化；精确操作与 CombatVis 参与者见证；实际伤害、双方可能死亡和双查找 | 普通军事实体不能替代宗教目标；移动队列、目标不可见或单个事件不能单独证明战斗。 |
| 结果未知 | 延迟、丢回应、原生抛错、取消、身份变化后恢复、未知字段旁的有效不匹配、保存／推进／其他动作门禁 | 原所有者持续保留；同意图重入只确认原请求，不重复提交。 |

跨特性测试使用同一个 Lua engine 和共享原生实体，先查询压力 `400 / 200`、审判官 charge `3`，经公开 `unit_action` 清除后再查询压力 `120 / 60`、charge `2`。外方宗教仍为原生多数，流入压力保持 `6.25`，因此压力下降不会冒充城市转化。延迟及丢回应流程中，动作前后查询均保持原 pending；原入口确认后，原生提交数仍为 1。

和平神学战流程先观察精确宗教目标，再经公开动作确认伤害和原生移除，最后重新扫描可见单位。同格军事单位仍留在原生地图，并未被当成宗教目标。新建 stdio 服务的 `initialize`／`tools/list` 烟测确认只读查询及两个动作已公开；子进程禁止所有 socket connect，元数据发现仍通过。

## 实际检查

集成流程测试：`tests/test_religion_defense_workflow.py`，5 项通过。回归 RED 使用实施前 `4224de2` 的离线源码快照运行新增流程测试，公开 `get_religion_defense` 缺失而失败；当前实现同一测试转绿。R02 的动作入口、城市消失、同次探测隐藏身份变化、最终 charge getter 变化等 RED→GREEN，以及 R01／R03 的逐项回归，由对应工作单记录。

受测版本为 `1b557e3`：集成 `f44dcd8`，包含 R04 流程提交 `4b49dc1`。下列 15 个受影响套件共 **1288 passed in 36.05s**，包含 5 项新增跨特性流程。`compileall`、`git diff --check` 通过；3 份本次操作／验收文档的 26 个本地链接及锚点通过。独立 Spec／Standards 审查仍待执行，由父任务后续记录。

```bash
uv run --locked pytest -q \
  tests/test_religion_defense_workflow.py tests/test_religion_defense.py \
  tests/test_remove_heresy.py tests/test_religious_attack.py \
  tests/test_game_state.py tests/test_server.py tests/test_action_confirmation.py \
  tests/test_action_scope.py tests/test_scope_observation_recovery.py \
  tests/test_pending_dispatch.py tests/test_action_lua_execution.py \
  tests/test_spread_pressure.py tests/test_condemn_heretic.py \
  tests/test_launch_inquisition.py tests/test_unit_effect_confirmation.py
uv run --locked python -m compileall -q src tests
git diff --check
```

## 尚未执行的原生验收

**原生动作未执行，离线 adapter 通过不等于实机通过。** 本机第一方 UI／XML 只用于核对[调用契约](../specs/2026-10-20-religion-defense/native-contracts.md)。真实 `REMOVE_HERESY` 目标返回形状、最后 charge 的销毁与事件顺序，以及神学战 `CombatVisEnd` 的实际投递仍需后续获授权实机验证。本次不为验收重开、加载或回放。

后续获授权存档测试按下列有界清单执行，保留存档身份、世界／回合、完整实体 ID、工具请求、原生事件和动作回读。仅使用当次授权基准或该轮已确认检查点，不覆盖本局终局证据。

| 场景 | 执行与通过依据 |
| --- | --- |
| 当前观测 | 对照本方城市界面核对多数宗教、信徒与累计／流入压力；核对地图上和平可见传教士，确认隐藏单位未输出；核对 InGame／GameCore 差异被明确展示。 |
| 原生清除目标 | 先只读查看实际 `GetOperationTargets` 的 `PLOTS`／`MODIFIERS`，核对唯一目标确为实际作用城市。若没有可绑定证明，记录 `NATIVE_TARGET_UNCONFIRMED` 与零提交；不得用离线 fixture 假定真实形状或补猜坐标。 |
| 普通 charge 清除 | 对原生合法审判官只调用一次；核对 exact OperationStarted、charge 减 1、原城市各异教累计压力下降，再刷新观测。多数未改变也可成功，但必须按实测记录。 |
| 最后 charge 清除 | 记录真实事件次序。若审判官消耗，必须有原始精确 started／removed 及两个原生查找均不存在；不得解引用已消耗 userdata。若实际规则不同，保留未知并修正契约，不伪造销毁。 |
| 和平神学战 | 和平状态下用可主动攻击的宗教单位与唯一可见目标，核对原生宗教预演精确双方、宣战变化为空、路径当回合可达。单次提交后核对 exact OperationStarted、CombatVisEnd 双方及实际伤害／死亡；同格军事单位不能借用见证。 |
| 未完成与恢复 | 在单次提交后的延迟或可控丢回应下查询宗教防御，确认 pending 不被查询释放，保存和推进仍受保护；同意图仅确认原请求，原生提交计数不增。不得通过真实网络断开或服务重启制造新的未授权恢复范围。 |
| 议会资格变化 | 核对当前 active resolutions 和会议剩余原始值，分别记录当前谴责资格与可读权限来源。来源／宗教／效果无法唯一确定时应保持 unknown，不引用历史回顾补猜。 |

未决结果按照[保存与恢复](../agent-recovery.md#结果未知)处理。上述清单不授权新游戏、重放、直接改数值、合成原生事件或有利结果回档。
