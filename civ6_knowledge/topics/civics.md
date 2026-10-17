# 市政：前置、鼓舞与解锁

规划文化路线、政策或政体时读取。

## 规则

- `CivicPrereqs` 中同一市政的多条前置，在普通静态树中共同核对；随机树的实际前置需查询本局。
- `Boosts` 保存鼓舞的对象、数量和条件。原始 `Boost` 值不等于所有文明、速度和修正下的最终完成比例。
- `PrereqCivic` 提取直接解锁；对象仍可能受科技、文明／领袖特性、区域、资源和地块条件限制。
- 政策需核对槽位、现任政体、过时状态及能否调整；解锁政体不等于已更换政体。

## 本局核对

| 事实 | 查询工具 |
| --- | --- |
| 研究、前置、进度、鼓舞与展示候选 | `get_tech_civics` |
| 现任政体、槽位与可用政策 | `get_policies` |
| 解锁对象在城市中是否可建及费用 | `get_city_production` |

## 继续查阅

示例 ID：`CIVIC_CODE_OF_LAWS`，按[统一查询命令](../README.md#查询)展开。涉及落位读[区域建设](districts.md)，涉及科研前置读[科技](technologies.md)。

证据表：`Civics`、`CivicPrereqs`、`Boosts` 及解锁对象表；原生片段 `civic-live-prerequisites` 区分静态与活动随机前置。
