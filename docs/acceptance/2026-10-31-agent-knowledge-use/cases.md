## C01 理性主义

一座本方城市人口14。现有报告给校园 district adjacency science=6、plot native adjacency science=3；当前政策是否解锁、插槽和实际条件未读。有人建议按“相邻6”直接认定该城获得理性主义全部额外收益。请判断目前能否下此结论，并给出最少的补查和依据。不要把旧版或其他规则集的门槛当作当前事实。

## C02 长路径队列

单位A当前原生next_turn=2、仍保留远期目标；单位B的原生next_turn=1，turn end因此返回QUEUED_MOVE_CURRENT且明确NOT_SUBMITTED。模型希望继续保留A的远期路线，但B是否继续需要重新决定。有人建议先对所有单位skip，以证明旧队列都已清空。应怎样分别处理，何时才可以推进？

## C03 交易收尾

一次发送模式trade propose 2，offer为空、request为open_borders且玩家支付14金币，已原生成交；余额107→93。随后diplomacy respond 2 EXIT返回SESSION_CLOSED和TRADE_CONFIRMATION_PENDING，并给出player=2、send及原参数提示。Runtime diplomacy仍pending，下一步想移动单位。请给出正确收尾及避免二次付费的验证点。

## C04 未知移动

unit move 131073 34 18已提交一次，返回OUTCOME_UNKNOWN；原Runtime仍持有移动意图，native只读状态发现NaturalWonderPopup，传输标记为null。有人建议重新发送移动，或新建CLI后用空pending证明没有未决请求。请给出诊断、处理和确认顺序。

## C05 政府窗口

government list的can_change=true、can_change_at_all=true，但can_change_now=false；active_turn=true、civic_completed_this_turn=false、free_change_window=false、in_anarchy=false，非当前政体均NO_FREE_GOVERNMENT_CHANGE_WINDOW。另有政策付费解锁报价170、金币130。是否应攒到170后再换政？保留当前政体与选择新政体有何区别？

## C06 使徒晋升

一名使徒经验15/15、MP3。旧资料列出9类晋升；当前原生UI候选和current候选均只有92、95、96，对应Proselytizer/IndulgenceVendor/HeathenConversion。模型想选Debater。该怎么决策和提交？若这两个原生集合读成unknown，能否用经验或静态树补齐？

## C07 城市宗教

玩家宗教多数值为-1，native_religion_city_stat=4，本方有4城。独立逐城扫描为65536:11、131073:8、196610:11、262147:-1；扫描完整，已知道教id11、新教id8。判断目前本方宗教分布，下一回合只需要发现哪座本方城市改宗时应优先读什么；-1、0、unknown分别怎样处理？

## C08 升级目标与报价

旧提示把勇士升级写为Swordsman/80g。当前原生CanStartCommand(UPGRADE)第二返回值含UNIT_TYPE=94，对应Man-at-Arms；本次价格165g。已提交一次后收到替换确认，旧单位131073消失，新1835017为Man-at-Arms，金币266.6484375→101.6484375；回执另有resource_quote13/resource_net_change-11。应该怎样展示、验证及界定结论？需要重发升级吗？

## C09 战斗预估

勇士当前HP51，在(33,19)，一个当前可见的蛮族散兵位于(34,18)。基础CS均为20，模型不愿先真正攻击来试伤害。当前接口若返回current_turn_preflight_refused并注明Runtime ZOC guard提前拒绝，是否证明引擎不允许？应如何获取当前伤害预估、处理unknown，并验证查询没有造成攻击？神学战斗能沿用吗？

## C10 地块收益

同回合相同本方地块(41,26)，map area为F4/P0，而city workforce为F5/P2；地图另一固定测试有4.75、2.5、-0.75曾显示为4、2、0。能否统一认定“城市政策使缓存不同”，或说修复小数截断已解决前述整数差异？应如何选择来源、保留精度以及解释省略的收益字段？

## C11 邻城传教

当前传教士位于本方城市相邻格，城内被友方单位占据。当前格及六邻格完整可见，只有一座预期目标城市位于(28,27)，原生传教资格需运行时核验。必须先进城才能使用工具吗？给出命名命令、资格及未决参数约束；若压力3.83203125→206.32421875、次数2→1但多数宗教未读，能否报告已改宗？

## C12 续测与脚本

当前是本轮已确认T100，原始四叶草基准benchmark_gold_tun_1和本轮已登记T57检查点均保留，全部pending为空。用户只授权本轮检查点离线方案审查，没有授权你现在操作游戏；未来若执行本轮T57复验后恢复T100应怎样选入口和存档？仓库有integration_turns.py及hotseat_endurance.py，是否可以直接用它们替代模型逐回合续局以快速完成100回合？不要现在执行任何游戏操作。
