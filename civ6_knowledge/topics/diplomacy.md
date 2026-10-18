# 外交、联盟与贸易

比较外交行动、商路或交易时读取；按[统一查询](../README.md#查询)展开。

## 关系与行动

`MECHANIC_DIPLOMACY_ACTIONS` 分开发起方与目标方前置、关系状态、有效协议和行动专属条件。友好不等于宣布友谊；基础行的 `AiAllowed` 也不是当前玩家的资格判定。

`MECHANIC_DIPLOMACY_PROMISES` 区分承诺费用、拒绝后果与后续侵入怨恨。旧 `WarmongerPercent` 不直接等于风云变幻的怨恨。

例：`DIPLOACTION_DECLARE_FORMAL_WAR`、`DIPLOACTION_KEEP_PROMISE_DONT_CONVERT`；关联 `DiplomaticStateActions` 及行动扩展表。

## 联盟

`MECHANIC_DIPLOMACY_ALLIANCES`：宣布友谊、双方文官制度、类型排他性、等级累计与续期。`AllianceEffects.LevelRequirement` 是效果门槛，不表示本局已生效；商路与研究条件仍需满足。

例：`ALLIANCE_RESEARCH`。联盟提供互相开放边界和防御协定，紧急事件及防御参战链仍有例外。

## 商路与双边交易

- `MECHANIC_DIPLOMACY_ROUTES`：商人、容量、目的地、贸易站、往返与国内／国际收益列。
- `MECHANIC_DIPLOMACY_DEALS`：双方交易条目、一次性转移、持续条款、报价与成交。

商路不等于资源交易。容量不能仅按旧百科的区域概括判断；战略库存交易也不能套用“所有资源到期归还”的旧描述。具体冲突随规则卡保留。

## 城邦

`MECHANIC_DIPLOMACY_ENVOYS`：影响力、可用使者、派遣和宗主权。达到最低门槛仍须独占最多使者；普通类型奖励不要求宗主权，专属奖励另查当前城邦。

## 本局核对与关联

`get_diplomacy`、`get_pending_diplomacy` 查关系与请求；`get_trade_options`、`get_pending_trades` 查交易；`get_trade_routes`、`get_trade_destinations` 查商路；`get_city_states` 查使者与宗主。未显示的对方研究、资格或价格保持未知。

前置查[市政](civics.md)，资源条款查[资源](resources.md)，宣战后查[战斗](combat.md)，紧急事件查[世界议会](congress.md)。机制解释的来源包括 `CivilopediaTexts`；数值与资格字段保留原始数据。
