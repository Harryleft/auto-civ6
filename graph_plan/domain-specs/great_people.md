# 伟人（Great People）查询域实现规格

> 实现样板：现有 `src/civ_mcp/lua/great_people.py` 全链路；新增为纯增量 overview 工具（方案 B），动作层零改动。

## ⓪ 现状盘点

- Lua `src/civ_mcp/lua/great_people.py`（25.6kB）：`build_great_people_query` / `parse_great_people_response`、`build_gp_advisor_query` + parse、4 动作 builder（recruit/patronize/reject/activate）。
- dataclass `lua/models.py`：`GreatPersonInfo` / `GPAdvisorCity` / `GPAdvisorResult`。
- `GameState` `game_state.py:1399-1459`：`get_great_people` / `get_gp_advisor` / `recruit` / `patronize` / `reject` / `activate_great_person`。
- `narrate.py`：`narrate_great_people`（INT_MAX 成本过滤、`[CAN RECRUIT]`、individual_id 回显）。
- MCP `server/tools/world.py:265-366`：`get_great_people` / `get_gp_advisor` / `recruit` / `patronize` / `reject`；`actions.py:626-714`：`unit_action`(activate)。
- `docs/agent-tools.md`「商路与伟人」；`lua/__init__.py:83-92` 已导出。动作层不动。

## ① 官方 Lua API

来源 `$GAME/Assets/Base/Assets/UI/Popups/GreatPeoplePopup.lua`（1233 行已逐段核对）；`$GAME = ~/Library/Application Support/Steam/steamapps/common/Sid Meier's Civilization VI/Civ6.app/Contents/`。

`Game.GetGreatPeople()` 服务对象：

| API | 位置 | 说明 |
|---|---|---|
| `GetTimeline()` | L703 | 当前池，条目 `Class`/`Individual`/`Era`/`Claimant`（nil=未认领）/`Cost`/`TurnGranted`/`ActionNameText`/`ActionUsageText`/`ActionEffectText`/`PassiveNameText`/`PassiveEffectText`（L745-765） |
| `GetPastTimeline()` | L701 | 历史，仅已认领（L709） |
| `CanRecruitPerson(playerID, individual)` | L727 | |
| `CanRejectPerson` / `GetRejectCost` | L729/731 | |
| `CanPatronizePerson` / `GetPatronizeCost(playerID, individual, YieldTypes.GOLD/FAITH)` | L734-737 | 不可用返回大数，popup 以 `<1000000` 判可用 |
| `CountPeopleReceivedByPlayer(classID, playerID)` | L796 | |
| `GetEarnConditionsText` | L740 | |

玩家侧：

- `player:GetGreatPeoplePoints():GetPointsTotal(classID)`（L799）/ `GetPointsPerTurn(classID)`（L800）。
- `Game.GetPlayers{Major=true, Alive=true}`（L778）为 standings 范围。
- `localPlayer:GetDiplomacy():HasMet(playerID)`（L507/783-788）未met玩家名遮罩为 `LOC_DIPLOPANEL_UNMET_PLAYER`，观察者豁免。
- `GameInfo.GreatPersonClasses()` / `GreatPersonIndividuals` / `Eras`。
- `PlayerConfigurations[id]:GetCivilizationShortDescription()`（L712）。
- `GreatPersonIndividuals[entry.Individual].ActionCharges`（L739）。
- `HasCapability("CAPABILITY_GREAT_PEOPLE_CAN_RECRUIT"/"RECRUIT_WITH_GOLD"/"RECRUIT_WITH_FAITH"/"CAN_REJECT")`（L240-279）特性开关。
- `UI.RequestPlayerOperation(RECRUIT/PATRONIZE/REJECT_GREAT_PERSON)`（L913/926）；`Events.GreatPeoplePointsChanged`（L978/1215）。
- 官方 standings 排序（L803-812）：本方永远首行，其余 `PointsTotal` 降序。

交叉印证：github.com/chaorace/Civ6-UIFiles；civfanatics/CQUI_Community-Edition；Azurency/CQUI issue #380（跨补丁变更）。

## ② FireTuner 可达性

1. `GreatPeoplePopup.lua` 是 InGame 前端脚本，FireTuner 的 InGame Lua state 同上下文——仓库 `GameConnection.execute_write()`（connection.py:182-201，`require_sentinel=True`，5s）持续执行同类查询。
2. `build_great_people_query` 同通道已实跑 `GetTimeline`/`GetPointsTotal`/`GetPatronizeCost`/`CanRecruitPerson`；新增面仅 `GetPastTimeline`/`GetPointsPerTurn`/`CountPeopleReceivedByPlayer`/`GetPlayers{Major,Alive}`，同对象同上下文；`GetPastTimeline` 用 pcall 防护。
3. 执行模型：`tuner_client.py` Firaxis Nexus 线协议端口 4318；`connection.py:173` `execute_read`→GameCore，`:182` `execute_write`→InGame，`:204` `execute_mutation`。GP 查询全走 `execute_write`（InGame，纯读），与 barbarian/congress 同模式。
4. 只读无变异→治理路由 routine；按需工具，不进每回合治理快照（不加剧 V3）。

## ③ Lua 查询片段

新增于 `lua/great_people.py`；行协议沿用仓库约定：管道分隔、`|` 消毒 `gsub("|","/"):gsub("~","-")`、f-string Lua 表 `{{}}`、`{SENTINEL}` 结束、`_bail` 失败即终。

`def build_great_people_overview_query() -> str:` 要点：

- `local me=Game.GetLocalPlayer(); local gp=Game.GetGreatPeople(); gp==nil 则 _bail("ERR:NO_GP_SYSTEM|...")`。
- `myDiplo=Players[me]:GetDiplomacy()`。
- 第 1 段 standings：`for classInfo in GameInfo.GreatPersonClasses() do for _,p in ipairs(Game.GetPlayers{{Major=true,Alive=true}}) do`——pcall 包 `GetPointsTotal(classID)`/`GetPointsPerTurn(classID)`/`CountPeopleReceivedByPlayer(classID,pid)`，失败置 -1；`name=Locale.Lookup(PlayerConfigurations[pid]:GetCivilizationShortDescription())`；`pid~=me 且 not myDiplo:HasMet(pid)` 则 `name="Unmet"`；`print("GP_CLASS|"..className..classType..pid..name..pts..ppt..earned)`（name 经 gsub 消毒）。
- 第 2 段池：复用现有 `GP|` 循环源文本（timeline+ability+patronize costs+individual_id），零改动。
- 第 3 段历史：`local okH,past=pcall(function() return gp:GetPastTimeline() end); if okH and past then` 仅 `e.Claimant~=nil` 条目→cname 按 HasMet 遮蔽（`==me` 豁免），`print("GP_HIST|name|class|era|cname|TurnGranted或-1|Individual")`。
- 第 4 段己方在野：`for _,u in Players[me]:GetUnits():Members()`——`uInfo=GameInfo.Units[u:GetType()]`；`uInfo.GreatPersonClass~=nil` → pcall 取 `u:GetGreatPerson():GetActionCharges()`；`print("GP_UNIT|unitId|unitId|name|gpClass|x,y|charges")`。
- `print("{SENTINEL}")`。

完整源码：

```python
def build_great_people_overview_query() -> str:
    """Full Great People report: standings, pool, history, own idle GPs (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
local gp = Game.GetGreatPeople()
if gp == nil then {_bail("ERR:NO_GP_SYSTEM|Great People system not available")} end
local myDiplo = Players[me] and Players[me]:GetDiplomacy() or nil
-- 1) Per-class standings for all major alive players (mirrors popup L777-801)
for classInfo in GameInfo.GreatPersonClasses() do
    local classID = classInfo.Index
    for _, p in ipairs(Game.GetPlayers{{Major = true, Alive = true}}) do
        local pid = p:GetID()
        local ok, pts, ppt, earned = pcall(function()
            return p:GetGreatPeoplePoints():GetPointsTotal(classID),
                   p:GetGreatPeoplePoints():GetPointsPerTurn(classID),
                   gp:CountPeopleReceivedByPlayer(classID, pid)
        end)
        if not ok then pts, ppt, earned = -1, -1, -1 end
        local name = Locale.Lookup(PlayerConfigurations[pid]:GetCivilizationShortDescription())
        -- HasMet masking, same rule as the official popup (L783-788)
        if pid ~= me and (myDiplo == nil or not myDiplo:HasMet(pid)) then name = "Unmet" end
        print("GP_CLASS|" .. (Locale.Lookup(classInfo.Name):gsub("|", "/")) .. "|" .. classInfo.GreatPersonClassType
              .. "|" .. pid .. "|" .. name .. "|" .. pts .. "|" .. ppt .. "|" .. earned)
    end
end
-- 2) Current pool: reuse the existing GP| loop from build_great_people_query verbatim
--    (timeline entries with class/name/era/cost/claimant/my points/ability/patronize costs/individual_id)
-- 3) Claimed history (pcall: older binaries may lack GetPastTimeline)
local okH, past = pcall(function() return gp:GetPastTimeline() end)
if okH and past then
    for _, e in ipairs(past) do
        if e.Claimant ~= nil then
            local ci = GameInfo.GreatPersonClasses[e.Class]; local ii = GameInfo.GreatPersonIndividuals[e.Individual]
            local era = GameInfo.Eras[e.Era]
            local cname = "Unmet"
            if e.Claimant == me or (myDiplo and myDiplo:HasMet(e.Claimant)) then
                cname = Locale.Lookup(PlayerConfigurations[e.Claimant]:GetCivilizationShortDescription())
            end
            print("GP_HIST|" .. Locale.Lookup(ii.Name) .. "|" .. Locale.Lookup(ci.Name) .. "|"
                  .. (era and Locale.Lookup(era.Name) or "?") .. "|" .. cname
                  .. "|" .. (e.TurnGranted or -1) .. "|" .. e.Individual)
        end
    end
end
-- 4) Own great people on the map (side-channel made first-class)
for _, u in Players[me]:GetUnits():Members() do
    local ui = GameInfo.Units[u:GetType()]
    local gpc = ui and ui.GreatPersonClass or nil
    if gpc then
        local charges = -1
        pcall(function() charges = u:GetGreatPerson():GetActionCharges() end)
        print("GP_UNIT|" .. u:GetID() .. "|" .. u:GetID() .. "|" .. Locale.Lookup(u:GetName())
              .. "|" .. gpc .. "|" .. u:GetX() .. "," .. u:GetY() .. "|" .. charges)
    end
end
print("{SENTINEL}")
"""
```

说明:第 2) 段直接复用现有 `GP|` 循环源文本(抽成局部常量或复制均可,以最小 diff 为准);`GP_UNIT` 的 `unit_index` 与 `get_gp_advisor`/`unit_action` 所需索引一致(现有代码 unit id 即 index)。

## ④ dataclass

`lua/models.py` 新增，全默认值向后兼容：

- `GPPlayerPoints{player_id, player_name("Unmet" if masked), points_total=-1, points_per_turn=-1, instances_earned=-1}`。
- `GPClassStanding{class_name, class_type, entries:list[GPPlayerPoints]}`（官方序：本方首行余按 PointsTotal 降序）。
- `GPHistoryEntry{individual_name, class_name, era_name, claimant, turn_granted=-1, individual_id=0}`。
- `GPOwnUnit{unit_id(=index, 供 get_gp_advisor/unit_action), name, gp_class, x, y, charges=-1}`。
- `GreatPeopleOverview{standings:list[GPClassStanding], timeline:list[GreatPersonInfo](复用现有类型), history:list[GPHistoryEntry], own_units:list[GPOwnUnit]}`。

解析器 `parse_great_people_overview_response(lines)` 与现有解析同风格（`GP_CLASS|`/`GP_HIST|`/`GP_UNIT|` 前缀、`_int`、坏行跳过）。

## ⑤ narrate 样式

`narrate.py` 新增 `narrate_great_people_overview(ov)`：

```
=== Great People Overview ===
Standings (total points / per turn / received):
  Great Scientist: YOU 34/6 (2) | France 51/7 (3) | Unmet 12/1 (0) | ...   (每类 top-3+本方，截断防膨胀)
Current pool:   （现有 narrate_great_people 逐条格式原样复用：[CAN RECRUIT]/Ability/Patronize/(individual_id: N)）
Claimed history (latest 5):
  Turn 87 — Ada Lovelace (Great Scientist) claimed by France
Your great people on the map:
  Hypatia (Great Scientist) at (34,21), 1 charge — get_gp_advisor(unit_id=42) for placement
No history available.   （GetPastTimeline 不可用时显式降级，不伪造）
```

## ⑥ MCP 工具定义

`server/tools/world.py` 伟人块内追加；现有五工具与动作层零改动：

```python
@mcp.tool(annotations={"readOnlyHint": True})
async def get_great_people_overview(ctx: Context) -> str
```

docstring：

```
One-shot Great People report: standings, recruit pool, history, and your
idle great people. Returns four sections: 1. Points standings per great
person class for every major alive civ — total points, points per turn,
and great people already received. Civilizations you have not met are
masked as "Unmet". 2. Current recruit pool: each available individual
with era, recruit cost, ability, patronize gold/faith costs, and your
points toward that class (same rows as get_great_people; [CAN RECRUIT]
marks affordable ones). 3. History: already-claimed great people with
claimant and turn granted. 4. Your great person units on the map with
activation charges; pair with get_gp_advisor(unit_index) and
unit_action(action='activate'). Use this when deciding GP point
investment, patronage, or who will win a class race.
```

主体：

```python
gs = pipeline._get_game(ctx)

async def _run():
    ov = await gs.get_great_people_overview()
    return nr.narrate_great_people_overview(ov)

return await pipeline._logged(ctx, "get_great_people_overview", {}, _run)
```

GameState 侧（`game_state.py` 伟人块）：

```python
async def get_great_people_overview(self) -> lq.GreatPeopleOverview:
    lines = await self.conn.execute_write(lq.build_great_people_overview_query())
    return lq.parse_great_people_overview_response(lines)
```

`lua/__init__.py` 追加导出。

取舍：纯增量新工具（方案 B），符合 graph_plan §6 阶段四，零破坏；overview 稳定后可再把 `get_great_people` 收敛为精简视图。

## ⑦ ruleset 门控

不需要：

1. 伟人是基础机制（权威 UI 脚本在 Base/，`GameInfo.GreatPersonClasses` 在 Standard 即有）；与 weather 相反不存在 GameCore 恒 XP2 假阳性。
2. 场景规则集禁 GP：现有 `gp==nil → _bail("ERR:NO_GP_SYSTEM")` 已兜底；`GreatPersonClasses` 空表→空 standings，narrate 显式 "No Great People"。
3. 金币/信仰购买特性开关在数据面自然表达（`GetPatronizeCost` 不可用返回大数，narrate `<2_000_000_000` 过滤已处理）。

## ⑧ 测试计划

新建 `tests/test_great_people_overview.py`，仿 `test_barbarian_overview.py`；现有测试零改动：

1. `test_overview_query_covers_official_api`——builder 断言含 `GetPastTimeline`/`GetPointsPerTurn`/`CountPeopleReceivedByPlayer`/`Game.GetPlayers{{Major = true, Alive = true}}`/`HasMet`/`GP_CLASS|`/`GP_HIST|`/`GP_UNIT|`/`GP|`/`{SENTINEL}`/`ERR:NO_GP_SYSTEM`。
2. `test_parse_overview_happy_path`——2 类 × 3 玩家（含一个 Unmet）+ 1 `GP|` + 2 `GP_HIST`（含 TurnGranted）+ 1 `GP_UNIT` 逐字段断言。
3. `test_parse_overview_skips_malformed_lines`。
4. `test_narrate_overview_sections_and_masks`——Standings/Unmet/`[CAN RECRUIT]`/`claimed by`/`get_gp_advisor(unit_id=`/INT_MAX 成本被过滤。
5. `test_narrate_overview_degrades_without_history`——history 空→`No history available`。
6. 回归全绿。

真机验收（用户做）：一次 `get_great_people_overview` 全量输出 + 与 `get_great_people` 行一致性抽查。

## ⑨ 风险

1. `GetPastTimeline` 版本可用性（pcall 降级，低）。
2. 【需裁决】现有 `build_great_people_query` 无条件打印未met claimant 文明名 vs 官方 HasMet 门控——新工具已遮罩，是否同步修现有工具属行为变更，留用户裁决。
3. 输出体积（每类 top-3+本方、最新 5 条历史截断）。
4. Lua 杂散 print（前缀过滤天然免疫）。
5. 多 charges 同帧陈旧（查询侧只读不受影响）。
6. 补丁漂移（CQUI #380 先例；API 均当前安装实测存在）。
