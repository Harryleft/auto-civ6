# 域规格：天气/气候（Gathering Storm 气候系统）— `get_climate_overview`

> 调研基线：本地游戏安装（Steam macOS 版，含 Expansion1/Expansion2 DLC）官方 Lua/XML，
> 与 civ6-mcp 仓库 `main`（server 已拆分为 `server/tools/` 包）。
> 结论先行：**FireTuner 可达，成本正常，建议实施**。全套 API 由 Firaxis 自己的
> InGame UI 脚本 `ClimateScreen.lua` 原样消费，无需任何黑魔法。

## ① Lua API 清单（标注来源）

游戏根目录：`~/Library/Application Support/Steam/steamapps/common/Sid Meier's Civilization VI/Civ6.app/Contents/`
（下称 `$GAME`）。气候 UI 脚本：`$GAME/Assets/DLC/Expansion2/UI/Additions/ClimateScreen.lua`（1202 行，本地已逐段核对）。

### `GameClimate`（GameCore 暴露的全局表）

| API | 语义 | 来源行号（ClimateScreen.lua） |
|---|---|---|
| `GetTotalCO2Footprint()` | 全世界累计 CO₂ 排放 | 128, 398, 599 |
| `GetPlayerCO2Footprint(playerID, bLastTurn)` | 单玩家 CO₂（当前/上回合） | 134, 399, 820 |
| `GetCO2FootprintModifier()` | 毁林带来的 CO₂ 足迹修正（百分比） | 401, 423 |
| `GetTemperatureChange()` | 相对基线的温升（摄氏度） | 410 |
| `GetDeforestationType()` | 毁林等级索引（→ `GameInfo.DeforestationLevels`；<0 = 无） | 412 |
| `GetStormPercentChance()` / `GetStormClimateIncreasedChance()` | 风暴概率 / 气候加剧增量 | 425-426 |
| `GetFloodPercentChance()` / `GetFloodClimateIncreasedChance()` | 洪水概率 / 增量 | 428-429 |
| `GetEruptionPercentChance()` | 火山喷发概率 | 438 |
| `GetDroughtPercentChance()` / `GetDroughtClimateIncreasedChance()` | 干旱概率 / 增量 | 440-441 |
| `GetNextIceLossTurns()` | 距下次极冰消融的回合数 | 443 |
| `GetTilesFlooded()` / `GetTilesSubmerged()` | 已淹没 / 已沉没地块数 | 445-446 |
| `GetNextSeaLevelRiseTurns()` | 距下次海平面上升的回合数 | 447 |
| `GetClimateChangeLevel()` | 当前累计气候变化点数 | 1046 |
| `GetClimateChangeForLastSeaLevelEvent()` | 已触发最后一次海平面事件的点数门槛（未触发为负） | 954 |
| `GetClimateChangeFromRealism()` / `GetClimateChangeFromTemperature()` | 点数来源分解（世界真实感 / 温升） | 1047-1048 |
| `GetPlayerResourceCO2Footprint(playerID, resIndex, bLastTurn)` | 单玩家单资源 CO₂ | 765, 895 |
| `GetPlayerRawResourceConsumption(playerID, resIndex, bLastTurn)` | 单玩家单资源原始消耗 | 771, 912 |

### `GameRandomEvents`（GameCore 暴露；无 .lua 支持文件，纯原生全局）

| API | 语义 | 来源行号 |
|---|---|---|
| `GetCurrentTurnEvent()` | 本回合事件（可 nil）。字段：`RandomEvent`（RandomEvents 表 Index）、`Name`（LOC key）、`CurrentLocation`（plot index）、`CurrentDirection`、`FertilityAdded`、`TilesDamaged`、`UnitsLost`、`PopLost` | 211-345 |
| `GetCurrentAffectedCities()` | 当前事件受影响城市列表，元素 `{CityOwner, CityID}` | 296-310 |
| `GetEventsForTurn(turn)` | 指定回合的历史事件（同一形状，`StartLocation`）；UI 按"每回合至多一条"消费 | 625 |

### 周边管理器（同为 ClimateScreen.lua 直接消费）

- `RiverManager.GetNumRivers()` / `GetNumFloodableRivers()`（430-431）
- `MapFeatureManager.GetNumNormalVolcanoes()` / `GetNumActiveVolcanoes()` / `GetNumEruptions()` / `GetNumNaturalWonderVolcanoes()`（433-437）

### GameInfo 数据表（`$GAME/Assets/DLC/Expansion2/Data/Expansion2_RandomEvents.xml`）

- `RandomEvents`：字段 `RandomEventType`、`Name`、`EffectOperatorType`（`STORM`/`DROUGHT`/`VOLCANO`/`FEATURE`/`FLOODPLAIN`/`SEA_LEVEL`/`NUCLEAR_ACCIDENT`）、`Severity`、`ClimateChangePoints`（仅 SEA_LEVEL 1..7 行为 2..8，升序）、`IceLoss`、`Global`。
- `CoastalLowlands`：1M/2M/3M 低地 → `FloodedEvent`/`SubmergedEvent` 映射。
- `DeforestationLevels`、`RealismSettings`（5 档，各自带 ClimateChangePoints 贡献）。

### 海平面阶段（phase）推导 —— 官方算法（ClimateScreen.lua:954-968）

```lua
-- 逐行扫描 GameInfo.RandomEvents，取 EffectOperatorType == "SEA_LEVEL" 的行：
-- firstSeaEvent = 第一条 SEA_LEVEL 行的 Index；
-- 若某行 ClimateChangePoints == GameClimate.GetClimateChangeForLastSeaLevelEvent()
--   则 phase = 该行 Index - firstSeaEvent + 1（1..7），阶段名 = Locale.Lookup(row.Name)；
-- 无匹配（门槛为负/低于首个门槛）→ phase 0（"Climate Change Phase 0"，即尚未海平面上升）。
```

## ② 可达性论证

1. **InGame UI 上下文已验证可达**：`ClimateScreen.lua` 是 InGame 前端脚本（位于 `Expansion2/UI/Additions/`，经 `ContextPtr`/`Controls` 注册），它在顶层直接引用 `GameClimate`、`GameRandomEvents`、`RiverManager`、`MapFeatureManager`。FireTuner 的 `InGame` Lua state 就是这个上下文——本仓库 `GameConnection.execute_write()`（`connection.py:183`）已经持续用它执行 World Congress 等查询。**Firaxis 自己的 UI 就是"这些 API 在 InGame 上下文可用"的活证据。**
2. **GameCore 上下文亦可期**：`$GAME/Assets/DLC/Expansion2/Binaries/Win64/GameCore_XP2_FinalRelease.map` 符号表含 `GameClimate`，即 GameCore DLL 原生导出（macOS 包内为同一二进制家族）。但本规格**不依赖** GameCore 通道，统一走 `execute_write`（InGame），与 `get_world_congress` 同通道，规避双上下文差异风险。
3. **规则集守卫必须有**（`_helpers.py` 的 `_lua_require_ruleset` 文档已明示原因）：装了 GS DLC 后 GameCore 永远是 XP2 二进制，Standard/R&F 对局里 `GameClimate` 全局与部分数据库行**可能仍存在**，能力探测会假阳性。规则集名（`GameConfiguration.GetRuleSet()`，回退 `GameConfiguration.GetValue("RULESET")`）才是权威开关。
4. **一个只读工具就够**：所有 API 均为纯读取，无 `UI.RequestPlayerOperation`，无变异；按管道归类为 `routine` 路由。

## ③ Lua 片段（新文件 `src/civ_mcp/lua/climate.py`）

Builder/Parser 对照 `congress.py` 的行协议约定（管道分隔、`|`/`~` 消毒、`{SENTINEL}` 结束、f-string 内 Lua 表用 `{{`）：

```python
"""Climate domain — Lua builders and parsers (Gathering Storm only)."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _bail, _lua_require_ruleset
from civ_mcp.lua.models import ClimateOverview


def build_climate_overview_query(history_turns: int = 30) -> str:
    """Get world climate report: CO2, phase, risks, current + recent events (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
{_lua_require_ruleset("RULESET_EXPANSION_2", "ERR:NO_CLIMATE_IN_RULESET")}
if GameClimate == nil or GameRandomEvents == nil or GameInfo.RandomEvents == nil
then {_bail("ERR:NO_CLIMATE_IN_RULESET")} end
local curTurn = Game.GetCurrentGameTurn()

-- Phase derivation mirrors ClimateScreen.lua:UpdateClimateChangeEventsData()
local firstSeaEvent = -1
local phase = 0
local phaseName = "Phase 0"
local lastThreshold = GameClimate.GetClimateChangeForLastSeaLevelEvent()
for row in GameInfo.RandomEvents() do
    if row.EffectOperatorType == "SEA_LEVEL" then
        if firstSeaEvent == -1 then firstSeaEvent = row.Index end
        if row.ClimateChangePoints == lastThreshold then
            phase = row.Index - firstSeaEvent + 1
            local lok, lname = pcall(Locale.Lookup, row.Name)
            phaseName = (lok and lname) or tostring(row.RandomEventType)
        end
    end
end

local co2Total = GameClimate.GetTotalCO2Footprint()
local co2Self = GameClimate.GetPlayerCO2Footprint(me, false)
local co2SelfLast = GameClimate.GetPlayerCO2Footprint(me, true)
local defor = GameClimate.GetDeforestationType()
local deforName = ""
if defor ~= nil and defor >= 0 and GameInfo.DeforestationLevels[defor] then
    local dok, dname = pcall(Locale.Lookup, GameInfo.DeforestationLevels[defor].Name)
    deforName = (dok and dname) or ""
end
phaseName = phaseName:gsub("|", "/"):gsub("~", "-")
deforName = deforName:gsub("|", "/"):gsub("~", "-")
print("CLIMATE|" .. phase .. "|" .. GameClimate.GetClimateChangeLevel()
    .. "|" .. GameClimate.GetClimateChangeFromRealism()
    .. "|" .. GameClimate.GetClimateChangeFromTemperature()
    .. "|" .. lastThreshold
    .. "|" .. GameClimate.GetNextSeaLevelRiseTurns()
    .. "|" .. GameClimate.GetNextIceLossTurns()
    .. "|" .. GameClimate.GetTilesFlooded()
    .. "|" .. GameClimate.GetTilesSubmerged()
    .. "|" .. GameClimate.GetTemperatureChange()
    .. "|" .. co2Total .. "|" .. co2Self .. "|" .. co2SelfLast
    .. "|" .. GameClimate.GetCO2FootprintModifier() .. "|" .. deforName)
print("CLIMATE_RISK|" .. GameClimate.GetStormPercentChance()
    .. "|" .. GameClimate.GetStormClimateIncreasedChance()
    .. "|" .. GameClimate.GetFloodPercentChance()
    .. "|" .. GameClimate.GetFloodClimateIncreasedChance()
    .. "|" .. GameClimate.GetEruptionPercentChance()
    .. "|" .. GameClimate.GetDroughtPercentChance()
    .. "|" .. GameClimate.GetDroughtClimateIncreasedChance()
    .. "|" .. RiverManager.GetNumRivers() .. "|" .. RiverManager.GetNumFloodableRivers()
    .. "|" .. MapFeatureManager.GetNumNormalVolcanoes()
    .. "|" .. MapFeatureManager.GetNumActiveVolcanoes()
    .. "|" .. MapFeatureManager.GetNumEruptions())

-- Per-player CO2 (all ever-alive majors, mirroring TabCO2ByCiviliation;
-- unmet civs get a masked name)
local pDiplo = Players[me]:GetDiplomacy()
local topID = -1
local topCO2 = 0
for _, pPlayer in ipairs(PlayerManager.GetWasEverAliveMajors()) do
    local pid = pPlayer:GetID()
    local co2 = GameClimate.GetPlayerCO2Footprint(pid, false)
    if co2 > topCO2 then topCO2 = co2; topID = pid end
    local cName = "Unmet Player"
    if pid == me then
        cName = "You"
    elseif pDiplo:HasMet(pid) then
        local cok, cres = pcall(Locale.Lookup,
            PlayerConfigurations[pid]:GetCivilizationShortDescription())
        if cok and cres then cName = cres end
    end
    print("CLIMATE_CO2|" .. pid .. "|" .. cName:gsub("|", "/"):gsub("~", "-") .. "|" .. co2)
end

local function fmtEvent(ev, evDef, turn, useStart)
    local evType = tostring(evDef.RandomEventType)
    local op = tostring(evDef.EffectOperatorType or "")
    local isGlobal = evDef.Global == true
    local plotIdx = useStart and ev.StartLocation or ev.CurrentLocation
    local x, y = -1, -1
    local revealed = isGlobal  -- global events are always reportable
    local pPlot = plotIdx ~= nil and Map.GetPlotByIndex(plotIdx) or nil
    if pPlot ~= nil then
        local vis = PlayersVisibility[me]
        if vis ~= nil and vis:IsRevealed(pPlot:GetX(), pPlot:GetY()) then
            revealed = true
            x = pPlot:GetX(); y = pPlot:GetY()
        end
    end
    local nm = ""
    if ev.Name ~= nil then
        local nok, nres = pcall(Locale.Lookup, ev.Name)
        nm = (nok and nres) or tostring(ev.Name)
    end
    nm = nm:gsub("|", "/"):gsub("~", "-")
    print("CLIMATE_EV|" .. turn .. "|" .. evType .. "|" .. op .. "|" .. nm
        .. "|" .. (isGlobal and 1 or 0) .. "|" .. (revealed and 1 or 0)
        .. "|" .. x .. "|" .. y
        .. "|" .. (ev.FertilityAdded or 0) .. "|" .. (ev.TilesDamaged or 0)
        .. "|" .. (ev.UnitsLost or 0) .. "|" .. (ev.PopLost or 0))
end

-- Current event + affected cities (only for non-global, non-sea-level events)
local cur = GameRandomEvents.GetCurrentTurnEvent()
if cur ~= nil then
    local curDef = GameInfo.RandomEvents[cur.RandomEvent]
    if curDef ~= nil then
        local evType = tostring(curDef.RandomEventType)
        local op = tostring(curDef.EffectOperatorType or "")
        local isGlobal = curDef.Global == true
        local x, y = -1, -1
        local revealed = isGlobal
        local pPlot = cur.CurrentLocation ~= nil and Map.GetPlotByIndex(cur.CurrentLocation) or nil
        if pPlot ~= nil then
            local vis = PlayersVisibility[me]
            if vis ~= nil and vis:IsRevealed(pPlot:GetX(), pPlot:GetY()) then
                revealed = true; x = pPlot:GetX(); y = pPlot:GetY()
            end
        end
        local nm = ""
        if cur.Name ~= nil then
            local nok, nres = pcall(Locale.Lookup, cur.Name)
            nm = (nok and nres) or tostring(cur.Name)
        end
        nm = nm:gsub("|", "/"):gsub("~", "-")
        print("CLIMATE_CUR|" .. curTurn .. "|" .. evType .. "|" .. op .. "|" .. nm
            .. "|" .. (isGlobal and 1 or 0) .. "|" .. (revealed and 1 or 0)
            .. "|" .. x .. "|" .. y
            .. "|" .. (cur.FertilityAdded or 0) .. "|" .. (cur.TilesDamaged or 0)
            .. "|" .. (cur.UnitsLost or 0) .. "|" .. (cur.PopLost or 0))
        for _, ac in ipairs(GameRandomEvents.GetCurrentAffectedCities() or {{}}) do
            local cName = "Unmet Player City"
            if ac.CityOwner == me or pDiplo:HasMet(ac.CityOwner) then
                local pCity = Players[ac.CityOwner]:GetCities():FindID(ac.CityID)
                if pCity ~= nil then
                    local ck, cr = pcall(Locale.Lookup, pCity:GetName())
                    if ck and cr then cName = cr end
                end
            end
            print("CLIMATE_CITY|" .. ac.CityOwner .. "|" .. ac.CityID
                .. "|" .. cName:gsub("|", "/"):gsub("~", "-"))
        end
    end
end

-- Bounded history scan (UI scans to turn 0; we bound by history_turns)
local fromTurn = math.max(0, curTurn - {history_turns})
for t = curTurn, fromTurn, -1 do
    local ev = GameRandomEvents.GetEventsForTurn(t)
    if ev ~= nil then
        local evDef = GameInfo.RandomEvents[ev.RandomEvent]
        if evDef ~= nil then
            fmtEvent(ev, evDef, t, true)
        end
    end
end
print("{SENTINEL}")
"""
```

要点：
- `history_turns` 由 Python 侧 clamp 到 `[1, 200]` 再插值（builder 入口处），杜绝负数/超大扫描。
- 历史里**保留** `NUCLEAR_ACCIDENT`（官方 UI 在历史页刻意排除；对 agent 而言"自家核电站出事"必须可见——spec 决策，见 ⑨）。
- `GetWasEverAliveMajors()` 与官方逐文明页一致（含已灭国文明的累计 CO₂）。

## ④ dataclass（追加到 `src/civ_mcp/lua/models.py`）

沿用现有 plain `@dataclass` 风格（同 `WorldCongressStatus`）：

```python
@dataclass
class ClimateContributor:
    player_id: int
    civ_name: str          # "Unmet Player" for unmet majors
    co2: float


@dataclass
class ClimateEventRecord:
    turn: int
    event_type: str        # e.g. RANDOM_EVENT_BLIZZARD_CRIPPLING
    operator: str          # EffectOperatorType: STORM/DROUGHT/VOLCANO/SEA_LEVEL/...
    name: str              # localized event name, "" if unnamed
    is_global: bool
    revealed: bool         # False = location in fog (coords unknown)
    x: int                 # -1 when not revealed or global without location
    y: int
    fertility_added: int
    tiles_damaged: int
    units_lost: int
    pop_lost: int


@dataclass
class ClimateAffectedCity:
    owner_id: int
    city_id: int
    name: str              # "Unmet Player City" when owner unmet


@dataclass
class ClimateOverview:
    """Full Gathering Storm climate report (World Climate screen parity)."""
    phase: int                      # sea-level phase 0-7
    phase_name: str
    climate_change_points: float
    points_from_realism: float
    points_from_temperature: float
    last_sea_level_threshold: float # negative = no rise yet
    next_sea_level_rise_turns: int  # -1 = n/a
    next_ice_loss_turns: int        # -1 = n/a
    tiles_flooded: int
    tiles_submerged: int
    temperature_change: float       # Celsius vs baseline
    co2_total: float
    co2_self: float
    co2_self_last_turn: float
    co2_footprint_modifier: float
    deforestation_level: str        # "" when none
    storm_chance: float
    storm_increase: float
    flood_chance: float
    flood_increase: float
    eruption_chance: float
    drought_chance: float
    drought_increase: float
    rivers_total: int
    rivers_floodable: int
    volcanoes_total: int
    volcanoes_active: int
    volcano_eruptions_total: int
    current_event: ClimateEventRecord | None = None
    affected_cities: list[ClimateAffectedCity] = field(default_factory=list)
    contributors: list[ClimateContributor] = field(default_factory=list)
    event_history: list[ClimateEventRecord] = field(default_factory=list)
```

Parser `parse_climate_response(lines) -> ClimateOverview`（同文件）：逐行按前缀分派
`CLIMATE|` / `CLIMATE_RISK|` / `CLIMATE_CO2|` / `CLIMATE_CUR|` / `CLIMATE_CITY|` / `CLIMATE_EV|`；
计数用 `_int()`（Lua 整数打印为 `3.0`），比率用 `float()`；`CLIMATE_CO2` 的占比（share）
在 **Python 侧**由 `co2_total` 计算，不进 Lua。`top contributor` 不单独出字段——
narrate 从 `contributors` 里取最大值（单一事实源）。

## ⑤ narrate 样式（`narrate.py` 追加 `narrate_climate_overview`）

对照 `narrate_barbarian_overview` 的"行动队列"取向 + `narrate_world_congress` 的告警前置：

```python
def narrate_climate_overview(status: lq.ClimateOverview) -> str:
    """Format the climate report: phase first (irreversible!), then CO2, risks, events."""
    lines = ["=== CLIMATE REPORT ==="]
    # Phase header — the one thing the agent cannot undo
    if status.phase >= 4:
        lines.append(f"!! SEA LEVEL PHASE {roman(status.phase)} ({status.phase_name}) — "
                     "storm/flood fertility bonuses GONE; move coastal assets NOW")
    elif status.phase >= 1:
        lines.append(f"Sea level phase {roman(status.phase)} ({status.phase_name})")
    else:
        lines.append("Sea level phase 0 — no rise yet")
    lines.append(
        f"Points: {status.climate_change_points:.1f} "
        f"(realism {status.points_from_realism:.1f} + temp {status.points_from_temperature:.1f})"
    )
    if status.next_sea_level_rise_turns >= 0:
        lines.append(f"Next sea-level rise in ~{status.next_sea_level_rise_turns} turns | "
                     f"polar ice loss in ~{status.next_ice_loss_turns}")
    # CO2 with our own share and trend vs last turn
    ...
    # Risk line: "storms 12% (+8), floods 9% (+4), eruptions 20%, droughts 7% (+3)"
    # Current event block (name, location if revealed, affected cities, damage)
    # History tail: "T85 Tornado Family — 3 tiles damaged, 1 unit lost" (newest first)
```

narrate 内部小工具 `roman(n)`（I..VII）只服务本函数；文案面向 agent 决策
（"洪水风险高→别把单位驻扎河漫滩"、"phase 4+→沿海资产搬迁"），不做装饰。

## ⑥ MCP 工具定义

**GameState**（`game_state.py`，紧邻 World Congress 区块）：

```python
    # ------------------------------------------------------------------
    # Climate (InGame context, Gathering Storm only)
    # ------------------------------------------------------------------

    async def get_climate_overview(self, history_turns: int = 30) -> lq.ClimateOverview:
        lua = lq.build_climate_overview_query(history_turns)
        lines = await self.conn.execute_write(lua)
        _raise_query_error(lines)
        return lq.parse_climate_response(lines)
```

**MCP 工具**（`server/tools/world.py` 追加，与 `get_world_congress` 同域同款）：

```python
@mcp.tool(annotations={"readOnlyHint": True})
async def get_climate_overview(ctx: Context, history_turns: int = 30) -> str:
    """Get the Gathering Storm climate report: sea-level phase, CO2, disaster
    risks, and recent weather events (Gathering Storm ruleset only).

    Args:
        history_turns: How many turns of event history to include (1-200, default 30).

    Shows: sea-level phase and points to next rise, world/your CO2 and top
    contributors, storm/flood/eruption/drought risk percentages, this turn's
    disaster with affected cities, and recent event history with damage.
    Call every ~10 turns, or after any flood/volcano/blizzard notification.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        overview = await gs.get_climate_overview(history_turns)
        return nr.narrate_climate_overview(overview)

    return await pipeline._logged(
        ctx, "get_climate_overview", {"history_turns": history_turns}, _run
    )
```

接线清单：`lua/climate.py` 新文件 + `lua/models.py` 追加 4 个 dataclass + `game_state.py`
一个方法 + `narrate.py` 一个函数 + `tools/world.py` 一个工具（import 副作用自动注册，无需改
`assembly.py`/`tools/__init__.py`）+ `docs/agent-tools.md` 补一行。`result_filter` 只管 3 个
belief 工具，无需登记。

## ⑦ 新能力位 `climate` + 规则集取值表 + Standard 显式错误

### 定义（三处，照 `world_congress` 抄）

`governance/capabilities.py`：

```python
CAPABILITY_NAMES = frozenset(
    {
        "governors", "ages", "dedications", "alliances", "diplomatic_favor",
        "world_congress", "resource_stockpiles",
        "climate",                      # ← 新增
        *_COMMON_CAPABILITIES,
    }
)

RULESET_CAPABILITIES = MappingProxyType({
    RULESET_STANDARD:      RulesetCapabilities(..., climate=False, ...),
    RULESET_EXPANSION_1:   RulesetCapabilities(..., climate=False, ...),
    RULESET_EXPANSION_2:   RulesetCapabilities(..., climate=True, ...),
})
```

`governance/models.py` 的 `RulesetCapabilities`：新增字段 `climate: bool = False`，
**并把它加进 `__post_init__` 的 `_strict_bool` 校验元组**（漏加不会报错，但会静默失去
严格校验——`test_capabilities_reject_truthy_non_boole` 只测了 governors）。

### 全规则集取值表

| ruleset | `climate` | 依据 |
|---|---|---|
| `RULESET_STANDARD` | `False` | 原版无气候系统；气候数据全链路不存在 |
| `RULESET_EXPANSION_1` (R&F) | `False` | 气候/风暴/海平面是 GS（XP2）机制，R&F 无 |
| `RULESET_EXPANSION_2` (GS) | `True` | ClimateScreen.lua、RandomEvents.xml 均在 Expansion2 DLC |

消费方式与 `resource_stockpiles` 一致：`capability_enabled(ruleset, "climate")`。

### Standard（及 R&F）显式错误文案

- Lua 侧：`ERR:NO_CLIMATE_IN_RULESET`（`_lua_require_ruleset` 守卫在前，`GameClimate == nil`
  兜底同码），`_raise_query_error` 提升为 `LuaError`。
- MCP 侧最终用户可见文案：**`Error: ERR:NO_CLIMATE_IN_RULESET`**（`pipeline._logged` 的
  `except (LuaError, ValueError)` 分支，`pipeline.py:614`，与 WC 的
  `Error: ERR:NO_WORLD_CONGRESS_IN_RULESET` 完全同型）。docstring 已声明
  "Gathering Storm ruleset only"。
- 不需要 Python 侧重复拦截（与 get_world_congress 相同哲学：Lua 守卫是单一权威点）。

### 对 graph / snapshot / freeze / canonical_json / `_bool_mapping` 的兼容性

- **消费面**：`RulesetCapabilities` 只被 `governance/snapshot.py`（显式字段读取）与
  `governance/capabilities.py` 消费；`graph/` 包零引用（已 grep 验证）。新增字段不触达图模型。
- **`_canonical` / canonical_json**：`snapshot.py:74` 按数据类字段遍历，新字段自动出现在
  canonical payload 与 belief observation 里（`"climate": false/true`），无需改代码。
  旧 JSONL 事件里没有该键——所有读回路径按 dict 取键，不重建 dataclass，缺键无影响。
- **freeze / `_stable_snapshot_id`**：`snapshot.py:101` 对含 capabilities 的 identity payload
  做内容哈希。**加字段会让同一局面的 snapshot_id 变化**（内容哈希语义本就如此）。已核实无任何
  路径跨版本比较 snapshot_id：同回合图新度检查（`_graph_is_current`）比较的是同进程内的
  snapshot_id+turn；replay 从已记录事件重建视图、不重算 snapshot_id；影子对比
  （`compare_shadow_projection`）新旧投影吃同一份内存 snapshot，对称。→ **预期内的良性漂移**，
  在实现 PR 描述里注明即可。
- **replay 确定性**：graph.delta 的 state_hash 只覆盖节点/边/观察标志，不含 capabilities；
  旧行为回放测试不受影响（现有 fixtures 不含 `climate` 键，新代码读键用 `capabilities.climate`
  只在构建侧发生）。
- **`_bool_mapping`**：仅用于 `ActionIntent.hard_constraints`（`models.py:469`），与
  RulesetCapabilities 无关；capabilities 的严格校验走 `__post_init__` 逐字段 `_strict_bool`。
  结论：新字段与 `_bool_mapping` 校验**无交互**。
- **构造兼容**：全仓 10+ 处 `RulesetCapabilities(...)` 关键字构造（含
  `RulesetCapabilities(resource_stockpiles=True)`）默认 False 兜底，零改动。

### 明确不做

- **不把气候采集加进 `get_governance_snapshot` 的 11 连环**（README 审查 V3 的教训：采集序列
  只许缩短）。气候是按需查询工具，治理快照零改动。
- 不做 `TurnSnapshot.climate` 字段；不建气候图实体（无消费者）。department 若未来要用，
  走 `get_climate_overview` + Observation 显式补查。

## ⑧ 测试计划

全部离线（组合层），与现有测试一一对应：

1. **规则集守卫**（`tests/test_standard_rules_compat.py` 追加，照
   `test_world_congress_builders_have_a_ruleset_guard` 抄）：
   `build_climate_overview_query()` 含 `ERR:NO_CLIMATE_IN_RULESET`、含
   `GameConfiguration.GetRuleSet`、含 `RULESET_EXPANSION_2`、不含 `"{_bail"`；
   `history_turns` clamp 生效（-5→1，9999→200）。
2. **Parser**（`tests/test_parsers.py` 追加，断言只增不减）：
   - 完整 fixture：CLIMATE/CLIMATE_RISK/CLIMATE_CO2×4/CLIMATE_CUR/CLIMATE_CITY×2/CLIMATE_EV×3
     → 字段逐项断言（含 `"3.0"` 浮点计数、share 计算、contributors 排序）。
   - 无当前事件、空历史（早局）→ `current_event is None`、空列表。
   - `revealed=0` 行 → x/y=-1、revealed=False。
   - 错误行 `ERR:NO_CLIMATE_IN_RULESET` → 由 `_raise_query_error` 抛 `LuaError`（GameState 层测）。
3. **能力位**（`tests/test_governance_core.py` 追加）：
   - `capabilities_for_ruleset("RULESET_EXPANSION_2").climate is True`；
     STANDARD/EXPANSION_1 为 False。
   - `"climate" in CAPABILITY_NAMES`；`capability_enabled("RULESET_STANDARD", "climate") is False`。
   - `RulesetCapabilities(climate=1)` 抛 `TypeError`（补 `_strict_bool` 元组项的证据）。
   - `_canonical(RulesetCapabilities.standard())` 含 `"climate": False`。
4. **MCP 注册**（`tests/test_governance_server.py::test_governance_mcp_tools_are_registered`
   只增断言）：`get_climate_overview` 在 `mcp.list_tools()` 中。
5. **Standard 拒绝路径（MCP 级）**：stub conn 返回 `["ERR:NO_CLIMATE_IN_RULESET", SENTINEL]`
   → `GameState.get_climate_overview` 抛 `LuaError`；经 `pipeline._logged` 包装后返回字符串
   `Error: ERR:NO_CLIMATE_IN_RULESET`（断言前缀，不锁死全文）。
6. **真机验收**（阶段二门禁之外的一次性手动验证，GS 对局）：`get_climate_overview` 输出与
   游戏内 World Climate 屏逐项对照（phase、CO₂ 总量/自产、风险百分比、本回合事件）；再开一局
   Standard 规则确认显式错误文案。

## ⑨ 风险与放弃判断

**结论：可达性成立，不放弃。** 剩余风险按影响排序：

1. **`GetEventsForTurn` 每回合单事件假设**（中）：官方 UI 按"每回合至多一条事件"消费；若引擎
   允许同回合多事件（如风暴+干旱同回合），本工具与官方屏一样会漏。已按官方行为对齐，标注为
   已知盲区；缓解：`FertilityAdded` 等聚合字段仍来自完整事件对象。
2. **字段缺省值歧义**（中低）：`kEvent.FertilityAdded` 可能为 nil（官方 UI 显式判 nil）。
   协议统一 `or 0`，语义损失：0 伤害与"字段缺失"不可区分。可接受（agent 决策只关心非零伤害）。
3. **phase 推导依赖 XML 行序**（低）：官方算法要求 SEA_LEVEL 行按 ClimateChangePoints 升序
   且 Index 连续（XML 注释自认 "must be in ascending order"）。社区 MOD 改 RandomEvents 表
   会破坏 `Index - firstIndex + 1`。缓解：narrate 只依赖 phase 数值本身；modded 对局非支持面。
4. **数值单位未文档化**（低）：`GetStormPercentChance` 等按 UI 用法当百分比直传；若实为
   千分比，narrate 文案首次真机验收时校正（测试 6 的对照步骤就是为此设的）。
5. **历史扫描成本**（低）：默认 30 回合 × `GetEventsForTurn`，均为引擎内存查询，官方 UI 扫全史
   无压力；clamp 上限 200 兜底。
6. **`Locale.Lookup` 依赖**（低）：事件名/文明名本地化在 InGame 上下文可用（ClimateScreen
   原样使用）；全部 pcall 包裹，失败回退类型名。
7. **核事故纳入历史的口径分歧**（记录在案）：官方屏排除，本工具保留——若后续要求"严格官方
   视角"，parser 一行过滤 `operator == "NUCLEAR_ACCIDENT"` 即可，协议不变。
