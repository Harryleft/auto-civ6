# 时代进度查询域规格:`get_era_progress`

> 状态:规格(未实施)。调研日期 2026-08-15,基于本机安装的完整版 Civilization VI(Steam 289070,含全部 DLC)与仓库当前 `main`。
> 自包含:实施者不需要重走本调研。

## 0. 概念界定(本规格的唯一术语基础)

| 概念 | 定义 | 机制归属 |
|---|---|---|
| **Era(纪元)** | 远古/古典/中世纪/文艺复兴/工业/现代/原子/信息(+GS 未来)的科技时代序列 | **所有规则集的基础机制**。每个玩家有自己的 Era;游戏有一个世界 Era(多数玩家推进后整体推进) |
| **Age(时代)** | 黑暗/普通/黄金/英雄时代,由上一纪元的时代分(Era Score)与门槛决定 | **Rise and Fall(资料片 1)机制**。资料片 2 延续。对应 `governance/capabilities.py` 的 `ages` 能力位(`RULESET_STANDARD=False`,XP1/XP2=True) |

游戏自己的数据也这样分层:`Base/Assets/Gameplay/Data/GameCapabilities.xml` 中 `CAPABILITY_ERAS`(纪元)与 `CAPABILITY_GOLDEN_AND_DARK_AGES`、`CAPABILITY_HISTORIC_MOMENTS`(时代分)是三个独立能力,后两者 `DependsOnCapability="CAPABILITY_ERAS"`。

本工具定位:**纪元进度是基础查询,所有规则集必须可用**;时代/时代分子块按 `ages` 能力位门控。仓库已有 `get_dedications`(`lua/governance.py` `build_dedications_query`,硬错误门控)覆盖"选择时代献礼";本工具覆盖"当前处于什么纪元、每个文明推进到哪、(XP1+)时代分进展"。

---

## 1. Lua API 清单(标注来源)

以下所有路径相对
`~/Library/Application Support/Steam/steamapps/common/Sid Meier's Civilization VI/Civ6.app/Contents/Assets/`
(下称 `$GAME`)。符号表 = `$GAME/DLC/Expansion{1,2}/Binaries/Win64/GameCore_XP{1,2}_FinalRelease.map`(二进制导出符号,即 C++ 暴露给 Lua 的真实方法表;两份符号表方法集完全一致)。

### 1.1 基础层(所有规则集)

| API | 返回 | 证据来源 |
|---|---|---|
| `Players[i]:GetEra()` | 0-based 纪元索引(int) | 符号表 `lGetEra@IPlayer@Lua@GameCore`;官方 UI `$GAME/Base/Assets/UI/ActionPanel.lua:353`(`displayEra = pPlayer:GetEra() + 1; -- Engine is 0 Based`)、`PortraitSupport.lua:35`、`ARXManager.lua:414` |
| `GameInfo.Eras` 行 | `EraType` / `Name`(LOC 键)/ `ChronologyIndex`(1-based) | `$GAME/Base/Assets/Gameplay/Data/Eras.xml`(8 行,`ERA_ANCIENT`..`ERA_INFORMATION`,ChronologyIndex 1–8);`ERA_FUTURE`(ChronologyIndex=9)仅 GS 添加,`$GAME/DLC/Expansion2/Data/Expansion2_Eras.xml:7` |
| `Players[i]:IsMajor()` / `IsAlive()` / `PlayerConfigurations[i]:GetCivilizationShortDescription()` | — | 仓库现有所有 builder 的既有用法(如 `lua/overview.py:97`) |
| `GameConfiguration.GetValue("RULESET")` (+ `GetRuleSet()` pcall) | 规则集字符串 | `lua/_helpers.py` `_lua_require_ruleset`、`lua/overview.py:24-27` 同款读取 |

### 1.2 世界纪元与 XP1+ 时钟层(`Game.GetEras()` 对象,`IGameEras`)

| API | 语义 | 证据来源 |
|---|---|---|
| `Game.GetEras()` | GameEras 对象;**无 R&F 安装时为 nil** | `$GAME/Base/Assets/UI/PortraitSupport.lua:26` Firaxis 自己的注释:"Era system changed between BASE and XP1",`if Game.GetEras ~= nil then ... end`,nil 时回落到 `Players[i]:GetEra()` |
| `:GetCurrentEra()` | 当前世界纪元索引 | 同上 `PortraitSupport.lua:28`;`EraProgressPanel.lua:62` |
| `:GetFinalEra()` | 最终纪元索引(判"已到最后纪元") | `EraProgressPanel.lua:122` |
| `:GetCurrentEraStartTurn()` | 当前世界纪元开始的回合 | `$GAME/DLC/Expansion1/UI/Additions/EraCompletePopup.lua:46` |
| `:GetNextEraCountdown()` | 纪元倒计时剩余回合;未开始时返回 -1(UI 里 `+1` 后 `>0` 判断) | `EraProgressPanel.lua:100`、`ActionPanel_Expansion1.lua:104`(注释:"0 turns remaining is the last turn")。倒计时长度 `GlobalParameters.NEXT_ERA_TURN_COUNTDOWN=10`(`$GAME/DLC/Expansion1/Data/Expansion1_GlobalParameters.xml:122`) |
| `:GetCurrentEraMinimumEndTurn()` / `:GetCurrentEraMaximumEndTurn()` | 当前纪元最早/最晚结束回合号 | `EraProgressPanel.lua:107-111`;数据来自 `Eras_XP1` 表 `GameEraMinimumTurns/MaximumTurns=40/60`(`$GAME/DLC/Expansion2/Data/Expansion1_Eras.xml`)——**XP1 数据表,Standard 规则集无此机制** |
| `:GetCurrentEraNumPlayersMoreAdvanced()` / `:GetCurrentEraNumPlayersAsOrLessAdvanced()` | 已进入下一纪元 / 未进入的玩家数 | 符号表 `lGetCurrentEraNumPlayersMoreAdvanced` / `lGetCurrentEraNumPlayersAsOrLessAdvanced`(官方 UI 无调用点,仅二进制暴露——低置信,必须 pcall) |

### 1.3 时代分/Age 层(XP1+,全部按 `ages` 门控)

符号表 `IGameEras` 完整方法集(两份 .map 一致)中与 Age 相关的:

| API | 语义 | 证据来源 |
|---|---|---|
| `:GetPlayerCurrentScore(pid)` | 本纪元当前时代分 | `EraProgressPanel.lua:65`;仓库 `lua/overview.py:142`、`lua/governance.py:614` 已真机使用 |
| `:GetPlayerDarkAgeThreshold(pid)` / `:GetPlayerGoldenAgeThreshold(pid)` | 黑暗/黄金门槛 | `EraProgressPanel.lua:113-115`;仓库同上已用 |
| `:GetPlayerThresholdBaseline(pid)` | 门槛基线(上纪元得分,门槛=基线+加成) | `EraSupport.lua:5`、`ActionPanel_Expansion1.lua`(XP2 替换版 `:118` 附近 baseline 变量) |
| `:GetPlayerPreviousScore(pid)` | 上一纪元得分 | 符号表;`EraProgressPanel.lua:75` 经 breakdown 使用 |
| `:GetPlayerCurrentEraScoreBreakdown(pid)` | `list[{来源文本: 分数}]`,单项为单键表 | `EraProgressPanel.lua:86-98`(`for sourceString, sourceValue in pairs(source)`) |
| `:GetPlayerPreviousEraScoreBreakdown(pid)` | 同上,上一纪元 | `EraProgressPanel.lua:70` |
| `:HasDarkAge(pid)` / `:HasGoldenAge(pid)` / `:HasHeroicGoldenAge(pid)` | 当前 Age 判定 | `EraProgressPanel.lua:139-169`(优先级 Heroic > Golden > Dark > Normal);仓库唯一已用正确拼写的先例是 `lua/governance.py:607`(`HasHeroicGoldenAge`)。注意:`lua/overview.py:870`(rival snapshot)用的是错误拼写 `HasHeroicAge(i)`——pcall 吞错后英雄时代在 diary 数据中恒显示 NORMAL,这本身就是既有隐患,不是可参考先例 |

### 1.4 已排除的 API(调研结论,避免实施者踩坑)

- **`Players[i]:GetEras()` 不是本工具路径**。符号表存在 `IPlayerEras`(`Lua_IPlayerEras`,方法仅 `GetEra`/`SetEra`/`SetStartingEra`),但 Firaxis 全部 UI 脚本零调用(全 `$GAME` 范围 grep `:GetEras()` 排除 `Game.GetEras` 无结果),它是 WorldBuilder/开局设置用途。任务简报中的 `Players[i]:GetEras():GetCurrentEra()` **不存在**(IPlayerEras 没有 `GetCurrentEra`)。每玩家纪元一律用 `Players[i]:GetEra()`。
- **`HasCapability("CAPABILITY_...")` 不用于 tuner 查询**。它是 UI 上下文全局函数(`$GAME/Base/Assets/UI/Scripts/GameCapabilities.lua:6`,实现为查 `GameInfo.GameCapabilities[c]`);且仓库 `lua/_helpers.py` `_lua_require_ruleset` 的 docstring 已确立:Standard 规则集下 DB 行探测会**假阳性**,权威开关是 `GameConfiguration.GetValue("RULESET")`。
- `ChangePlayerEraScore`、`GetPlayerNumAllowedCommemorations`、`GetPlayerCommemorateChoices`、`GetPlayerActiveCommemorations` 属写入/献礼域,归 `get_dedications`,不进本工具。

## 2. 可达性论证(FireTuner)

1. **通道选择:InGame 上下文(`conn.execute_write`)。** 仓库所有纪元相关查询都在此通道真机验证过:`lua/overview.py` ERA| 行(`game_state.py:113`)、`lua/governance.py` dedications(`game_state.py:1291`)、diary 的 per-player era/age(`game_state.py:203`)。注意仓库命名陷阱:`execute_read/write` 指 Lua 上下文而非读写语义;本工具为纯读,仍走 `execute_write` + `_raise_query_error`,与 `get_dedications` 完全同构。
2. **`Game.GetEras()` 在 InGame tuner 上下文可达**:overview builder 的 ERA| 块(`lua/overview.py:137-148`)与 dedications builder 均已在真实游戏执行(graph_plan README 记录真机验证)。GameCore 上下文同样可达(`$GAME/DLC/Expansion2/Scripts/WorldCongress.lua:255`),但无需用它。
3. **`Players[i]:GetEra()` 可达性**:与已验证的 `Players[i]:GetScore()`、`GetTreasury()` 等同属 `IPlayer` 同一方法表(符号表 `lGetEra@IPlayer@...` 与仓库已用方法同源),同一对象、同一上下文。风险仅剩"从未在本仓库真机执行过一次",列入 §9 风险 1 与 §8 真机验收项。
4. **本机安装(全 DLC)下 Standard 规则集**:`Game.GetEras()` 非 nil(该函数只依赖 R&F 安装而非规则集,`PortraitSupport` 的 nil 守卫针对"无资料片安装")。但 Standard 下 XP1 时钟与时代分语义不成立,故 §3 的 Lua 把这些读取整体放在 `activeRuleset ~= "RULESET_STANDARD"` 分支内——与 `lua/overview.py:137` 的既有组合守卫(`规则集 ~= STANDARD and Game.GetEras ~= nil`)完全一致。
5. **无 GameEras 的退化路径**(理论上仅"无资料片安装"会出现):每玩家纪元(`Players[i]:GetEra()` + `GameInfo.Eras`)完全不依赖 GameEras,基础层照常返回;世界纪元字段置 None。本机不可测试,见 §9 风险 8。

## 3. Lua 片段(`src/civ_mcp/lua/eras.py` 新文件)

按仓库 builder 惯例:管道分隔行、行首标签、`Locale.Lookup` 后 `gsub("|","/")` 清洗、`{SENTINEL}` 收尾(`lua/_helpers.py`)。输出协议:

```text
RULESET|RULESET_EXPANSION_2                       # 必有
GAMEERA|<idx>|<EraType>|<本地化名>|<isFinal>       # Game.GetEras() 非 nil 时(本机恒有)
CLOCK|<startTurn>|<countdown>|<minEnd>|<maxEnd>|<moreAdv>|<asOrLess>   # 仅 XP1+ 规则集
PERA|<pid>|<civ>|<eraIdx>|<EraType>|<名>|<age>|<score>   # 每个存活主要文明;Standard 下 age/score 为 "-"
AGE|<score>|<dark>|<golden>|<baseline>|<prevScore>       # 仅 XP1+ 规则集,本地玩家
AGEDETAIL|<来源文本>|<分数>                               # 仅 XP1+ 规则集,本地玩家,多行
---END---
```

```python
def build_era_progress_query() -> str:
    """Read world era, per-player eras, and (XP1+) era score progress."""
    return """
local id = Game.GetLocalPlayer()
local activeRuleset = GameConfiguration.GetValue("RULESET")
if GameConfiguration.GetRuleSet ~= nil then
    pcall(function() activeRuleset = GameConfiguration.GetRuleSet() end)
end
print("RULESET|" .. tostring(activeRuleset or "UNKNOWN"))
local xp1 = activeRuleset ~= nil and activeRuleset ~= "RULESET_STANDARD"

-- Era sequence for this game (GameInfo.Eras, all rulesets)
local eras = {}
for row in GameInfo.Eras() do table.insert(eras, {Index=row.Index, Type=row.EraType, Chron=row.ChronologyIndex or 99, Name=Locale.Lookup(row.Name):gsub("|","/")}) end
table.sort(eras, function(a,b) return a.Chron < b.Chron end)
for _, e in ipairs(eras) do print("ERAS|idx" .. e.Index .. "|" .. e.Type .. "|" .. e.Name) end

-- World era (needs Game.GetEras; nil only on installs without Rise and Fall)
local pGameEras = nil
pcall(function() if Game.GetEras ~= nil then pGameEras = Game.GetEras() end end)
local finalEra = false
if pGameEras ~= nil then
    local cur = pGameEras:GetCurrentEra()
    local entry = GameInfo.Eras[cur]
    finalEra = (cur == pGameEras:GetFinalEra())
    print("GAMEERA|" .. cur .. "|" .. (entry and entry.EraType or "UNKNOWN")
      .. "|" .. (entry and Locale.Lookup(entry.Name):gsub("|","/") or "Unknown")
      .. "|" .. tostring(finalEra))
end

-- XP1+ era clock (ruleset-gated; countdown/min/max are Eras_XP1 mechanics)
if xp1 and pGameEras ~= nil then
    local st, cd, mn, mx, ma, ml = -1, -1, -1, -1, -1, -1
    pcall(function() st = pGameEras:GetCurrentEraStartTurn() end)
    pcall(function() cd = pGameEras:GetNextEraCountdown() end)
    pcall(function() mn = pGameEras:GetCurrentEraMinimumEndTurn() end)
    pcall(function() mx = pGameEras:GetCurrentEraMaximumEndTurn() end)
    pcall(function() ma = pGameEras:GetCurrentEraNumPlayersMoreAdvanced() end)
    pcall(function() ml = pGameEras:GetCurrentEraNumPlayersAsOrLessAdvanced() end)
    print("CLOCK|" .. st .. "|" .. cd .. "|" .. mn .. "|" .. mx .. "|" .. ma .. "|" .. ml)
end

-- Per-player chronological era (all rulesets) + age/score (XP1+ only)
for i = 0, 62 do
    local p = Players[i]
    if p and p:IsMajor() and p:IsAlive() then
        local cfg = PlayerConfigurations[i]
        local civName = Locale.Lookup(cfg:GetCivilizationShortDescription()):gsub("|","/")
        local eraIdx = -1
        pcall(function() eraIdx = p:GetEra() end)
        local entry = GameInfo.Eras[eraIdx]
        local eraType = (eraIdx >= 0 and entry) and entry.EraType or "UNKNOWN"
        local eraName = (eraIdx >= 0 and entry) and Locale.Lookup(entry.Name):gsub("|","/") or "Unknown"
        local age, score = "-", -1
        if xp1 and pGameEras ~= nil then
            pcall(function()
                if pGameEras:HasHeroicGoldenAge(i) then age = "Heroic"
                elseif pGameEras:HasGoldenAge(i) then age = "Golden"
                elseif pGameEras:HasDarkAge(i) then age = "Dark"
                else age = "Normal" end
                score = pGameEras:GetPlayerCurrentScore(i)
            end)
        end
        print("PERA|" .. i .. "|" .. civName .. "|" .. eraIdx .. "|" .. eraType
          .. "|" .. eraName .. "|" .. age .. "|" .. score)
    end
end

-- Local player age progress detail (XP1+ only)
if xp1 and pGameEras ~= nil then
    local score, dark, golden, baseline, prev = 0, 0, 0, 0, 0
    pcall(function() score = pGameEras:GetPlayerCurrentScore(id) end)
    pcall(function() dark = pGameEras:GetPlayerDarkAgeThreshold(id) end)
    pcall(function() golden = pGameEras:GetPlayerGoldenAgeThreshold(id) end)
    pcall(function() baseline = pGameEras:GetPlayerThresholdBaseline(id) end)
    pcall(function() prev = pGameEras:GetPlayerPreviousScore(id) end)
    print("AGE|" .. score .. "|" .. dark .. "|" .. golden .. "|" .. baseline .. "|" .. prev)
    pcall(function()
        local bd = pGameEras:GetPlayerCurrentEraScoreBreakdown(id)
        if bd then
            for _, source in ipairs(bd) do
                for sourceString, sourceValue in pairs(source) do
                    if sourceValue and sourceValue ~= 0 then
                        print("AGEDETAIL|" .. tostring(sourceString):gsub("[|,]","/") .. "|" .. sourceValue)
                    end
                end
            end
        end
    end)
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)
```

解析器 `parse_era_progress_response(lines)` 与 builder 同文件,规则与 `parse_overview_response` 一致:必选行缺失→字段 None(基础层 GAMEERA/PERA 缺失不算错误,因为存在"无 GameEras 安装"与空行集合法情形;`RULESET|` 缺失 → `ValueError`);格式错行跳过;`age == "-"` / `score < 0` → None;`-1` 时钟哨兵 → None。

## 4. dataclass(`src/civ_mcp/lua/models.py` 新增)

```python
@dataclass
class EraTypeRow:
    """One chronological era in this game's database."""
    era_index: int          # GameInfo.Eras[Index]
    era_type: str           # "ERA_CLASSICAL"
    era_name: str           # localized
    chronology_index: int   # 1-based; 8 rows Standard, 9 with Gathering Storm


@dataclass
class EraProgressPlayer:
    player_id: int
    civ_name: str
    is_local: bool
    era_index: int            # Players[i]:GetEra(), 0-based, ALL RULESETS
    era_type: str
    era_name: str
    age: str | None = None        # AGES-GATED("Heroic"/"Golden"/"Dark"/"Normal")
    era_score: int | None = None  # AGES-GATED


@dataclass
class EraAgeDetail:
    """Local player's era-score progress. AGES-GATED as a whole."""
    era_score: int
    dark_threshold: int
    golden_threshold: int
    threshold_baseline: int
    previous_era_score: int
    score_breakdown: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class EraProgress:
    ruleset: str
    ages_supported: bool            # == capabilities.ages(仅 RULESET_STANDARD 为 False)
    current_era_index: int | None   # 世界纪元;None 仅当 Game.GetEras() 不可用(无 R&F 安装)
    current_era_type: str
    current_era_name: str
    final_era: bool
    era_sequence: list[EraTypeRow] = field(default_factory=list)
    # ---- 以下 6 个 XP1 时钟字段全部 AGES-GATED:capabilities.ages=False 时必须为 None ----
    era_start_turn: int | None = None
    next_era_countdown: int | None = None
    min_end_turn: int | None = None
    max_end_turn: int | None = None
    players_more_advanced: int | None = None
    players_as_or_less_advanced: int | None = None
    # ----
    players: list[EraProgressPlayer] = field(default_factory=list)
    local_age: EraAgeDetail | None = None  # AGES-GATED
```

`narrate_era_progress` 中的 Age 预测(下个纪元是 Dark/Normal/Golden)在 Python 侧由分数与门槛推导,不在 Lua 推导——与 `narrate.py:97-101` 对 `overview.era_score` 的既有推导同一逻辑、可离线测试。

## 5. narrate 样式(`src/civ_mcp/narrate.py` 新增 `narrate_era_progress`)

风格跟随 `narrate_overview` / `narrate_dedications`(英文、管道摘要;中文语义由 `presentation.localize_model_result` 管道层统一加,不在 narrate 内做)。样例输出:

```text
XP2:
World Era: Medieval (ERA_MEDIEVAL) | final era: no | started turn 45
Era clock: countdown 7 | window turns 85-105 | advanced 3, remaining 5
Era sequence: Ancient > Classical > Medieval > Renaissance > Industrial > Modern > Atomic > Information > Future (9 eras)
Players: Rome (you) Medieval [Normal] score 15 | Egypt Medieval [Normal] 9 | Kongo Classical [Dark] 2
Your age progress: 15 (Dark 12, Golden 24, baseline 0, previous era 5) -> NORMAL AGE projected
  Score sources: Built a wonder: 5, First to meet a city-state: 3, ...

Standard:
World Era: Classical (ERA_CLASSICAL) | final era: no | started turn 21
Era sequence: ... (8 eras)
Players: Rome (you) Classical | Egypt Classical | Kongo Ancient
Age mechanics: not available in RULESET_STANDARD (no era score, dark or golden ages)
```

## 6. MCP 工具定义(`src/civ_mcp/server/tools/queries.py`)

```python
@mcp.tool(annotations={"readOnlyHint": True})
async def get_era_progress(ctx: Context) -> str:
    """Get chronological era progress for the game and every major civilization.

    Always returns: current world era, the full era sequence of this game,
    and each major civ's current era (who is ahead or behind).
    Under Rise and Fall / Gathering Storm rules also returns the next-era
    countdown clock and your era score vs dark/golden thresholds, with the
    score source breakdown. Under Standard rules the age block is explicitly
    reported as unavailable instead of being silently omitted.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_era_progress()
        return nr.narrate_era_progress(status)

    return await pipeline._logged(ctx, "get_era_progress", {}, _run)
```

`game_state.py` 配套方法(放在 Dedications 段之后):

```python
async def get_era_progress(self) -> lq.EraProgress:
    lua = lq.build_era_progress_query()
    lines = await self.conn.execute_write(lua)
    _raise_query_error(lines)
    return lq.parse_era_progress_response(lines)
```

实施时同步:`docs/agent-tools.md` 纪元/献礼相关条目(现 101 行附近)加一行;`tests/test_governance_server.py::test_governance_mcp_tools_are_registered` 的 issubset 断言不受影响,但需在新测试中断言 `get_era_progress` 已注册。

## 7. 门控策略(与 `overview.era_score` 现行处理对齐)

仓库现有三种门控形态,本工具选第 1 种:

| 形态 | 现行范例 | 适用 | 本工具 |
|---|---|---|---|
| **软省略 + 显式标记** | `lua/overview.py:137-148`(Lua 侧 `activeRuleset ~= "RULESET_STANDARD" and Game.GetEras ~= nil` 才采集/打印 ERA| 行)+ `narrate.py:95`(`if ov.era_name:` 空串跳过) | 基础工具中的可选子块 | **采用**。区别:overview 是顺带字段、静默省略;本工具主题就是时代进度,省略必须**显式**——dataclass 带 `ages_supported: bool`,narrate 固定输出一行 "Age mechanics: not available ..." |
| 硬错误 | `get_dedications`:`_lua_require_ruleset(("RULESET_EXPANSION_1","RULESET_EXPANSION_2"), "ERR:NO_DEDICATIONS_IN_RULESET")` → `_raise_query_error` → LuaError(`lua/governance.py:596-644`) | 整个工具只服务可选机制 | 不采用(本工具基础层必须常开) |
| 服务端能力位 | `governance/capabilities.py` `capability_enabled(ruleset, "ages")`,治理路由/ActionIntent 校验使用 | 变更动作与治理推理 | 本工具是只读查询,Lua 侧字符串门控已足够;未来 derivation/治理消费者读取该查询结果时,必须以 `capabilities.ages` 为同一判据(两判定恒等:`ages == (ruleset != RULESET_STANDARD)`,XP1/XP2 均为 True) |

对齐要点(逐条可核对):Lua 规则集读取与守卫写法复制 `overview.py:24-27,137,146`;XP1 时钟与 AGE 块在同一 `xp1` 分支内,与 overview 对 era_score 的分支边界一致;Standard 下不打印任何 XP1 专属行(overview 同样不打印 ERA| 行);不引入新的门控机制、不探测 DB 能力行(`_helpers.py` docstring 确立的假阳性教训)。

## 8. 测试计划

全部离线(真实游戏验收另列),文件与既有模式对应:

**`tests/test_parsers.py` 新增 class TestParseEraProgress**
1. XP2 完整样本(ERAS×9 + GAMEERA + CLOCK + PERA×N + AGE + AGEDETAIL×2)→ 全字段正确,`ages_supported=True`。
2. **Standard 样本**:只有 RULESET/ERAS×8/GAMEERA/PERA(age/score 为 "-"/-1),无 CLOCK/AGE/AGEDETAIL 行 → `ages_supported=False`、6 个时钟字段与 `local_age`、每个玩家 `age`/`era_score` 全为 None,基础字段(era_sequence 长度 8、PERA era 字段)照常解析。
3. AGEDETAIL 来源文本含 "|"/"," 已被清洗;`-1` 哨兵 → None;格式错行跳过;缺 `RULESET|` 行 → ValueError。

**`tests/test_standard_rules_compat.py` 新增 test_era_progress_builder_gates_xp1_sections(静态契约,照 dedications 测试样式)**
4. `build_era_progress_query()` 含 `activeRuleset ~= "RULESET_STANDARD"`(XP1 块守卫)。
5. `Players[i]:GetEra()` / `GameInfo.Eras` 出现在守卫**之外**(基础层不得被规则集门控)。
6. **不包含** `ERR:NO_.*_IN_RULESET` 硬错误路径(与 dedications 的行为差异即契约);`{_bail` 不出现在字符串里。
7. `print("RULESET|"` 存在(与 overview/diplomacy 的 RULESET 回显契约一致)。

**narrate 与注册测试(新增 `tests/test_era_progress.py` 或并入 test_parsers/test_governance_server)**
8. narrate Standard 断言:含 "not available in RULESET_STANDARD";**不含** "Era Score"、"GOLDEN AGE"、"countdown"。
9. narrate XP2 断言:分数 ≥ golden → "-> GOLDEN AGE projected";score < dark → "!! N short of avoiding Dark Age"(复用 `narrate.py:97-101` 判定文案)。
10. `get_era_progress` 出现在 `mcp.list_tools()` 且带 `readOnlyHint`(对齐 `test_governance_mcp_tools_are_registered` 模式)。
11. 门控恒等性:parser 产物的 `ages_supported == capability_enabled(ruleset, "ages")`(对三种规则集逐一断言)。

**真实游戏验收(阶段二式,离线测试不能替代)**
12. XP2 存档:一次读取,PERA 行覆盖全部存活主要文明,`Players[0]:GetEra()` 与 overview ERA| 的纪元索引交叉一致。
13. Standard 规则集存档:读取成功,age 块显式 unavailable,无 ERR。

## 9. 风险

1. **`Players[i]:GetEra()` 未在本仓库真机执行过**(仅二进制符号 + 官方 UI 证据)。缓解:与已验证 IPlayer 方法同表;§8-12 列为首项真机断言。若真机异常,退路是 `Players[i]:GetEras():GetEra()`(符号表存在,同为每玩家纪元)。
2. **Standard 规则集下 `Game.GetEras()` 各方法返回值无官方语义保证**(Firaxis 只按"安装"而非"规则集"守卫;时钟/门槛数据表是 XP1 专属)。缓解:这些读取整体在规则集分支内且逐个 pcall;Standard 下绝不输出。
3. **`GetCurrentEraNumPlayersMoreAdvanced/AsOrLessAdvanced` 无官方 UI 调用点**(仅符号表暴露),语义按名字推断。缓解:pcall + None 容忍;narrate 中标注为参考值;若真机返回 -1 则解析为 None。
4. **戏剧时代游戏模式**:dark == golden 门槛,不存在普通时代;`narrate` 的 Normal/Golden 预测会失真(官方 UI 有专门 `CAPABILITY_DRAMATICAGES` 分支,`ActionPanel_Expansion1.lua`)。缓解:narrate 检测 `dark == golden` 时改为输出 "Dramatic Ages mode threshold detected"。
5. **AGEDETAIL 来源文本是本地化字符串**,含 "|"/","/编码差异。缓解:Lua 侧 gsub 清洗;解析失败跳过该行不致命。
6. **开局时代分为 0、breakdown 为空**:AGEDETAIL 零行合法,narrate 输出 "(no score sources yet)"。
7. **规则集字符串漂移**(未来 DLC/更新新增规则集):`normalize_ruleset` fail-closed 抛 `UnsupportedRulesetError`,Lua 门控用字符串比较保持同一行为;builder 必须回显 `RULESET|` 原始值供遥测排查。
8. **无资料片安装不可在本机测试**(Game.GetEras 为 nil → GAMEERA 缺省、current_era=None、narrate 回落用本地玩家纪元)。该路径只能留契约测试,不能留真机验收声明。
9. **与 diary/overview 的重复采集**:diary PLAYER 行已含 era/eraScore/age(全知);本工具按需查询,不并入每回合治理快照采集序列(避免 V3 采集序列再加长);两者数据同源同 API,无一致性风险。
