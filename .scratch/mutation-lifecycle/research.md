# 实施调研补充

基线 d3450a3；接续[全量审计](../../docs/specs/2026-10-19-mutation-audit.md)与[正式规格](spec.md)。以下证据来自现有实现、原版游戏 Lua 和隔离故障注入；不涉及真实对局操作。

| 问题 | 已核对的事实 | 实施含义 |
| --- | --- | --- |
| 地块购买 | 现有 builder 在 RequestCommand 后直接输出 TILE_PURCHASED；缺精确新归属与消费回读 | 原城市完整身份、地块 owner／购买城市及黄金差共同确认 |
| 领域证据扩展 | 使者特殊处理分散在接受、拒绝、失效、确认、提示五处 | 用请求携带的小型领域证据描述，仍由 ActionLifecycle 统一管理存活；购买和使者是两个实际用例 |
| 直接入口 | 多个 GameState 方法依赖 MCP 外层门禁，直接调用可绕过 | 同一普通动作未决事实必须保护两个公开入口；不能只测购买方法本身 |
| 保存取消 | 真实 GameState＋生成 Lua＋临时目录复现：原生执行后取消，save_pending 仍为 false | 在可能提交的 await 前保留门禁；取消继续传播，不能 finally 无条件解锁 |
| 保存伪完成 | 原生抛错／false 时 q.done 被置 true；写文件后丢回应可在没有 SaveComplete 时错误产生检查点 | done 仅来自完成事件；使用已有 NativeTransition 区分 confirmed、not_submitted、unknown |
| 覆盖旧检查点 | 撤销旧元数据失败时，旧记录仍可能被判可信 | 先按名称撤销本进程内可信资格，再尝试文件清理；不影响其他检查点 |
| 政体参数 | 本机原版 GovernmentScreen 向 RequestChangeGovernment 传 Hash，当前 builder 传 Index | 修正 native 标识语义，测试不能让 Hash 与 Index 恰好相等而掩盖问题 |
| 总督晋升 | 原版 HasPromotion 查询用 promotion.Hash，提交操作的参数用 Index | 分开建模查询标识与提交标识，避免机械统一 |
| 政策两阶段 | 原版政策付费解锁与 RequestPolicyChanges 是两个动作；当前实现同块连续写入 | 记录已支付与槽位变更阶段，避免重入重复付费 |
| 多回合过程 | 原版政体流程展示无政府期；间谍旅行／任务也需后续回合 | 本次命令的启动确认与长期最终结果分开，避免未决门禁与推进形成自锁 |
| 单位升级 | 原版 UnitPanel 的宽松可见性与真实资格查询不同；升级目标由原生结果返回，UnitUpgraded 按原 full ID 回读 | 不用升级列表第一项猜目标，不假定升级必然替换 ID |
| 建造道路 | Expansion2_Routes.xml 普通道路消耗一次建造次数；铁路不耗次数，但耗铁、煤各一 | 按 Routes_XP2／Route_ResourceCosts 核对，修正现有“道路不耗次数”文案 |
| 拒绝伟人 | 原版 LOC_GREAT_PEOPLE_PASS_DETAILS 明确消耗伟人点数，并把消耗作为其他玩家招募折扣；现有代码误写信仰 | 核对原 individual、对应类别点数及资格变化，修正文案和验收预期 |
| 宗教创建 | ReligionScreen 通过已赚取信条数、已装备信条集合和宗教归属决定阶段；三个原生命令分开提交 | 读取精确宗教与两个信条，保留已进入阶段；通知尚未清除不等于创建失败 |
| 商路 | TradeSupport 与 TradeOverview 的路线数据包含 TraderUnitID 和始末城市 owner／ID | 使用完整路线关联，不能用路线总数或任意同类商人确认 |
| 占城决定 | 原版 RazeCity 保存选中城市；当前 builder 每次重新取队首 | 保存请求 city_id 与原待决来源，重入不得处理下一城 |
| 来约响应遗漏 | respond_to_deal 的 outgoing counter 有 DiplomacyProgress；无 outgoing attempt 时仍走直接 SendWorkingDeal fallback | 18 收口须将该 incoming 分支纳入现有外交所有者；不能仅按方法名称认定已有覆盖 |

测试 seam：公开 MCP／GameState，真实所有者与真实生成 Lua；只模拟原生引擎、连接和临时文件。人工并发明确不在范围内。

原版来源均为本机安装包 Assets：Base/Assets/UI/Panels/UnitPanel.lua、Popups/GreatPeoplePopup.lua、ReligionScreen.lua、TradeSupport.lua、TradeOverview.lua、Popups/RazeCity.lua；DLC/Expansion2/Data/Expansion2_Routes.xml；Base/Assets/Text/en_US/InGameText.xml 的 LOC_GREAT_PEOPLE_PASS_DETAILS。

研究原始笔记位于本机 /tmp/civ6-mutation-*-research.md。正式验收以各票提交和测试记录为准。
