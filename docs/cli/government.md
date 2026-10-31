# 政策、城邦与总督

[返回 CLI 索引](../cli.md)

## 政体

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `government show` | `get_policies` | 只读 |
| `government list` | `get_public_report`，section=governments | 只读 |
| `government change TYPE` | `change_government` | 变更 |

`government list` 的 `can_change`／`can_change_at_all` 是原生总开关；当前能否选择须读 `can_change_now`、逐目标 `can_select_now`、`refusal_reasons` 与 `change_conditions`，其中包括免费窗口和无政府状态等前提。未知为 null。保留当前政体的 ACK 是确认继续采用，与选择新政体分开判定；原生资格不代替 Runtime 未决门禁或实际提交确认。付费解锁政策与换政资格分开，足够金币不创建政府更换窗口。

## 政策、城邦和总督命令

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `policy show [--policy POLICY_ID]` | `get_policies` | 只读 |
| `policy set SLOT=POLICY_ID [SLOT=POLICY_ID ...]` | `set_policies` | 变更 |
| `city-state list` | `get_city_states` | 只读 |
| `city-state envoy PLAYER_ID` | `send_envoy` | 变更 |
| `governor list [--include-ineligible]` | `get_governors` | 只读 |
| `governor appoint TYPE` | `appoint_governor` | 变更 |
| `governor assign TYPE CITY_ID [--owner PLAYER_ID]` | `assign_governor` | 变更 |
| `governor promote TYPE PROMOTION_TYPE` | `promote_governor` | 变更 |

`policy show` 同时检查原生过时状态、当前槽位资格、政策更换成本／余额，以及零费用时是否已经改过政策。默认省略已过时或没有新增槽位的卡片，报告覆盖数量；资格未知的候选继续保留。`--policy` 可核对一张未列出的卡片，区分 `POLICY_LOCKED`、`POLICY_OBSOLETE`、`POLICIES_ALREADY_CHANGED` 和余额不足。一般检查通过不代表某份完整槽位方案一定合法，实际执行仍重新检查。

`governor list` 使用与执行入口相同的 `CanEarnPromotion`／`CanEverAppointGovernor` 及当前任命资格。默认省略原生明确拒绝的选项并报告数量，保留未知资格；`--include-ineligible` 展开未取得的晋升树与未任命总督，逐项标注原生资格。未取得的晋升不再自动表示现在可以晋升；资格为 false 的布尔接口未提供具体原因时，不猜测缺少哪项前置。

政策槽位可用空格或逗号分隔，未指定槽位保持原值。首次填槽或切换政体后，一次提供所有空槽；任何变更后的最终方案仍有空槽时，返回 `POLICY_PLAN_INCOMPLETE` 并列出编号，在付款或原生提交前拒绝。`NONE` 保留解析支持，但不能让变更后的方案留下空槽；原已为空且完全不变的方案仅返回 no-op。格式错误和重复槽位在发送前拒绝。城邦 player ID、总督类型及晋升均先查询；owner 省略时取本地玩家，显式 owner 为 0～63。政策、使者、任命和晋升可能消耗金币、使者或总督头衔，实际资格由引擎决定。

