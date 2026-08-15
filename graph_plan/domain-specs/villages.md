# 部落村落（Goody Hut / 口语"遗迹"）查询域实现规格

> 域定义：村落 = 地块改良 `IMPROVEMENT_GOODY_HUT`，单位踏入即由引擎移除并结算随机奖励。
> 本文口语"遗迹"即指部落村落（沿袭前代文明的 ancient ruins 俗称），与文物遗址（archaeology site，非 improvement，由建造者发掘）无关，后者不在本规格内。
> 实现样板：`src/civ_mcp/lua/barbarians.py` 全链路（同为"全图扫描已揭示地块找特定 improvement"），逐段镜像。

核心语义纪律（贯穿全部九节）：

- 工具是无状态快照查询：只报告**当前引擎状态下仍存在**的已揭示村落。
- 未观察 ≠ 已删除：村落从结果中消失只说明"已被某方踏入取用"（取用者、时间、奖励均不可查询），工具不保留、不推测、不虚构取用历史。
- 奖励内容不可查询（§1 论证），任何实现不得伪造。

## ① 官方 Lua API 清单

来源安装目录：`~/Library/Application Support/Steam/steamapps/common/Sid Meier's Civilization VI/Civ6.app/Contents/Assets/`（下称 `$ASSETS`）。

扫描所需 API（全部为 GameCore 上下文对象方法）：

| API | 返回 | 用途 |
|---|---|---|
| `Game.GetLocalPlayer()` | int | 本地玩家 id |
| `PlayersVisibility[me]:IsRevealed(plotIndex)` / `:IsVisible(plotIndex)` | bool | 迷雾判定（已揭示 / 当前可见） |
| `Map.GetGridSize()` | width, height | 全图遍历边界 |
| `Map.GetPlot(x, y)` / `Map.GetPlotByIndex(i)` | table/nil | 取地块 |
| `plot:GetIndex()` | int | 迷雾判定所需索引 |
| `plot:GetImprovementType()` | int（-1 = 无） | 判定是否村落 |
| `plot:GetOwner()` | int（-1 = 无主） | 村落是否落在某文明领土内 |
| `GameInfo.Improvements[improvementIndex]` | row（含 `.ImprovementType` 字符串） | 索引 → 枚举名 |
| `Players[me]:GetCities():Members()` / `city:GetX(), city:GetY()` | 迭代器 | 距离基准：己方城市 |
| `Players[me]:GetUnits():Members()` / `unit:GetX(), unit:GetY(), unit:GetType()` | 迭代器 | 距离基准：己方战斗单位 |
| `GameInfo.Units[type].Combat` / `.RangedCombat` | number | 战斗单位过滤（`UNIT_SCOUT` Combat=10，侦察单位天然包含） |
| `Map.GetPlotDistance(x1, y1, x2, y2)` | int | 六角格距离 |
| `PlayerConfigurations[ownerId]:GetCivilizationShortDescription()` + `Locale.Lookup(...)` | string | 领土归属文明短名 |

关键数据文件（引擎权威定义）：

- `$ASSETS/Base/Assets/Gameplay/Data/Improvements.xml:18` — `IMPROVEMENT_GOODY_HUT` 枚举行；`Improvements.xml:47` — 属性行：`Goody="True" RemoveOnEntry="true" TilesPerGoody="128" GoodyRange="3"`。`RemoveOnEntry` 是"踏入即消失"的唯一权威依据。
- `$ASSETS/Base/Assets/Gameplay/Data/GoodyHuts.xml` — 奖励池：`GoodyHuts` 六大类 + `GoodyHutSubTypes` 子类型（带 `Weight/Turn/MinOneCity/RequiresUnit` 资格条件），奖励经 `Modifier` 系统（`MODIFIER_PLAYER_GRANT_*`）由 C++ 结算。**仅解释语义，扫描不读它。**
- `$ASSETS/DLC/Expansion2/Data/Expansion2_GoodyHuts.xml` — GS 追加 `GOODYHUT_DIPLOMACY` 类（总督称号/使者/外交支持）并调整既有子类型权重。仅改奖励池，不影响扫描 API。
- `$ASSETS/Base/Assets/Gameplay/Data/Notifications.xml:28/221` — `NOTIFICATION_DISCOVER_GOODY_HUT`（LOW 级、回合末过期）：发现村落时的通知。现有 `get_notifications` 解析器已按 `NOTIF|类型名|消息|回合|x,y` 透传该类型，无需新接线；唯一缺口是 `NOTIFICATION_TOOL_MAP` 无该键的 resolution hint（见落地清单可选项）。

游戏自身对该 API 的官方用法佐证：

- `$ASSETS/Base/Assets/UI/WorldBuilderPlacement.lua:1384` `PlaceGoodyHut`：用 `Map.GetPlotByIndex(plot):GetImprovementType()` 读、用 `WorldBuilder.MapManager():SetImprovementType(...)` 写 —— 读取路径与我们一致。
- `$ASSETS/Base/Assets/Maps/Utility/MapUtilities.lua:648+` `CanPlaceGoodyAt`：地图生成侧按 `improvement.Goody and not improvement.TilesPerGoody` 分发，与扫描侧判定逻辑同一套数据。

**奖励内容为何不可查询**：全安装目录 Lua 中引用 `GoodyHut` 的仅 WorldBuilder、地图工具、教程脚本与若干 UI 面板；不存在 `player:GetGoodyHuts()` 之类的运行时查询接口，奖励结算完全发生在 C++ GoodyHuts 系统（Modifier 管线），Lua 侧只能事后看到资源/单位/科技等结果。因此规格明确：奖励内容 out of scope，docstring 与 narrate 均须写明。

## ② FireTuner 可达性论证

执行模型（`src/civ_mcp/connection.py`、`tuner_client.py`）：`conn.execute_read(code)` 把代码以 `CMD:{gamecore_index}:{code}` 发到 `GameCore_Tuner` Lua 状态，收集 `print()` 输出直到 `---END---` 哨兵（`connection.py:174-181, 326`）。上表 API 在该状态可达的证据全部来自本仓库已上线、真机验证过的代码：

| API | 既有 GameCore 用例 |
|---|---|
| `Map.GetGridSize/GetPlot/GetPlotDistance`、`plot:GetIndex/GetImprovementType`、`GameInfo.Improvements`、`vis:IsRevealed/IsVisible`、单位/城市迭代与过滤 | `lua/barbarians.py:37-68`（蛮族营地扫描，README 记录真机验证） |
| `plot:GetOwner()` | `lua/map.py:549`（资源查询，经 `game_state.py:294` execute_read） |
| `PlayerConfigurations + Locale.Lookup` | `lua/units.py:281-282, 343-344`（威胁扫描，同样走 execute_read/GameCore） |

明确**不可达或不采用**的部分（"游戏内 UI 脚本 API" vs "FireTuner 可达"的区分，以后者为准）：

- `LuaEvents.*`、`UI.PlaySound`、WorldBuilder 面板局部表（如 `m_GoodyHutTypeEntries`）：仅 UI 上下文，FireTuner 的 `GameCore_Tuner` 状态没有这些全局。
- `WorldBuilder.MapManager():SetImprovementType`：写接口，且正常对局中 WorldBuilder API 不可用；本工具纯读，不涉及。
- 事件订阅（如 improvement 变更事件在 UI 脚本里的监听）：FireTuner CMD 是一次性命令执行，本仓库整体是轮询快照模型；注册常驻回调与单连接纪律冲突，明确排除。
- 本查询不含任何 `UnitManager.RequestOperation` / `CityManager` / `DiplomacyManager` 调用 —— 纯读，走 `execute_read`，哨兵必须（默认 `require_sentinel=True`）。

## ③ Lua 查询片段

新建 `src/civ_mcp/lua/villages.py`，逐段镜像 `barbarians.py`（同一 `nearestDistance` 双基准、同一迷雾门控、同一哨兵收尾；唯一新增是 owner 字段解析）：

```python
"""Tribal village (goody hut) queries — one-shot rewards on revealed tiles."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import Village, VillageOverview


def build_village_overview_query() -> str:
    """GameCore: scan revealed tribal villages.

    A village is a one-shot reward removed the moment any unit enters its
    tile, so this reports current presence only — absence is never a claim
    about history. Distances target the nearest own city and own combat unit
    (UNIT_SCOUT has Combat 10, so scouts count) for grab-race prioritization.
    """

    return """
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local cityPositions = {}
local militaryPositions = {}

for _, city in Players[me]:GetCities():Members() do
    table.insert(cityPositions, {city:GetX(), city:GetY()})
end

for _, unit in Players[me]:GetUnits():Members() do
    local ux, uy = unit:GetX(), unit:GetY()
    local entry = GameInfo.Units[unit:GetType()]
    if ux ~= -9999 and entry and ((entry.Combat or 0) > 0 or (entry.RangedCombat or 0) > 0) then
        table.insert(militaryPositions, {ux, uy})
    end
end

local function nearestDistance(positions, x, y)
    local nearest = 999
    for _, pos in ipairs(positions) do
        local distance = Map.GetPlotDistance(pos[1], pos[2], x, y)
        if distance < nearest then nearest = distance end
    end
    return nearest
end

local width, height = Map.GetGridSize()
for y = 0, height - 1 do
    for x = 0, width - 1 do
        local plot = Map.GetPlot(x, y)
        if plot then
            local plotIndex = plot:GetIndex()
            if vis:IsRevealed(plotIndex) then
                local improvementIndex = plot:GetImprovementType()
                local improvement = improvementIndex >= 0 and GameInfo.Improvements[improvementIndex] or nil
                if improvement and improvement.ImprovementType == "IMPROVEMENT_GOODY_HUT" then
                    local visibility = vis:IsVisible(plotIndex) and "visible" or "revealed"
                    local ownerLabel = "none"
                    local ownerId = plot:GetOwner()
                    if ownerId and ownerId >= 0 and PlayerConfigurations[ownerId] then
                        local cfgName = Locale.Lookup(PlayerConfigurations[ownerId]:GetCivilizationShortDescription())
                        if cfgName then ownerLabel = tostring(cfgName):gsub("|", "/") end
                    end
                    print(
                        "VILLAGE|" .. x .. "," .. y .. "|" .. visibility
                        .. "|" .. ownerLabel
                        .. "|" .. nearestDistance(cityPositions, x, y)
                        .. "|" .. nearestDistance(militaryPositions, x, y)
                    )
                end
            end
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_village_overview_response(lines: list[str]) -> VillageOverview:
    """Parse ``VILLAGE`` records from Lua."""

    huts: list[Village] = []
    for line in lines:
        parts = line.split("|")
        try:
            if line.startswith("VILLAGE|") and len(parts) >= 6:
                x, y = (int(value) for value in parts[1].split(","))
                huts.append(
                    Village(
                        x=x,
                        y=y,
                        visibility=parts[2] or "revealed",
                        owner=parts[3] or "none",
                        distance_to_city=int(parts[4]),
                        distance_to_military=int(parts[5]),
                    )
                )
        except (IndexError, TypeError, ValueError):
            # A malformed line should not discard valid records from the same
            # scan; FireTuner output can contain unrelated diagnostic lines.
            continue
    return VillageOverview(huts=huts)
```

记录格式：`VILLAGE|x,y|visible-or-revealed|owner|distCity|distMilitary`。owner 为 `none` 或文明短名（管道字符已 `gsub` 成 `/`，与 `notifications.py` 消息消毒同一手法）。解析器对坏行整行跳过（同蛮族解析器语义）。

## ④ dataclass

`src/civ_mcp/lua/models.py`，插在 `BarbarianOverview`（约 696 行）之后：

```python
@dataclass
class Village:
    """A tribal village (goody hut) on a tile the player has revealed."""

    x: int
    y: int
    visibility: str = "revealed"  # "visible" or "revealed"
    owner: str = "none"  # civ short name, or "none" when unowned
    distance_to_city: int = 999
    distance_to_military: int = 999


@dataclass
class VillageOverview:
    """Revealed tribal villages for the current turn.

    A village is removed the instant any unit enters its tile, so this only
    reflects current presence; absence is not a claim about history.
    """

    huts: list[Village] = field(default_factory=list)
```

`lua/__init__.py` 两处导出：builder/parser 加在 barbarians 导入块（约 21-25 行）之后；`Village`、`VillageOverview` 加在 models 再导出块（约 152-154 行）。

`game_state.py` 在 `get_barbarian_overview`（261-265 行）后追加：

```python
async def get_village_overview(self) -> lq.VillageOverview:
    """已揭示的部落村落（一次性奖励，取用即消失）。"""
    lines = await self.conn.execute_read(lq.build_village_overview_query())
    return lq.parse_village_overview_response(lines)
```

## ⑤ narrate 样式

`narrate.py` 在 `narrate_barbarian_overview`（约 347 行结束）后追加。结构镜像蛮族版本（排序 → 逐条带优先级 → 收尾指导），两处强制差异：排序键改为"到最近战斗/侦察单位"（村落是抢夺竞速而非威胁响应）；迷雾语义句是必选项而非可选装饰。语言按 AGENTS.md 返回语言规则用中文（见 §9 风险 5）：

```python
def narrate_village_overview(
    overview: lq.VillageOverview, *, compact: bool = False
) -> str:
    """把已揭示的部落村落格式化为抢先取用队列。

    村落是入场即消失的一次性奖励, 按到最近己方战斗/侦察单位的距离排序。
    结果只反映当前仍存在的村落; 已被取用的村落不出现在这里, 工具也不
    保留或虚构取用历史。
    """

    lines = ["=== 村落总览 ==="]
    huts = sorted(
        overview.huts,
        key=lambda hut: (hut.distance_to_military, hut.distance_to_city),
    )
    if not huts:
        lines.append("当前没有任何已揭示地块上存在部落村落。")
        lines.append("迷雾说明: 空结果只表示没有可确认的村落, 不代表地图上没有村落。")
        return "\n".join(lines)

    lines.append(f"已揭示村落 ({len(huts)}):")
    for hut in huts:
        if hut.distance_to_military <= 5:
            priority = "速取"
        elif hut.distance_to_military <= 10:
            priority = "可达"
        else:
            priority = "远端"
        owner = hut.owner if hut.owner != "none" else "无主"
        unit_dist = (
            f"距最近战斗/侦察单位 {hut.distance_to_military}"
            if hut.distance_to_military < 999
            else "无己方战斗/侦察单位"
        )
        city_dist = (
            f"距最近城市 {hut.distance_to_city}"
            if hut.distance_to_city < 999
            else "无城市"
        )
        lines.append(
            f"  [{priority}] ({hut.x},{hut.y}) [{hut.visibility}] — "
            f"{unit_dist}; {city_dist}; {owner}"
        )
    lines.append("")
    lines.append(
        "任一单位踏入村落即取用并使其消失; 奖励内容在取用后由游戏结算, 本查询不可见。"
    )
    lines.append(
        "已揭示地块上的村落从结果中消失, 只说明它已被某方踏入取用, 取用者与时间不可确认。"
        "侦察单位顺路取用优先; 村落会被其他文明抢走, 但不要为远端村落偏离战略路线。"
    )
    return "\n".join(lines)
```

## ⑥ MCP 工具定义

`src/civ_mcp/server/tools/queries.py`，紧跟 `get_barbarian_overview`（约 218 行）之后，完整镜像其形态（`readOnlyHint`、tiles 回传给 `_logged` 供空间注意力记录）：

```python
@mcp.tool(annotations={"readOnlyHint": True})
async def get_village_overview(ctx: Context) -> str:
    """查询已揭示的部落村落（一次性奖励）及取用优先级。

    只返回当前仍存在的村落: 任一单位踏入村落即取用并使其消失, 已取用
    村落不出现在结果中, 本工具不保留取用历史。每条结果包含坐标、当前
    可见状态、所在领土归属, 以及到最近己方城市和最近己方战斗/侦察单位
    的距离, 用于抢先取用决策。奖励内容在取用后由游戏结算, 不可查询。
    """
    gs = pipeline._get_game(ctx)
    village_tiles: set[tuple[int, int]] = set()

    async def _run():
        overview = await gs.get_village_overview()
        village_tiles.update((hut.x, hut.y) for hut in overview.huts)
        return nr.narrate_village_overview(overview)

    return await pipeline._logged(
        ctx,
        "get_village_overview",
        {},
        _run,
        tiles=village_tiles,
    )
```

工具名 `get_village_overview`（对外 `mcp__civ6__get_village_overview`），保持 `mcp__civ6__*` 兼容命名族。docstring 中文依据 AGENTS.md 返回语言规则；工具名/枚举值/坐标保持原文。

**明确不做**：不加入 `get_game_overview` 每回合固定块。理由：村落不是威胁（蛮族营地是，故其 compact 块有守家语义），而治理快照采集序列已是 11+ 次 FireTuner 串行往返（README 对抗审查 V3），每回合再串一条固定查询会放大延迟；村落属于探索期按需查询，由文档（agent-turn-loop/agent-strategy）引导调用时机。`compact` 参数保留签名占位但不接任何调用方，待将来真要并入每回合入口时再实现截断逻辑。

## ⑦ ruleset 门控论证

**结论：无需 ruleset 门控，也不做 capability 探测。**

1. `IMPROVEMENT_GOODY_HUT` 定义在 `$ASSETS/Base/Assets/Gameplay/Data/Improvements.xml`（Base 层），任何 ruleset（Standard / RF / GS）都装载 Base 数据，该枚举行在所有模式下存在。
2. `_helpers._lua_require_ruleset` 的设计场景是"表行存在但机制被模式关闭"造成的假阳性（Governor、世界议会类）。村落不存在关闭开关：GameOptions 没有禁用村落的选项，扫描到即真实存在。
3. 扫描本身就是能力探测：自定义 mod 若移除该行，`GameInfo.Improvements[...]` 匹配不到，自然返回空结果，不会报错也不会产生误导（空结果的迷雾说明句已覆盖此况）。
4. 扩展包差异只在奖励池（§1），不影响扫描可见性。真正会需要 ruleset 判断的是"奖励内容/概率查询"（GS 独有 `GOODYHUT_DIPLOMACY` 类），v1 明确不做奖励查询，故无门控需求。

## ⑧ 测试计划

新建 `tests/test_village_overview.py`，镜像 `tests/test_barbarian_overview.py` 的三段式（builder 断言 / parser 固定行 / narrate 断言）。本域无 end_turn 联动，不涉及 `_barbarian_attack_opportunities` 类交叉测试：

```python
"""Tests for the tribal village (goody hut) query."""

from civ_mcp import narrate
from civ_mcp.lua.villages import (
    build_village_overview_query,
    parse_village_overview_response,
)


def test_village_query_scans_revealed_huts() -> None:
    query = build_village_overview_query()

    assert 'IMPROVEMENT_GOODY_HUT' in query
    assert 'vis:IsRevealed' in query
    assert 'vis:IsVisible' in query
    assert 'VILLAGE|' in query
    assert 'print("---END---")' in query
    assert 'RequestOperation' not in query  # 纯读查询, 不得混入任何变异调用


def test_parse_village_overview() -> None:
    overview = parse_village_overview_response(
        [
            'VILLAGE|12,24|revealed|none|6|3',
            'VILLAGE|40,10|visible|France|12|9',
            '---END---',
        ]
    )

    assert len(overview.huts) == 2
    assert overview.huts[0].x == 12
    assert overview.huts[0].owner == "none"
    assert overview.huts[0].distance_to_city == 6
    assert overview.huts[0].distance_to_military == 3
    assert overview.huts[1].visibility == "visible"
    assert overview.huts[1].owner == "France"


def test_parse_village_overview_skips_malformed_lines() -> None:
    overview = parse_village_overview_response(
        [
            'VILLAGE|not-a-coordinate|revealed|none|6|3',
            'VILLAGE|5,5|visible|none|4|1',
        ]
    )

    assert len(overview.huts) == 1
    assert overview.huts[0].x == 5


def test_narrate_village_overview_empty_states_fog_semantics() -> None:
    overview = parse_village_overview_response([])

    text = narrate.narrate_village_overview(overview)

    assert "迷雾说明" in text


def test_narrate_village_overview_lists_priority_and_ownership() -> None:
    overview = parse_village_overview_response(['VILLAGE|12,24|revealed|none|6|3'])

    text = narrate.narrate_village_overview(overview)

    assert '[速取]' in text
    assert '(12,24)' in text
    assert '无主' in text
    assert '消失' in text  # 取用即消失 + 不保留历史的语义句必须存在
```

配套核验：

- 全量 `uv run pytest tests/ -q --ignore=tests/test_scorer.py` 须全绿。工具注册测试 `test_governance_mcp_tools_are_registered` 用 `issubset` 断言，新增工具不破坏。
- `result_filter` 无需注册：`_FILTERED_TOOLS` 只压缩治理类大结果，村落输出为小结果。
- 真机验收（阶段二真机场景内完成，非本规格交付物）：见 §9 风险 1 的验证实验。

## ⑨ 风险

1. **迷雾下取用的可见性模型未验证（最高优先）**。`plot:GetImprovementType()` 在 GameCore 返回的是引擎当前状态还是"本地玩家最后已知状态"，决定了一件事：AI 在你**非当前可见**的已揭示地块上取用村落后，本扫描是立刻看不到该村落（引擎真值）还是继续显示直到你重新观察（按玩家记忆）。蛮族营地扫描（同一 API、同一模型）已真机验证过"离开视野后营地仍在结果中"，但"视野外**变化**是否实时反映"这一情形无真机证据，蛮族注释也未断言。**缓解**：narrate 已按最弱假设措辞（"从结果中消失只说明已被某方踏入取用"），不声称任何一方模型为真。**真机验证实验（并入阶段二验收）**：记录一个村落坐标 → 移走视野 → 等待 AI 侦察兵取用 → 重扫 → 对比两种行为并在 devlog 记录结论；若证明是引擎真值模型且团队认为属信息泄漏，再评估是否加 `vis:IsVisible` 门控（注意那会牺牲"已揭示但暂不可见"村落的可用性，需要单独裁决）。
2. **每查询一次全图双层循环**。与蛮族扫描同成本（巨型图约 7k 地块 × 廉价操作，5s 默认超时内），先例已上线；但村落工具若被高频轮询会放大 FireTuner 串行队列压力。缓解：文档引导探索期按需调用；不在每回合固定序列中（§6 已明确）。
3. **奖励不可查询是硬边界**。docstring 与 narrate 双处声明，防止 agent 期待回报奖励并脑补。若未来要补"我方取用及其奖励"信号，正确入口是治理事件/Notification 侧，不是本扫描。
4. **`Locale.Lookup` / `PlayerConfigurations` 在 GameCore_Tuner 的可用性**。有威胁扫描先例（`units.py:281-282`）背书，风险低。Lua 侧对 `ownerId < 0`、`PlayerConfigurations[ownerId]` 为 nil、`cfgName` 为 nil 三种情况都落到默认值，owner 字段在任何分支下都有确定值（`none` 或名字），解析器不会因空字段崩坏。若真机出现文明短名含管道字符，`gsub("|","/")` 已消毒。
5. **语言风格不一致**。AGENTS.md 返回语言规则要求 MCP 面向调用方的语义信息用中文，而 `narrate.py` 存量（含最近的 great_people/espionage 域）全部为英文。本规格按 AGENTS.md 执行（规则是现行仓库指令且 belief.py 新代码已是中文先例），代价是 narrate.py 内部中英并存。若团队裁决统一英文，应先修订 AGENTS.md 再实施，规格中 narrate 文案同步翻译即可，结构不变。
6. **概念混淆面**。"遗迹"在中文社区同时指 goody hut（本域）与考古文物遗址（不同机制、非 improvement）。文档（docs/agent-tools.md 新条目）须点名"部落村落（遗迹）= goody hut"，避免实施者或 agent 误扩域。

## 落地清单（按序，均为新代码，不改存量行为）

1. 新建 `src/civ_mcp/lua/villages.py`（§3 全文）。
2. `src/civ_mcp/lua/models.py`：`Village` / `VillageOverview`（§4）。
3. `src/civ_mcp/lua/__init__.py`：builder/parser 与 model 两处再导出。
4. `src/civ_mcp/game_state.py`：`get_village_overview`（§4）。
5. `src/civ_mcp/narrate.py`：`narrate_village_overview`（§5）。
6. `src/civ_mcp/server/tools/queries.py`：`get_village_overview` 工具（§6）。
7. 新建 `tests/test_village_overview.py`（§8）。
8. 可选一行：`src/civ_mcp/lua/notifications.py` 的 `NOTIFICATION_TOOL_MAP` 增加 `"NOTIFICATION_DISCOVER_GOODY_HUT": "get_village_overview(...)"` 类提示（含键名测试）。
9. 文档：`docs/agent-tools.md` 增条目（含"遗迹=部落村落"消歧，风险 6）。
10. 按仓库 Git 规则提交：`feat(queries): 新增部落村落（goody hut）查询工具`。

明确禁止：不修改 `deepseek-harness/`；不改蛮族域任何现有代码；不接入每回合治理快照序列。
