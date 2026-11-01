# 整局 A 首次尝试：T10 外交结果未知中断

本次尝试不能作为完整速度 A 样本。原始基准、已保存起点、全部命令和中断证据保留，不删除恢复耗时后拼接计时，也不以替代尝试覆盖本次记录。整局两倍目标尚未完成。

## 来源与过程

受信原件为 `benchmark_turn_1`，SHA-256 `688b12eddbf1ec6bdb44da4f558d39191e96a6893a538f57ccc531beb5339ffd`。CLI run `02844f75034d41d5b1fc2fda0d924dc3`；Runtime `edc5f68af50f4d81a6e4a119e81a96a6`；独立游戏测试轮次 `da91536af0d2473f8fff79cac7dc523d`。起点 `speed_20261101_whole_a_t001` 已确认，SHA-256 `b525ec0e1d2520b31e360dfffe482972bd2072af588bf165847b27943eb033a2`。

首次完整决策为命令 #29，2026-11-01 10:28:13.090168 UTC；首次未知结果 #340，10:34:45.889259 UTC。只推进至 T10，未获胜，不能据此计算完整 A/B 比率。执行遵循每回合 fresh brief、每命令独立模型调用、每变更独立 dry-run 后明确 execute。

首都在原地建立，侦察兵、建造者已完成，畜牧业完成，奶牛牧场已建，正在研究采矿并生产苏美尔战车。T10 为 1 城、2 人口、勇士／侦察兵／建造者各一。建造者合法消耗 1 次次数；未观察到军事单位、平民、城市或灾害人口损失。勇士面对营地长矛兵的原生预估为受伤 43、造成 21，因而没有攻击，转为林地防守。侦察兵发现瑞典，读取关系后尝试派遣代表团以缓解军事劣势。

T7→T8 的科技完成弹窗按原请求显式关闭并确认，没有重复结束回合。曾误向无参数的 `unit tasks` 提供单位 ID，CLI 参数拒绝后改正；对已有移动活动的侦察兵执行 skip 返回 NOT_READY、未提交，既有活动保留。这些拒绝同样保留在原日志中。

## 中断事实

`diplomacy action 2 DIPLOMATIC_DELEGATION --execute` 在原生 `RequestSession` 返回 false 后报告 `NATIVE_REFUSED_AFTER_ENTRY`、`OUTCOME_UNKNOWN`。Runtime 保留原 action，transport_unknown 为 null。只读关系未见我方代表团，外交待办为空；原命令只确认一次仍返回 `REQUEST_NOT_RETURNED`。未将未扣费或会话缺席解释为未提交，未重发新外交意图、未以身份读取释放门禁。

原生外交日志尾部记录了瑞典在 T10 发起的 `FIRST_MEET_NEAR_RECIPIENT` 会话 2。独立审查发现现有实现会在新关系请求前无条件关闭该目标开放会话；真实 GameState/Lua 夹具复现了“关闭 incoming first-meet 后立即申请新会话返回 false，原动作保持未知”的模式。这是需要修复的自动取消行为；日志与调用行号支持它与现场一致，但现场没有完整的每阶段原生回执，不能把夹具结果写成现场所有内部状态的证明。

证据位于本轮 `evidence/live/stdout.jsonl`、`evidence/interrupted-result.json`、`evidence/diplomacy-manager-tail-at-unknown.csv`。原门禁无法确认时，沿本轮已确认 T1 检查点发起显式受保护恢复；恢复结果后续追加，不透明替换 owner。

## 恢复与修复

显式恢复 #364→#367 于 10:38:12→10:39:18 UTC 完成，目标为本轮 T1 检查点。后续 overview／units／cities 均为原始 T1，pending #378 全 false、transport_unknown=null；quit #380 正常退出，unresolved=false。恢复丢弃了检查点后的 9 个净推进回合，本样本保留为中断，不能称为连续完整对局。

最小修复移除了新关系动作前的自动 CloseSession。发现目标已有开放会话时，返回 `RELATIONSHIP_REFUSED|DIPLOMACY_SESSION_OPEN`，保留会话并要求模型显式查待办、回应或关闭；进入后的 false／异常／取消仍保持未知，原工具重复只确认。

18 个新回归用例经真实 GameState→生成 Lua，覆盖 incoming／outgoing、首次见面／使馆／交易及代表团／友谊／谴责，均在修复前失败、修复后通过。外交相关定向回归共 274 passed，编译及 diff 空白检查通过。独立只读代码审查未发现材料性问题。该离线结果尚不能替代修复后实机首次接触处理与派遣代表团验收；后续完整尝试将使用新源码指纹，旧冻结版本记录不改。
