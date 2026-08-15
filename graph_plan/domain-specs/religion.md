# 域规格：世界宗教状态查询（get_religion_overview）

> 状态：规格，未实现。本文自包含：实现者不需要读本目录其他文档即可开工。
> 遵循 graph_plan/README.md §6「影子投影 → 单消费者切换」的边界纪律：本工具是纯查询（Query 消息），不引入新实体/关系到图，不触碰治理链路。

## 0. 定位与既有工具边界

仓库已有的宗教相关能力（新增工具不得重复）：

| 既有工具 | 覆盖 | 位置 |
|---|---|---|
| `get_pantheon_beliefs` | 己方万神殿状态 + 可选万神殿信条列表 | `server/tools/actions.py:163` |
| `get_religion_beliefs` | 创教状态 + 可选宗教 + 各类可选信条 | `server/tools/actions.py:198` |
| `choose_pantheon` / `found_religion` / `spread_religion` | 动作 | `actions.py:179/215`、`actions.py:718` |
| `get_religion_spread` | **逐城**主流宗教与信徒明细（IsRevealed 可见性门控） | `server/tools/world.py:449` |
| `get_game_overview` | `REL|`（已创宗教归属，met 门控）、`RELSLOTS|`（名额）、己方信仰值 | `lua/overview.py:119-134` |
| `get_victory_progress` | 每文明 `religion_majority`、`religion_cities`、`has_religion`（胜利视角） | `lua/victory.py:216-227` |

**空白**（本工具要填）：世界级聚合一次性回答——哪些宗教已被创立、创立者是谁、圣城在哪、每个宗教的信条构成与万神殿信条、**各宗教的信徒城市数与信徒总数**、各主要文明的己创宗教/主流宗教/万神殿对照、己方信仰值存量与创教名额余量。目前这些信息分散在 4 个工具里且没有"宗教→信徒城市数"聚合。

明确的非目标：不列可选信条（get_pantheon_beliefs / get_religion_beliefs 所有）；不做逐城明细（get_religion_spread 所有）；不做胜利逼近警告（get_victory_progress 所有，narrate 里只交叉引用）；不做宗教压力/城市内百分比明细。

## ① Lua API 清单（权威来源标注）

三类对象。来源缩写：`RS` = 游戏 `Base/Assets/UI/ReligionScreen.lua`（官方宗教总览界面脚本，最权威参考）；`CS` = `Base/Assets/UI/CitySupport.lua`；`repo` = 本仓库已在真实游戏验证过的用法（标注文件:行）。

**Game 宗教服务 `Game.GetReligion()`**（InGame 上下文，`RS:72`）

| 方法 | 语义 | 来源 |
|---|---|---|
| `:GetReligions()` | 返回记录数组，字段 `{Religion=索引, Pantheon=布尔, Founder=玩家ID, Beliefs=信条索引数组}`；含万神殿伪记录（`Pantheon==true`）与未创立宗教 | `RS:129/184`、`CS:498` |
| `:HasBeenFounded(idx)` | 该宗教是否已被创立 | `RS:189` |
| `:GetName(idx)` | 返回**本地化 key**，需再 `Locale.Lookup()` | `RS:1020` |
| `:GetMinimumFaithNextPantheon()` | 下一个万神殿所需最低信仰（本工具不用，列出备查） | `RS:367` |

**玩家宗教 `Players[i]:GetReligion()`**

| 方法 | 语义 | 来源 |
|---|---|---|
| `:GetReligionTypeCreated()` | 己创宗教索引，-1 = 未创 | repo `overview.py:119`（真机） |
| `:GetReligionInMajorityOfCities()` | 多数城市主流宗教索引 | repo `religion.py:369`（真机） |
| `:GetPantheon()` | 万神殿信条索引，-1 = 无 | repo `religion.py:22`（真机） |
| `:GetPantheonCost()` | 下一个万神殿成本（官方无直接暴露，repo 已 pcall 验证可达） | repo `religion.py:28` |
| `:CanCreatePantheon()` | 是否还能选万神殿 | `RS:121` |
| `:GetHolyCityID()` | 圣城复合 ID | `RS:795/1002` |
| `:GetFaithBalance()` / `:GetFaithYield()` | 信仰存量/每回合 | repo `cities.py:589`、`overview.py:842-844`（真机，Yield 需 pcall） |

**城市宗教 `city:GetReligion()`**：`:GetMajorityReligion()`（repo `religion.py:337` 真机）、`:GetReligionsInCity()` → `{{Religion=索引, Followers=数}}`（repo `religion.py:345` 真机；`RS:1074` 同）。

**解析辅助**：`GameInfo.Religions[idx]`（`ReligionType`/`Name`）、`GameInfo.Beliefs[idx]`（`BeliefType`/`BeliefClassType`/`Name`）；`CityManager.GetCity(id)` 单参复合形式（`RS:795`）与 `GetCity(owner, cityID)` 双参形式（`ProductionHelper.lua:178`）并存；repo 的 `city_id % 65536` 解码（`lua/_helpers.py:99-105`）证明复合 ID 低位 16bit 为城市 ID。

## ② 可达性论证

1. **上下文选择：InGame（`execute_write`）**。`ReligionScreen.lua` 本身是 InGame UI 脚本，全套 API 在该上下文原生可用；`Locale.Lookup` 只在 InGame 存在（GameCore 无 Locale），而名称输出必须本地化。兄弟查询 `build_religion_status_query` 走 `execute_write` 且作为产品工具 `get_religion_spread` 已在真实游戏可用（`game_state.py:1422-1424`）。不要改成 `execute_read`（GameCore）——Locale 会 nil 调用。
2. **Players 全遍历模式已被 diplomacy 查询证明**：`lua/diplomacy.py:38-63` 的 `for i = 0, 62` + `IsAlive` + `IsMajor` + `HasMet` + `pcall` 防御循环每回合在真实游戏执行；本工具的 PSTATE 行采用完全相同的循环骨架。
3. **全玩家城市遍历（不做迷雾门控）是官方行为**：`RS:1071-1086` 的 ViewReligion 城市 panel 遍历 `PlayerManager.GetAlive()` 的所有城市统计信徒，不看可见性——宗教传播是公共信息（宗教镜头同理）。本工具的 RSPAN 聚合照此办理；与 `get_religion_spread` 的 IsRevealed 门控差异是**有意的**（后者按城展示、前者只出聚合计数）。
4. **未见面创始者的遮蔽**：官方对未 met 的 founder 显示 "Unmet"（`RS:998-1008`）。本工具同样以 `pDiplo:HasMet(rec.Founder)` 门控 founder 文明名与圣城名。
5. **单次往返**：一条 Lua 脚本输出全部行，不增加治理快照的串行往返数（README 审查 V3 的教训——本工具不进 `get_governance_snapshot` 采集序列，仅按需调用）。

## ③ Lua 片段（build_religion_overview_query）

加入 `src/civ_mcp/lua/religion.py`。遵循 repo 惯例：`SENTINEL` 结尾、管道分隔、`gsub("|","/")` 清洗本地化文本、pcall 防御可选 API、行前缀避开现有 `REL|/RELSLOTS|/RCITY|/RSUMMARY|`。

```lua
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
local gRel = Game.GetReligion()
local myRel = Players[me]:GetReligion()

-- 已创宗教记录（过滤万神殿伪记录与未创立项）
local recs = {}
for _, rec in ipairs(gRel:GetReligions()) do
    if rec.Pantheon == false and gRel:HasBeenFounded(rec.Religion) then
        table.insert(recs, rec)
    end
end
local nFounded = #recs
local nMajors = 0
for i = 0, 62 do
    if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then nMajors = nMajors + 1 end
end
local maxRel = math.floor(nMajors / 2) + 1

local function relTypeOf(idx)
    local row = GameInfo.Religions[idx]
    if row and row.ReligionType then return row.ReligionType end
    return "RELIGION_" .. idx
end
local function relNameOf(idx)
    local ok, key = pcall(function() return gRel:GetName(idx) end)
    if ok and key then return Locale.Lookup(key):gsub("|", "/") end
    local row = GameInfo.Religions[idx]
    if row then return Locale.Lookup(row.Name):gsub("|", "/") end
    return "Unknown"
end

-- SELF|pid|faith|faithPT|createdType|majorityType|panType|panCost|nFounded|maxRel
local faith = myRel:GetFaithBalance()
local faithPT = 0
pcall(function() faithPT = myRel:GetFaithYield() end)
local createdIdx = myRel:GetReligionTypeCreated()
local createdType = createdIdx >= 0 and relTypeOf(createdIdx) or "None"
local okMM, myMaj = pcall(function() return myRel:GetReligionInMajorityOfCities() end)
local myMajType = (okMM and myMaj and myMaj >= 0) and relTypeOf(myMaj) or "None"
local panIdx = myRel:GetPantheon()
local panType = "None"
if panIdx >= 0 then
    local b = GameInfo.Beliefs[panIdx]
    if b then panType = b.BeliefType end
end
local panCost = -1
if panIdx < 0 then
    local okC, c = pcall(function() return myRel:GetPantheonCost() end)
    if okC and c then panCost = c end
end
print("SELF|" .. me .. "|" .. string.format("%.1f", faith) .. "|" .. string.format("%.1f", faithPT)
    .. "|" .. createdType .. "|" .. myMajType .. "|" .. panType .. "|" .. panCost
    .. "|" .. nFounded .. "|" .. maxRel)

-- WREL|idx|type|name|founderPid|founderCiv|holyCity|pantheonType|belief;list
for _, rec in ipairs(recs) do
    local founderName, holyName = "Unmet", "unknown"
    if rec.Founder == me or pDiplo:HasMet(rec.Founder) then
        local fcfg = PlayerConfigurations[rec.Founder]
        if fcfg then
            founderName = Locale.Lookup(fcfg:GetCivilizationShortDescription()):gsub("|", "/")
        end
        local okH, hId = pcall(function() return Players[rec.Founder]:GetReligion():GetHolyCityID() end)
        if okH and hId and hId >= 0 then
            local okC, hc = pcall(function() return CityManager.GetCity(hId) end)
            if not (okC and hc) then
                okC, hc = pcall(function()
                    return CityManager.GetCity(math.floor(hId / 65536), hId % 65536)
                end)
            end
            if okC and hc then holyName = Locale.Lookup(hc:GetName()):gsub("|", "/") end
        end
    end
    local wPanType = "None"
    local okP, fp = pcall(function() return Players[rec.Founder]:GetReligion():GetPantheon() end)
    if okP and fp and fp >= 0 then
        local b = GameInfo.Beliefs[fp]
        if b then wPanType = b.BeliefType end
    end
    local beliefList = {}
    for _, bIdx in ipairs(rec.Beliefs) do
        local bRow = GameInfo.Beliefs[bIdx]
        if bRow then table.insert(beliefList, bRow.BeliefType) end
    end
    print("WREL|" .. rec.Religion .. "|" .. relTypeOf(rec.Religion) .. "|" .. relNameOf(rec.Religion)
        .. "|" .. rec.Founder .. "|" .. founderName .. "|" .. holyName .. "|" .. wPanType
        .. "|" .. table.concat(beliefList, ";"))
end

-- RSPAN|type|dominantCities|totalFollowers（全存活玩家城市聚合，官方 ReligionScreen 同口径）
local foundedIdx = {}
for _, rec in ipairs(recs) do foundedIdx[rec.Religion] = true end
local domCities, followers = {}, {}
for i = 0, 62 do
    local p = Players[i]
    if p and p:IsAlive() then
        for _, c in p:GetCities():Members() do
            local cr = c:GetReligion()
            local okM, maj = pcall(function() return cr:GetMajorityReligion() end)
            if okM and maj and foundedIdx[maj] then
                domCities[maj] = (domCities[maj] or 0) + 1
            end
            local okR, rels = pcall(function() return cr:GetReligionsInCity() end)
            if okR and rels then
                for _, rd in ipairs(rels) do
                    if foundedIdx[rd.Religion] then
                        followers[rd.Religion] = (followers[rd.Religion] or 0) + rd.Followers
                    end
                end
            end
        end
    end
end
for idx in pairs(foundedIdx) do
    print("RSPAN|" .. relTypeOf(idx) .. "|" .. (domCities[idx] or 0) .. "|" .. (followers[idx] or 0))
end

-- PSTATE|pid|civ|createdType|createdName|majorityType|pantheonType（met 门控）
for i = 0, 62 do
    local p = Players[i]
    if p and p:IsMajor() and p:IsAlive() and (i == me or pDiplo:HasMet(i)) then
        local cfg = PlayerConfigurations[i]
        local civName = Locale.Lookup(cfg:GetCivilizationShortDescription()):gsub("|", "/")
        local pr = p:GetReligion()
        local cIdx = pr:GetReligionTypeCreated()
        local cType, cName = "None", "None"
        if cIdx >= 0 then
            cType = relTypeOf(cIdx)
            cName = relNameOf(cIdx)
        end
        local okM2, maj2 = pcall(function() return pr:GetReligionInMajorityOfCities() end)
        local mType = (okM2 and maj2 and maj2 >= 0) and relTypeOf(maj2) or "None"
        local pIdx2 = pr:GetPantheon()
        local pType = "None"
        if pIdx2 >= 0 then
            local b = GameInfo.Beliefs[pIdx2]
            if b then pType = b.BeliefType end
        end
        print("PSTATE|" .. i .. "|" .. civName .. "|" .. cType .. "|" .. cName .. "|" .. mType .. "|" .. pType)
    end
end
print("{SENTINEL}")
```

Python 侧 builder 返回 f-string（无插值参数，单引号字符串即可），`SENTINEL` 替换同现有文件。解析器 `parse_religion_overview_response(lines)` 按前缀分派，畸形行跳过（对齐 `parse_religion_status_response` 的防御风格）；`-1`/`None` 归一化为 `None`/`-1`。

## ④ dataclass（lua/models.py 新增）

```python
@dataclass
class WorldReligionEntry:
    religion_index: int
    religion_type: str                     # e.g. RELIGION_CATHOLICISM
    name: str                              # localized display name
    founder_player_id: int
    founder_civ_name: str                  # "Unmet" for unmet founders
    holy_city_name: str | None             # None if founder unmet / lookup failed
    pantheon_belief_type: str | None       # founder's pantheon belief (official screen shows it per religion)
    belief_types: list[str]                # full composition incl. pantheon, follower/founder/enhancer/worship


@dataclass
class PlayerReligionState:
    player_id: int
    civ_name: str
    founded_religion_type: str | None
    founded_religion_name: str | None
    majority_religion_type: str | None     # None = no majority
    pantheon_belief_type: str | None


@dataclass
class ReligionOverview:
    # SELF|
    player_id: int
    faith_balance: float
    faith_per_turn: float
    my_created_religion_type: str | None
    my_majority_religion_type: str | None
    my_pantheon_belief_type: str | None
    pantheon_cost: float                   # -1 = pantheon already chosen
    religions_founded: int
    religions_max: int
    # WREL| / RSPAN| / PSTATE|
    religions: list[WorldReligionEntry] = field(default_factory=list)
    followers: list[tuple[str, int, int]] = field(default_factory=list)  # (type, dominant_cities, total_followers)
    players: list[PlayerReligionState] = field(default_factory=list)
```

`followers` 单独成表而不是塞进 `WorldReligionEntry`，因为 RSPAN 的键是 religion_type 而 WREL 的主键是 index——两行在解析层合并不必要（narrate 层按 type 对齐）。

## ⑤ narrate 样式（narrate.py 新增）

`narrate_religion_overview(ro: lq.ReligionOverview) -> str`，纯函数，风格对齐 `narrate_religion_status`（紧凑英文列表 + 交叉引用；narrate 语言的全仓现状是英文，本工具跟随现状，见风险 R6）：

```text
Religion Overview:
Faith: 412.0 (+18.0/turn) | Pantheon: BELIEF_RELIGIOUS_IDOLS | Religion: none
  (2/6 slots used — Great Prophet needed) | Pantheon cost if chosen now: 145
Founded Religions:
  1. Catholicism (France, holy city Paris) — pantheon BELIEF_GOD_KING — 24 dominant cities, 310 followers
     beliefs: BELIEF_TITHE; BELIEF_PILGRIMAGE; BELIEF_WORLD_CHURCH; BELIEF_CATHEDRALS
  2. Islam (Unmet, holy city unknown) — 11 dominant cities, 122 followers
Players:
  France: founded Catholicism | majority Catholicism | pantheon BELIEF_GOD_KING
  Egypt: no religion | majority Catholicism | pantheon BELIEF_RIVER_GODDESS
Per-city breakdown: get_religion_spread | Available beliefs: get_religion_beliefs
```

规则：无任何宗教创立时输出早期提示（含下一个万神殿成本与名额），其余段省略；胜利逼近判断不在此工具（引用 `get_victory_progress`）；`religions_max - religions_founded == 0` 时提示名额已满（不可创教）。

## ⑥ MCP 工具定义（server/tools/world.py，紧邻 get_religion_spread）

```python
@mcp.tool(annotations={"readOnlyHint": True})
async def get_religion_overview(ctx: Context) -> str:
    """World religion state: founded religions with founder, holy city, belief
    composition; follower city counts and follower totals per religion; each
    major civ's founded/majority religion and pantheon; your faith balance and
    founding slot usage.

    Aggregate view. Per-city breakdown: get_religion_spread. Belief choices:
    get_pantheon_beliefs / get_religion_beliefs. Call when deciding pantheon
    timing, religion races, or missionary targets.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        ro = await gs.get_religion_overview()
        return nr.narrate_religion_overview(ro)

    return await pipeline._logged(ctx, "get_religion_overview", {}, _run)
```

`game_state.py` 在 `get_religion_status`（1422 行）旁加：

```python
async def get_religion_overview(self) -> lq.ReligionOverview:
    lines = await self.conn.execute_write(lq.build_religion_overview_query())  # InGame: Locale.Lookup
    return lq.parse_religion_overview_response(lines)
```

变更清单：`lua/religion.py`（builder+parser）→ `lua/models.py`（3 dataclass）→ `lua/__init__.py`（re-export）→ `narrate.py` → `game_state.py` → `server/tools/world.py` → 新测试文件。工具数 104 → 105（CLAUDE.md 大图节同步）。不进入 `get_governance_snapshot` 采集序列。

## ⑦ ruleset 门控论证

- **宗教是全规则集共有能力**：`capabilities.py:29` 把 `"religion": True` 放进 `_COMMON_CAPABILITIES`，STANDARD/R&F/GS 三档同为 True（`RulesetCapabilities` dataclass 默认继承，`governance/models.py:190`）。因此本查询**不加** `_lua_require_ruleset` 门控——这与 governor/world congress（STANDARD 下 Lua 表存在但运行时不可用，见 `_helpers.py:42-59` 的注释）不同。测试里反向断言这一点（见 ⑨），把"不需要门控"固化为契约，防止未来有人误加或误删。
- **favor 门控是参考样板而非本域模板**：`narrate_overview` 用 `ov.ruleset == "RULESET_EXPANSION_2"` 收缩 Favor 行（`narrate.py:33-35`），snapshot 用 `capabilities.diplomatic_favor` 门控 metric（`snapshot.py:389-391`）。宗教的对应门控位是 `capabilities.religion`——今天恒 True，未来接入 snapshot 投影时仍应读它而不是裸读字段，保持"ruleset 身份而非 API 存在性决定能力"的原则（`capabilities.py` 模块注释）。
- **唯一会变的是胜利开关**：`VICTORY_RELIGIOUS` 可在游戏设置中禁用（`_LUA_VICTORY_ENABLED` helper，`_helpers.py:198-206`）。本工具不输出胜利判断，故无需探测；若未来 narrate 加胜利提示，必须走 `enabled_victories` 而非假设默认全开。
- **无宗教创立刻的字段空值语义**（所有规则集通用）：
  - `religions=[]`、`religions_founded=0`：开局/名额未消耗。
  - `my_created_religion_type=None`、`my_majority_religion_type=None`：未创教且无主流（城市全无信徒压力时 `GetReligionInMajorityOfCities` 返回 -1）。
  - `my_pantheon_belief_type=None`、`pantheon_cost>=25`：未选万神殿；`pantheon_cost=-1` 表示已选（成本不再有意义）。
  - `holy_city_name=None`：创始者未见面（官方同口径遮蔽）或圣城查找失败（圣城被夷平/复合 ID 解析失败——见 R1）。
  - 死亡玩家从 PSTATE 消失（`IsAlive` 过滤），但其已创宗教仍留在 WREL/RSPAN——宗教在创始人灭亡后存续，这是游戏事实不是数据错误。

## ⑧ 与信念引擎的衔接建议（本期不实现）

供后续 snapshot/graph 投影直接取用的命名（对齐现有 `diplomacy.player_{N}.X`、`resource.{key}.X` 惯例，`snapshot.py:525/554`）：

- **TurnSnapshot 扩展**：`governance/models.py` 的 `TurnSnapshot` 增加可选段 `religion: ReligionOverview | None = None`（模式同 `barbarians: BarbarianOverview | None`，`models.py:236`）；类型校验加入 `_typed_or_none` 清单。
- **能力门控**：投影时读 `capabilities.religion`（恒 True，但保持模式统一）；False 时整段跳过，不产生半空 metric。
- **metrics（数值）**：
  - `religion.founded_count`、`religion.slots_max`（对齐已有 `player.religions_founded/max` 但归 religion 域，二选一，避免双写——建议沿用 `player.religions_*` 并只新增 religion 域新键）。
  - 每宗教：`religion.{religion_type小写}.dominant_cities`、`religion.{...}.followers`。
  - 每玩家：`religion.player_{N}.dominant_cities`（其己创宗教的主流城市数，未创教为 0）、`religion.player_{N}.has_religion`（0/1）、`religion.player_{N}.has_pantheon`（0/1）。
  - 己方信仰不新增（`player.faith` 已存在，`snapshot.py:374`）。
- **实体与关系（第二实体示例，遵循 README §3「先证明最小闭环」）**：`religion:{ReligionType}` 节点（attributes: name、founder_player_id、holy_city_name、belief_types）；关系 `founded`（player→religion）、`majority_religion`（player→religion）。城市主流宗教做成 City 节点属性而非每城一条边，避免节点数×宗教数的边膨胀。节点 ID 构造函数收敛进 `graph/project.py` 单点导出（README 审查 V5 的教训：`city_node_id()` 先例）。
- **覆盖语义声明**：全玩家城市聚合（RSPAN）按官方界面属公共事实 → `COMPLETE`；未见面创始者的身份 → 不写入（不是 unknown 写入，而是省略该 attribute），避免"未观察 ≠ 已删除"边界被宗教域破坏。

## ⑨ 测试计划（全部离线，新文件 tests/test_religion_overview.py，风格对齐 test_barbarian_overview.py）

1. **builder 内容断言**：输出含 `Game.GetReligion()`、`HasBeenFounded`、`GetHolyCityID`、`HasMet`、`print("---END---")`；**不含**任何变异调用（`UI.RequestPlayerOperation`、`UnitManager.RequestOperation`、`RequestCommand`）——固化只读契约。
2. **门控反向断言**：输出不含 `GameConfiguration.GetRuleSet` bail（与 test_standard_rules_compat 对 governor 的正向断言互为镜像，记录"宗教是共有能力，不设 ruleset 门"的决策）。
3. **parser 正常路径**：SELF/WREL/RSPAN/PSTATE 样例行 → 字段全对齐；`-1` → `pantheon_cost` 原样、`None` 字符串 → `None`；belief 列表按 `;` 拆分。
4. **parser 边界**：无任何宗教（仅 SELF 行 → 空列表 + 默认值）；"Unmet"/"unknown" 保留字面值；畸形行（缺列/非整数）跳过不抛；自定义宗教（`CUSTOM` 类型名）正常入表。
5. **narrate**：空局文本含万神殿成本与名额提示；有宗教时每宗教一行含 founder/圣城/信条数；末行交叉引用 `get_religion_spread`/`get_religion_beliefs`。
6. **注册**：`server.tools.world` 模块存在 `get_religion_overview` 且带 `readOnlyHint` 注解（导入副作用注册，无既有工具清单冻结测试，保持轻量）。
7. **真实游戏验收**（graph_plan §8 第三层，本规格不代做）：FireTuner handshake 后调用工具，SELF 信仰值与 `get_game_overview` 一致；WREL 行与游戏内宗教总览界面（Religion Screen）逐项核对 founder/圣城/信条数。

## ⑩ 风险

- **R1（中）圣城复合 ID 无官方文档**：`RS:795` 用单参 `CityManager.GetCity(holyCityID)`，repo 用 `city_id % 65536` 双参形式；规格已做单参→双参 floor/mod 回退 + 全程 pcall，失败时 `holy_city_name=None`。真机验收必须覆盖"圣城被夷平后 GetHolyCityID 仍返回旧 ID"的情形（CityManager 解析失败回 None 是正确降级）。
- **R2（中）`GetReligions()` 记录含噪声**：万神殿伪记录（`Pantheon==true`）与未创立宗教混在同一数组；漏掉 `HasBeenFounded` 过滤会把未创宗教当已创。测试 4 固化。
- **R3（低）超官方 UI 的信息暴露**：PSTATE 暴露 met 对手的万神殿信条（官方界面只经"其已创宗教"间接可见）。仓库先例是 rival snapshot 直接读对手信仰值（`overview.py:677`），本工具不再暴露对手信仰值（已在 `get_rankings`）；PSTATE 保留对手万神殿是"聚合对照表"的价值所在，标注为有意的 Lua 层可见性。若评审不认可，删 PSTATE 的 pantheon 列即可，wire 格式不变。
- **R4（低）上下文误用**：`Locale.Lookup` 仅 InGame；若有人把 `get_religion_overview` 改成 `execute_read`（GameCore）会 nil 报错。注释与测试 1 双重固化。
- **R5（低）与前缀惯例的耦合**：行前缀（SELF/WREL/RSPAN/PSTATE）在单脚本内无冲突风险，但与 overview 的 REL|/RELSLOTS| 语义相近；命名已错开，parser 按前缀严格分派。
- **R6（低）narrate 语言**：CLAUDE.md 返回语言规则要求面向模型的可读信息用中文，但 narrate.py 全仓现状（含最新 barbarian overview）是英文。本规格跟随现状以免单工具孤例；若启动全仓 narrate 中文化，本函数一并迁移。
- **R7（低）输出体积**：宗教数 ≤ ⌈玩家数/2⌉+1，PSTATE ≤ 存活大文明数，行数天然有界（<40 行），远低于 result_filter 阈值；逐城明细刻意留在 `get_religion_spread`。

## 附：实现顺序建议

lua builder/parser + models → tests 1-5 → narrate + test → game_state + world.py 工具 + test 6 → 文档（agent-tools.md 宗教节、CLAUDE.md 工具数）→ 真实游戏验收（测试 7）。全程不动治理链路与 graph 包。
