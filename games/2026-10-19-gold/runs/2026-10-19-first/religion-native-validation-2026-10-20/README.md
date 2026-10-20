# 宗教防御工具链实机验收

用户于2026-10-20明确授权重载MCP服务，从本局已确认检查点完成原生验收；不新开下一局，保留原终局证据。原始对局仍为T141瑞典宗教胜利，本目录中的恢复与新动作仅为工具验证，不改写原局结果。

- 当前代码起点：3b47465，集成分支codex/gold-terminal。
- 原轮次：e8b4d6a62dca49bbb256e8264beb18f2，Sumeria/Gilgamesh，seed -1896272000。
- [原证据指纹与检查点](preserved-evidence.json)，原始日志不追加；新工具请求在独立evidence/tool-calls.jsonl。
- fresh stdio MCP服务#1先查询当前状态：游戏未运行，明确连接拒绝；#2启动已有Civilization VI进程及FireTuner，服务工具目录83项。
- #3仅请求加载原确认 gold_20261019_t141_progress，SHA916e5d1ba215a68eff308d43b612e3130c68c768881da9daddf7d82dd4703592。加载确认后先核对概览、单位、城市和宗教，再测试原生资格、目标和效果。

验收覆盖：当前宗教查询、清除异教普通/最后charge、和平神学战斗、效果回读/后续合法动作及保存、相关未知/资格拒绝。真实引擎中不伪造事件、不直接改资源/伤害/科技/地图。遇到阻塞进入to-spec→implement-spec，沿现有公共MCP/GameState/Lua及真实原生边界验证，未知不重发。

## 已观察问题

| 问题 | 回合／证据 | 处理 |
| --- | --- | --- |
| Q01 UTF-8城市名 | T141#7、T139#14，#20原生中文与字节对照 | 宗教查询的%c过滤破坏byte129，进入to-spec→implement-spec |
| M01 跨层移动误拒绝 | T139#18，#20原生move资格true/完整当前回合path | 共享civilian formation不等于同层，进入to-spec→implement-spec |

#21已保存专用gold_val_20261020_t139_query_move_ready（929031bytes）。Apostle3145737在35,24，本方Builder3211267仍35,22，原生世界保持T139，无未决动作；修复后保留此现场验收。T141#9清除资格拒绝后#10实际次数3/MP0/无receipt，属于合法零提交，并非清除动作成功。

## 第一轮修复与查询复验

- 固定审查范围4e7ebb4…aa3f5b4：Q01 2910c63（集成8111057），M01 3bfbc4c（集成aa3f5b4）。Q01真实macOS Lua UTF-8 locale下RED→GREEN；248相关测试通过。M01公共MCP／GameState／实际Lua路径247项通过。Spec独立审查无实质问题；Standards发现目标宗教层占用未查可见性，交同M01 worker补修，故尚不关闭M01。
- #22／#23：重新启动83工具的新stdio服务，先确认仍T139、苏美尔／吉尔伽美什，再调用宗教查询。城市名正确为“乌鲁克”；InGame与GameCore范围一致、faith54.3984375／inquisitionfalse；和平外方使徒5570570仍在38,21、本方使徒3145737在35,24且2MP。Q01实机通过。
- #23后只有只读请求，干净关闭服务，无未决动作；未换档或推进。7项原证据／原检查点指纹再次全部一致。

- M01可见性补修4b87cc7，集成84dbedd，两轴复审均无实质问题；259项相关测试通过。#24发现游戏进程已退出，无未决；#25启动游戏，#26只加载本轮专用T139检查点，#27–29重新核对身份/实体。
- #30使徒3145737从35,24进入35,22成功；#31原生ANY层回读确认与建造者3211267、野战加农炮3014658三者同格，warfalse；#32保存gold_val_20261020_t139_layer_pass（929002bytes）。M01实机通过。
- #34确认T139→T140。#35当前宗教查询确认外方使徒5570570已到36,23、和平，#37保存gold_val_20261020_t140_peace_combat_ready（929153bytes）。
- C01：T140#38宗教攻击实际发生，但返回OUTCOME_UNKNOWN（COMBAT_WITNESS_INCOMPLETE）；#39run_lua被原未决门禁拦截，没有执行；#40/41只读回读显示本方使徒HP100→86/位置35,22→36,23、瑞典目标消失与周边异教压力减少。干净关闭原owner后只读diagnostic确认entered/returned=true、started=nil、combat=true、target.removed=true、目标两条原生查找均缺失、invalid=nil、warfalse。诊断输出独立保存在evidence/t140-combat-receipt-diagnostic.json；没有重发动作或释放旧结果。进入C01 to-spec→implement-spec；修复后须显式恢复#37原战前检查点再进行任何游戏动作，旧#38保留未知事实。

- C01修复a2be8bf／集成6425b31，226相关测试通过，两轴审查均无实质问题。#43显式恢复#37战前检查点并#44–46核对；#47一次公共神学攻击返回CONFIRMED（100→86／54→0）；#49独立原生查找目标双缺席、warfalse、exactCombatVisEnd/removaltrue，#50保存gold_val_20261020_t140_peace_combat_pass（928976bytes）。C01实机通过。#48诊断因Lua零返回值传tostring失败，#49仅修正只读查询，不涉及再次攻击。
- #59–64明确处理巴西要求停止传教：POSITIVE一次进入后先返回unknown；原生变为“谢谢”／EXIT，#62仅确认原请求，#63退出，#64无外交待办。随后瑞典对话、议会、历史时刻弹窗均按原生当前选项处理，#75T140→141、#92T141→142。
- #78原使徒启动审判且消费确认；#80花100信仰买Inquisitor3276804，#89保存独立T141买后检查点（937143bytes），#92进入T142。原局T141失败结果不改写。
- P01：T142#95审判官到35,22，#96严格资格true、2MP/3charges；原生GetOperationTargets仅效果说明（预计10信徒）而无PLOTS/MODIFIERS；原城市131073异教stock406.8515625。#97零提交NATIVE_TARGET_UNCONFIRMED，进入P01 to-spec→implement-spec。#98保存gold_val_20261020_t142_purge_ready（939896bytes），干净关闭无未决owner；保持此现场待修复复验。
