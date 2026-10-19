# B04-01：确认即时结束的原生探索尝试

Status: resolved
Blocked by: none
Spec: ../spec.md

职责：最小修复automate任务确认，把精确原生接纳与当前ready分开；保持其他任务非就绪／活动／no-op要求、scope/fullID/no-replay门禁。现有公共GameState/MCP seam内TDD。父代理负责实机与报告。


## Implementation

T29 实测 `evidence/b04-native-automate.txt`：fullID `262146`、种子 `-1896272000`、精确任务 Hash `-1676983098`／Index `1`；原生 entered／returned／started 均为 true，同时 ready=true、AWAKE、moves=3、未移动。任务确认只对新的 automate 接受可读 ready=true 或 false；缺失可读布尔值仍未知。精确事件、世界／回合／完整单位与请求 token、entered／returned 门槛保持。alert／heal／sleep 与 ALREADY 的非就绪要求保持。

工具结果明确原生探索请求已接纳，移动及持续探索未测量；不自动重试、不推断可用路径。

TDD：实际 T29 ready=true／AWAKE 和原生 Hash／Index 用例先红后绿。公共入口覆盖丢回应后只确认一次、ready 下错事件／错 fullID 不确认、接纳后读取 readiness 失败不确认／不重放、其他任务仍就绪不确认，以及 ALREADY 丢失非就绪状态不确认。直接相关 `test_unit_tasks.py`、`test_unit_readiness.py`、`test_action_scope.py`、`test_server.py` 共 323 passed；compileall、diff --check 通过。独立审查、同轮 T29 检查点实机与 T29→T30 由父代理继续。

## Verification

实施546a622，集成6d3bf7d；323项相关离线通过，两项独立审查无实质发现。#362显式恢复同轮T25并按原决策重放，#393保存同局T29操作前检查点。刷新MCP后#396单次automate确认接纳，#397/398回读仍ready=true／AWAKE；#400后续明确移动正常。完成瑞典首次会面后#409确认T29→T30，#410保存gold_20261019_t030_b04_verified，种子-1896272000、苏美尔、回合30。
