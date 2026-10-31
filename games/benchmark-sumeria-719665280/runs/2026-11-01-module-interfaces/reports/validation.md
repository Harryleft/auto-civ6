# 模块接口实机验证

**PASS：本轮覆盖的查询、动作确认、T1→T3 推进及正常检查点回载。** 验证于 2026-11-01 00:47–00:52（Asia/Shanghai）执行；原始日志使用 UTC。工程修改与离线结果见[工程验收](../../../../../docs/acceptance/2026-11-01-module-interfaces.md)。

## 起点与控制通道

用户指定原始 `benchmark_turn_1`，SHA-256 `688b12eddbf1ec6bdb44da4f558d39191e96a6893a538f57ccc531beb5339ffd`。启动前 `civ6 tuner status` 显示游戏离线，无控制客户端，因此没有待保护的活动对局；已有存档不覆盖。

仅使用一个 `uv run --locked civ6 debug --compact` 普通会话，未开启 developer、未直接调用 MCP 或 Lua。变更均先 dry-run，再逐项执行和读取结果。加载回读为苏美尔、种子 `719665280`、玩家 0、T1；游戏轮次 `00518115e4634afbbcb06abe195ea445` 与 CLI 轮次 `d86b80cbcdd54263aed357ea28518506` 分开记录。

受测基线提交 `f8da63024702ebdba0dce6a381381a1c61829070`，叠加本轮未提交代码；精确源码指纹见 `evidence/cli/context.json`。

## 实际执行及回读

下列序号指 `evidence/cli/debug.jsonl` 的结果事件；完整实参及回包同时保存为 `debug-response-*.json`。所有 43 次实际工具调用均无工具错误。

| 功能 | 输入与原生事实 | 结果序号／结论 |
| --- | --- | --- |
| 状态与身份 | `state overview`、`unit list`、`city list`、`verify state`；T1 为开拓者 65536、勇士 131073，无城市 | 14–26；所有快照检查通过 |
| 知识 | `knowledge show CIVILIZATION_SUMERIA`、`knowledge show LEADER_GILGAMESH` | 36、39；found，静态知识适用性边界保留 |
| 建城 | `unit found-city 65536`；当前位置 (19,17) 为沿河丘陵，附近有已观察粮食和生产地块 | 47；`FOUNDED`、action confirmed；回读乌鲁克 65536 |
| 生产／科研 | `city set-production 65536 UNIT UNIT_SCOUT`，`research set TECH_MINING`；候选来自当前生产／研究查询。选择侦察兵探索，采矿对应周边石材与丘陵 | 74、77；两项 confirmed |
| 移动 | T1 `unit move 131073 17 17`；T2 `unit move 131073 17 16`，目标均来自本轮地图查询 | 80、109；原单位到达、移动力消耗及新揭示地块；confirmed |
| 批量跳过 | T2 `unit skip-remaining`；当前只有该勇士、剩余 1 移动力，暂留原地结束本轮行动 | 114；原生 ready=false，父结果及该单位子结果均 confirmed |
| 城市查询 | `city growth 65536`、`city workforce 65536` 的 brief／evidence；`city yields 65536` brief | 59、62、65、83、86；ready，零值／未知区别保留 |
| 决策／公开报告 | `state decision`，`game decision --city 65536 --include research`，`empire gossip`，`empire resources` | 42、99、120、117；组合范围检查通过；未相遇消息为空有完整读数 |
| 回合推进 | 每轮 `turn status` 确认 LOCAL_READY，再 `turn end` | 96、128；明确 from_turn/to_turn 为 1→2、2→3 |
| 保存 | `save create design_20261101_t003` | 144；原生完成事件、新鲜非空文件及世界身份确认 |
| 同轮回载 | `save reload design_20261101_t003`，随后概览、单位、城市、pending、`verify state` | 149–166；T3、分数 12，概览／单位／城市文本与保存前逐项相同，所有检查通过 |

动作入口各执行一次，没有重发原意图。生产、科研、建城、移动及跳过的 `outcome` 来自原生确认所有者；两次推进各返回 confirmed。单次提交与丢回应后的不重放还由隔离回归覆盖；本轮没有主动制造原生断线。

一次 `state decision --city 65536 --include research` 在本地被拒绝（事件 88，`INVALID_DEBUG_COMMAND`），没有发送请求；按已有命令契约改用 `game decision` 后通过。这个输入错误计入时间统计，未作为工具失败或被删除。

## 检查点与证据

- `design_20261101_t003`：595400 字节，SHA-256 `9d240df5ae46485e2057bcc184d24c8d80cc9e4def607ddbd18a11cf0b80658a`；登记文明 sumeria、种子 719665280、T3、本轮 ID，官方分数 12。
- `evidence/checkpoints/` 保存检查点副本、登记与当前轮次，副本指纹已核对。原始基准结束时重新计算指纹，保持不变。
- `evidence/cli/`：完整命令、响应、stdio、源码指纹、状态验证及自动回合统计。`turn-timing.json` 为 `completed_turns=2`，`issues=[]`，`unknown_count=0`；加载边界与 T3 未完成回合单独保留。时间包含模型与工具间隔，不能视为纯游戏耗时或性能基准。
- `evidence/action-log/`：通过 `civ6 logs show` 保存同会话 43 条标准日志；来源 SHA-256 `d0ffcf159579dbb363ac377d88a929c2c18503a612f655588229b876d36ac27e`，无坏行，原文本与 outcome 均保留。
- `manifest.json` 保存结果与动作事件引用。原始证据及存档留本地，不随报告发布。

## 结束状态与限制

最终所有 pending=false，`transport_unknown=null`；正常 `quit`，退出码 0、`context_lost=false`。随后端口检查为 available，CLI/MCP 控制进程列表为空。游戏保留在本轮 T3 回载现场，未额外推进或退出游戏。

Windows、战斗、编队、外交与议会多阶段原生交互、真实故障／取消、完整对局胜利均 **NOT_RUN**。这是一轮接口回归，不对所有 34 类动作或全部游戏机制宣称实测通过。
