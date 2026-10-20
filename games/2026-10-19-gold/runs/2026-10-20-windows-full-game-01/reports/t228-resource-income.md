# 第228回合：战略资源完整净收益

黄金时代“机器人大战”原生奖励了一台末日机甲。第226～228回合接口持续显示铀0/33、每回合-3，但原生只读核验：开采0、进口0、GetBonusResourcePerTurn=3、单位消耗3、库存0，实际收支平衡。

官方游戏本地UI `DLC/Expansion2/UI/Replacements/TopPanel_Expansion2.lua` 使用开采+进口+奖励计算总收入，使用单位+发电计算总消耗。旧查询遗漏奖励、进口收入和发电消耗，影响帝国资源面板及回合资源摘要。不是Windows不支持黄金时代，也不是实际铀短缺。

修改`src/civ_mcp/lua/map.py`的两种资源查询及`models.py`字段口径注释，保持现有解析协议：per_turn为完整收入，demand为完整消耗，imported仅作为收入明细。没有重复在展示层相加。

验收规格：真实Lua查询分别覆盖奖励抵消单位消耗、混合收入与发电消耗、真实短缺、单独发电、全部为零；沿用GameState、回合报告测试。231项测试通过（6.15秒）。

Windows实机：仅重连已配置的MCP服务，无需重载存档，原第228回合帝国资源查询显示URANIUM 0/33 (+0/turn) [income 3, demand 3]，COAL +2/turn [income 7, import 3, demand 5]。机甲、文明、种子和回合均保留。

同时统一GameState回合摘要和耗尽提示：总收入已含进口，不能再次加imported。新增真实Lua→解析→回合报告及快照差异的闭环断言，覆盖进口不重复计算；231项再次通过（5.94秒）。
