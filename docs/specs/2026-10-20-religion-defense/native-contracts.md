# 宗教防御原生契约（仅静态第一方源，2026-10-20）

范围：只读 repo 与本机 Civ VI shipped UI/XML/中文文本。未连接游戏、未加载存档、未执行操作、未改工程代码。以下实际调用签名以文件为准；未由 shipped Lua 暴露的引擎细节明确留作实机只读观测。

第一方根目录 A：`~/Library/Application Support/Steam/steamapps/common/Sid Meier's Civilization VI/Civ6.app/Contents/Assets`。
下文所有 `Base/...` 与 `DLC/...` 均相对 A，可用 `nl -ba` 核验行号。

## 1. REMOVE_HERESY

- `Base/Assets/Gameplay/Data/UnitOperations.xml:36,95`：定义 `UNITOPERATION_REMOVE_HERESY`，VisibleInUI=true、HoldCycling=true、CategoryInUI=SPECIFIC，**没有 InterfaceMode**。
- `Base/Assets/UI/Panels/UnitPanel.lua:679–715`：对通用 operation，先 `CanStartOperation(pUnit,actionHash,nil,true)` 作松散 UI 可见性判断，再 `CanStartOperation(pUnit,actionHash,nil,false,OperationResultsTypes.NO_TARGETS)` 作真实可执行检查，返回 `tResults[UnitOperationResults.FAILURE_REASONS]`。松散 true 不能作为动作授权。
- `Base/Assets/UI/Panels/UnitPanel.lua:2493–2536`：通用 operation 仅有 InterfaceMode 才进入定点模式；否则 **`UnitManager.RequestOperation(pSelectedUnit,actionHash)`，没有 PARAM_X/Y**。因此不要把 spread_religion 的目标坐标协议无证据复制到 REMOVE_HERESY。
- 目标城市选择仍需精确归属：**该 shipped 源没有展开引擎从自身/邻接格选城算法**。可以按当前 actor 坐标及真实 `GetOperationTargets` 结果识别唯一实际作用城市；无唯一原生证明时拒绝，不猜最近城市。建议能力接口允许 caller 指定 intended city，仅作前置绑定/验证；不要声称原生提交一定使用这个 city 参数。
- `Base/Assets/Gameplay/Data/Units.xml:756`：Inquisitor 基础 `ReligiousStrength=75`, `SpreadCharges=3`, `TrackReligion=true`, `ReligionEvictPercent=100`, `RequiresInquisition=true`；`:926–927`：生产需 Temple 或 StaveChurch。
- `DLC/Expansion2/Data/Expansion2_Units.xml:44–49`：GS 把 Inquisitor `ReligionEvictPercent` 改为 **75**。按实际 GameInfo row 查询，不硬编码100%。
- `DLC/Expansion2/Text/Expansion2_Translations_Text.xml:202760–202762`：GS 官方文本为“可将他们领土上其他宗教的势力清除掉75%，并能对其他宗教单位发动神学战争”。只能用作通用规则；真实合法资格由 CanStartOperation 结果决定，不能由文本推定当前城市可清除。
- `Base/Assets/UI/Panels/UnitPanel.lua:2470–2474`：`OnUnitChargesChanged(player,unitId)` 触发刷新（事件只给 actor 身份，不给目标城市/压力/具体新charges）。
- **末次 charge 后是否销毁、UnitRemoved 与 OperationStarted 事件顺序，shipped 源未直接证明**。需支持最终 charge 消耗的真实见证：提交前保存 actor scalar IDs/type/religion/charges，提交后不再持有可能失效 userdata；要求 exact started event + 最后一次charge时 exact removed + 两条原生查找均不再是原始ID + 精确目标城市压力变化。不能把任意消失当成功，也不能对未证实销毁硬称原生规则。

### 压力效果证据

- `Base/Assets/UI/WorldView/CityBannerManager.lua:1226–1245`：`city:GetReligion():GetReligionsInCity()` 的 row `Religion, Followers, Pressure`；UI 将 **row.Pressure** 命名为 `LifetimePressure`，而独立的 `GetTotalPressureOnCity(religion)` 命名为当前 `Pressure`（流入速率）。
- `.../CityBannerManager.lua:1356–1359`：分别展示当前 pressure 与 lifetime pressure。因此清除操作前后应对比全部异教 **row.Pressure**，不能对比流入速率或仅看Followers/多数宗教。
- GS 75%为静态规则而非结果证明；浮点及舍入行为不在 XML 中，建议回报真实 before/after 及变化，不硬套必须精确 ×0.25。至少一个实际异教 stock 明确下降、宗教ID集合和城市owner/id/position严格绑定，才可确认清除效果。信徒未变化不等于操作失败。

## 2. 神学攻击：真实 operation、目标层、和平检查与路径

### 已由第一方证明

- `Base/Assets/Text/Vanilla_zh_Hans_CN.xml:47177–47187`：Missionary 和 Guru 不能发起神学战争但能防御；Apostle、Inquisitor 可发起。不要仅凭 ReligiousStrength>0 推定可主动攻击。真正资格仍由原生 CanStartOperation + preview 决定。
- `Base/Assets/UI/Civ6Common.lua:102–130`：`MoveUnitToPlot` 首先对 visible plot 调用 **`CombatManager.IsAttackChangeWarState(kUnit:GetComponentID(),plotX,plotY)`**；非空 list 会进入宣战确认。和平宗教战若该API不可读或返回将宣战对象，工具应拒绝/未知，不能自动宣战或以外交是否在战代替原生校验。
- `.../Civ6Common.lua:137–166`：普通非远程攻击采用 **`UnitOperationTypes.MOVE_TO`**；参数 `PARAM_X`, `PARAM_Y`，`PARAM_MODIFIERS=UnitOperationMoveModifiers.ATTACK + MOVE_IGNORE_UNEXPLORED_DESTINATION`（UI通用交互支持迷雾）。为避免误入未知，可限制visible且仅ATTACK，沿用repo已有即时攻击限制。没有找到独立 `THEOLOGICAL_COMBAT` operation。
- `Base/Assets/UI/WorldInput.lua:951–953`：`UnitManager.GetMoveToPathEx(kUnit,endPlotId)` 返回 `pathInfo.plots/.turns`。
- `.../WorldInput.lua:1033–1050`：用 **`Units.GetUnitsInPlotLayerID(x,y,MapLayers.ANY)`** 获取目标格单位，需同时 `PlayersVisibility:IsVisible(x,y)` 与 `IsUnitVisible(pUnit)`；这能包含宗教层，不应用默认 map layer。
- `.../WorldInput.lua:1052–1069`：注释“Hide destination indicator only if attack guaranteed this turn”；最终 `.turns` >1 是后续回合，不应当成本回合可攻击。repo既有 `_LUA_MELEE_ELIGIBILITY`（src/civ_mcp/lua/units.py:669–735）已处理GetMoveToPathEx完整dense序列、起终点、1basedturn、ZOC、canstart等，可共用。
- `Base/Assets/UI/Panels/UnitPanel.lua:3889–3929`：原生 preview `CombatManager.SimulateAttackInto(attacker,eCombatType,x,y)`，默认 eCombatType=nil 时让引擎判定；`:1436,1589–1590,1737` 明确 `CombatTypes.RELIGIOUS`，不能以melee数值替代宗教预演。
- `.../UnitPanel.lua:1748–1751,1775`：preview side `[ATTACKER]/[DEFENDER]` 含 `[COMBAT_STRENGTH]`, `[STRENGTH_MODIFIER]`, `[DAMAGE_TO]`；`:3170–3171,3587` 的 side `[ID]` 是component identity表。
- **必须exact匹配preview attacker和defender componentID，要求COMBAT_TYPE==RELIGIOUS**。如此同格军事单位/平民/不同宗教目标均不能误认成神学战。

### 事件和确认证据：能证明与不能证明

- `Base/Assets/UI/UnitFlagManager.lua:1714–1723`：combat vis side identity 字段是 **`.componentType`, `.playerID`, `.componentID`**，UNIT类型需 `ComponentType.UNIT`。
- `.../UnitFlagManager.lua:1728–1747`：**`Events.CombatVisBegin/CombatVisEnd` callback(kVisData)**，side读取 **`kVisData[CombatVisType.ATTACKER]`, `[DEFENDER]`, `[INTERCEPTOR]`, `[ANTI_AIR]`**。`:2034–2035` 注册这些 Events。可用它见证精确 actor→target 身份，但此side不包含HP/伤害字段。
- **没有在 shipped Assets 的 Lua 文件中找到 `Events.Combat.Add` 或其回调参数/CombatResultParameters结构的使用。** 不能把 CombatManager.SimulateAttackInto 的 preview table未经验证当 Events.Combat 的事件table，也不能假定CombatVis有COMBAT_TYPE。若实现需要真正 `Events.Combat`，需先实机只读监听观测结构；离线mock不能建立该原生契约。
- 在已有第一方契约内，建议 exact OperationStarted + exact CombatVisEnd actor/target + 后置精确HP下降/双侧死亡事实 + 同religion/type/seed/turn身份范围；宗教preview只证明提交前类型，CombatVis证明实际参与者，postHP证明发生效果。任一证据欠缺保留pending/unknown，不自动重发。
- 末次击杀需 exact removed/native lookup缺失，多数情况下攻击者也可能死亡；必须允许双侧伤亡，而不是把actor存在当成功必要条件或只看目标位置空了。

## 3. 谴责资格 / WorldReligion B 当前状态 / 有效期

- `Base/Assets/UI/Panels/UnitPanel.lua:461–472`：command采用松散 `CanStartCommand(pUnit,actionHash,true)` 决定显示、严格 **`CanStartCommand(pUnit,actionHash,false,true)`** 获得当前实际资格和结果；`:504–527` UI将command结果的 ACTION_NAME/ADDITIONAL_DESCRIPTION/FAILURE_REASONS 按 `UnitOperationResults` key显示（注意这个原生UI使用的是operation key，不要凭命名改成不存在的key）。
- `.../UnitPanel.lua:2452–2460`：`OnUnitCommandStarted(player,unitId,hCommand,iData1)` 中 hCommand==`UnitCommandTypes.CONDEMN_HERETIC` 时播放声效。
- `Base/Assets/Text/Vanilla_zh_Hans_CN.xml:38419–38427`：拒绝文本包括未交战、目标信奉本方创建宗教、目标信奉城市主流宗教。`DLC/Expansion2/Text/Expansion2_Translations_Text.xml:200372–200374`还存在议会免疫拒绝文本。应暴露原生失败原因，不用一条“必须交战”覆盖全部当前资格。
- `DLC/Expansion2/Data/Expansion2_Congress.xml:91`：`WC_RES_WORLD_RELIGION` TargetKind=RELIGION；`:142–143` WhichEffect=1增强宗教单位，**WhichEffect=2 ANYONE_CONDEMNS_FOR_FAVOR**；`:239–240` modifier type `MODIFIER_RELIGION_ADJUST_ANYONE_CONDEMNS_FAVOR`；`:452–455` Amount25。
- `DLC/Expansion2/Text/Expansion2_Translations_Text.xml:198647–198649`：B 官方描述为所有文明可对所选宗教单位谴责并得25Favor。
- **当前active不是review**：`DLC/Expansion2/UI/Additions/WorldCongressPopup.lua:2008–2029` 的 `PopulateActiveEffects()` 使用 `Game.GetWorldCongress():GetMeetingStatus()` 与 **`:GetResolutions(localPlayerID)`**，遍历table的table行（有非行`Stage`），用 `GameInfo.Resolutions[kResolutionData.Type]`，`ChosenOption` 描述；`:2068,2074` 读 `ChosenThing` 和 `ChosenLabel`。
- 剩余有效期UI用 **`GetMeetingStatus().TurnsLeft + 1`**，原始字段不是直接UI剩余回合，不应把会期长度硬编码25/30，也不要虚构每条resolution自己的expiration字段。
- `.../WorldCongressPopup.lua:1819–1834` 的 `GetReview(localPlayerID).Resolutions` 是会议结果复查；`:697–742` 的 `GetPreviousVotesOnResolution` 是历史投票。两者不能当成当前active effect依据。
- `ChosenThing` / `ChosenOption` / `ChosenLabel` 在UI被Locale.Lookup，可能是LOC字符串而非稳定数字ID；**此静态代码未证明active行含 numeric targetReligion 或 winningEffect字段**。应保留原生raw字段并按GameInfo.Resolutions.Type精确识别WorldReligion；若无法唯一对应当前宗教ID或A/B，报告known active raw description但target/effect=unknown，不能按翻译或历史目标猜测。
- 对on-tile military actor，严格 `CanStartCommand` 是当前最终可执行授权；其false不应自动推定“无WorldReligion B”，因为还有宗教归属/城市主流/行动力等资格。
- 若Congress对象/API/字段缺失：报告 `unavailable/unknown` 和具体原因；只有成功取得完整当前active列表且无该Type才能报告当前无WorldReligion生效。unknown不能伪装为false、0turns或历史决议仍生效。

## 尚需实机只读观测的问题

1. REMOVE_HERESY GetOperationTargets 是否给唯一实际作用city plot、空params与显式coords差异（不能靠offline stub宣布）。
2. 最终charge的原生删除事件与顺序。
3. 若要用Events.Combat而非已证实CombatVisEnd，需要实际事件参数结构观测。
4. active WorldReligion row能否稳定提供effect/target数字ID；UI只展示LOC字段，语义映射不能猜。

本笔记不建议加载/重开下一局；若当前终局无法产生合法候选，可以完成只读与离线验证，明确实机动作验收尚未覆盖，并为下一次获授权存档测试列出精确case。
