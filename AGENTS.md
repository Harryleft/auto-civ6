# Civ 6 MCP — Agent Reference

An MCP server connecting to a live Civilization VI game via FireTuner. You can read full game state and issue commands. All commands respect game rules.

**You only know what you explicitly query.** A human player passively absorbs the score ticker, religion lens, unit health bars — you have none of that. Information you don't ask for simply doesn't enter your world model. The patterns below exist to compensate for this.

`end_turn` now runs **empire warnings** automatically — alerts for loyalty crises, idle trade routes, gold deficits, resource caps, scoreboard position, and military imbalance. These compensate for the most common blind spots, but don't replace periodic deep checks (victory progress, religion spread, diplomacy).

## Coordinate System

**Hex grid: (X, Y) where higher Y = visually south.**
- Y increases → south (down). Y decreases → north (up).
- X increases → east. X decreases → west.
- Moving from (9,24) to (9,26) is **south**, not north.

## Game Start

Before your first turn:
1. Read your civ's unique abilities, units, and buildings — what is this civ designed to do?
2. Identify the tech/civic that unlocks your unique unit; plan a research path to reach it.
3. Form a working hypothesis for a victory path. Hold it loosely — geography and rivals will clarify things through the Classical era.

Early choices compound. Each decision shapes what's available 20, 40, 60 turns later. A scout reveals the map early; a defensive unit lets your settlers move safely; more cities mean more districts which mean more everything. Religious civs often benefit from Holy Site infrastructure before the Great Prophet pool fills. What you don't build early, you pay for later.

## Turn Loop

Each turn in order:
0. `python3 scripts/civ6_assist.py precheck` — blockers (production/research/policy/envoy/diplo/WC) + opportunities (sell surplus, improve resources, Eureka) + unit state. Fix blockers before anything else.
1. `get_game_overview` — turn, yields, research, score, era score, difficulty. If resuming after context compaction, call `get_diary` first.
1b. `get_belief_state` after a new session/context compaction; revise important beliefs, predictions, and plans when new evidence materially changes them.
2. `get_units` — positions, HP, moves, charges, nearby threats
3. `get_map_area` around cities/units — terrain, resources, enemy units
4. Move/action each unit
5. `get_cities` — queues, growth, pillaged districts
6. `get_district_advisor` if placing a new district
7. `set_city_production` / `set_research` if needed
8. Run **Strategic Checkpoints** if it's time
9. `review_belief_engine`; route any high-impact/irreversible unresolved choice with `route_belief_decision`.
10. `skip_remaining_units` then `end_turn` — verify no blockers remain first (an end_turn failure costs 5-10 min of AI turns)

## Belief Engine

The diary records what happened and what the agent said. The Belief Engine is
the mutable current world model. Successful `get_*` calls automatically create
fact-only observations; do not copy every query manually.

- `upsert_belief`: interpretation with probability, confidence, evidence,
  counter-evidence, and falsifiers.
- `upsert_hypothesis` + `rebalance_hypothesis_pool`: preserve competing
  explanations instead of locking onto the first story.
- `upsert_prediction`: falsifiable claim with a deadline and optional metric
  rule. Failed high-confidence predictions create Surprise records.
- `upsert_dynamic_plan`: 5/10/20-turn goal with assumptions, exit conditions,
  and a review turn.
- `review_belief_engine`: checks prediction deadlines, belief contradictions,
  and plan invalidation triggers.
- `route_belief_decision`: returns `fast`, `verify_then_fast`, or `slow` from
  uncertainty, consequence, urgency, irreversibility, and active surprises.
- `get_combat_estimate` + `assess_route_combat_risk`: a nearby hostile only
  triggers verification; revise route safety only from quantified CS, HP,
  modifiers, and expected damage.
- `update_belief_entity` / `delete_belief_entity`: correct or remove current
  state without erasing the audit trace.
- `get_belief_trace` / `get_belief_metrics`: postmortem and calibration data.

Probability is the event likelihood; confidence is confidence in that estimate.
Do not use precise-looking percentages as a substitute for evidence. Link
beliefs to Observation IDs, state what would falsify them, and update promptly
when reality conflicts with the current story.

## Diary

The diary is your persistent memory across sessions. When context compacts or you return to a game, `get_diary` is how you reconstruct where you were and why you made the decisions you did. Entries with specific details — unit names, coordinates, yield numbers, reasoning — are far more useful to your future self than brief summaries.

Reflections are recorded **before** AI processing begins — write what YOU observed and did this turn. Anything that surfaces after `end_turn` (a diplomacy proposal, AI units entering your territory, events in the turn result) belongs in the **next** turn's diary, not this one.

Five reflection fields each turn (all required, non-empty):
- **tactical**: What happened — specific units, tiles, outcomes.
- **strategic**: Standings vs rivals — yields, city count, victory path viability with numbers.
- **tooling**: Tool issues observed, or "No issues".
- **planning**: Concrete actions for the next 5-10 turns — specific builds, moves, research targets with turn estimates.
- **hypothesis**: Specific predictions — attack timing, milestone turns, biggest risks.

## Strategic Checkpoints

Periodic checks worth doing regularly. The game doesn't surface most of this proactively.

### Around every 10 turns:
- `get_empire_resources` — unimproved luxuries and nearby strategics
- Surplus luxuries: duplicates beyond 1 copy provide zero amenity benefit. Trade them via `propose_trade` for GPT, strategic resources, or luxury types you don't own (each new type = +1 amenity to 4 cities). Even 5 GPT per surplus luxury adds up over 30 turns. Use `mode="test"` to check what the AI will accept before sending.
- Gold/faith balance: if either is accumulating with no plan, spend it — `purchase_item`, `purchase_tile`, `patronize_great_person`
- City count vs time in game — if expansion is behind, a settler tends to be the highest-leverage production choice
- `get_trade_routes` — check for idle routes; idle routes are free yields going uncollected
- Government tier — `change_government` when a new tier unlocks (free the first time)
- Era score vs thresholds — shown in `get_game_overview`; a Dark Age is recoverable but costly
- Great People — `get_great_people`; rivals will recruit what you don't

### Around every 20 turns:
- `get_diplomacy` — delegations to new civs, friendships with Friendly civs, alliances if eligible
- `get_victory_progress` — check all 6 victory types, not just your own path
- `get_religion_spread` — religious victory is invisible without active checking; a rival with majority in most civs is a serious threat

### Around every 30 turns:
- `get_strategic_map` — fog per city + unclaimed resources
- `get_global_settle_advisor` — best remaining settle sites
- Wonder scan: `get_city_production` in your best city — wonders that align with your victory path are worth considering
- Victory path check: is your chosen path still viable? Is any rival close to winning something you haven't been tracking?
- Civ kit check: are you building/using your unique units, buildings, or improvements? If not, you're playing a generic civ and giving up your structural advantage. The unique unit often requires a specific tech — if that tech isn't on your current research path, that's a problem.

## Deity Strategy Playbook (神级生存与复利)

**核心世界观**:文明6是有限回合内的资源配置与复利竞争,不是建漂亮帝国。每次行动问:当前最大瓶颈是什么?这个行动解决了吗?机会成本?它是否提高未来几十回合的资源生成?神级允许科技/文化/军力排名落后,但不允许浪费土地、生产力和时间。

**每回合三问**:有没有便宜可占?有没有回合可偷?有没有资源闲置?

**行动顺序**:优先生存 → 扩张 → 建立复利机器 → 围绕一个胜利条件集中资源。不要平均发展,不要生产无用单位,不要为收藏建奇观,不要为打赢战争而打战争。每10回合重新评估胜利方向/最大瓶颈/最大威胁/最大机会。

### 生存(前期)
- **城墙是最高性价比投资**:宣战前用~320金秒买城墙。无墙城市3回合沦陷,有墙城市顶住全部攻势。
- **识别宣战前兆**:AI攻城武器(投石机/攻城车)+近战单位在边境集结3-5回合=战争信号,立即切产兵/买防御。
- **不打高一级时代兵种**(如Man-at-Arms CS45):集火弱目标、弃城保单位、等己方科技升级。
- **白和平比消耗战划算**:神级硬拼军力是下策;消耗对方攻城单位后主动求和。
- **远程守城是核心,近战是炮灰**:弓/弩手留在城墙内集火,近战只堵路补刀。
- **开放边境只给盟国(致命教训)**:德国多次提议"互开边境+金币"都被接受,结果德国借道把5个高级单位(线列步兵CS65/骑士/野战炮)集结到伦敦城下突袭宣战,2-3回合破城。对 UNFRIENDLY/军事强于己的 AI 一律拒绝开放边境。
- **军事代差红线**:对方已解锁线列步兵/骑士(CS50-65),我方若只有弓/枪兵(CS25)就打不动。扩张期必须并行升级兵种(弓→弩手需机械,勇士→剑客需铁),边境城市一律先买城墙。
- **首都丢失=忠诚雪崩**:首都(忠诚锚)丢失后,所有城市忠诚压力暴涨,连锁叛乱。防首都 > 一切;首都永远要有城墙+驻军+机动部队。
- **识别"借道"真面目**:AI单位反复在边境集结(即使和平)就是宣战前兆,立即切军事生产+买墙(前次俄罗斯、本次德国都如此)。

### 扩张(复利机器)
- **英国Pax Britannica的复利=城市数**(每城+1商路容量)。扩张是第一优先级,没有之一。
- **马格努斯(给养保障)+殖民政策=扩张引擎**:开拓者不耗人口+50%产力,伦敦可7回合/个连续产。
- **建城前验证距离>(3格)**:距任何城市≤3格无法建城,开拓者白走是巨量浪费。
- **新城瓶颈是生产力**:新城顺序=纪念碑(领土)→建造者(改良马/盐/石头)→自持。沙漠无食物点慎重。
- **战略资源(铁/马)尽早占领改良**:铁=剑客,马=骑手,晚占=晚解锁整条兵线。
- **200回合10城目标**:伦敦每7回合一个开拓者+偶尔买,成批生产(先上殖民政策再连产)。

### 资源与金币
- **金币不囤积**:买建造者/单位/建筑绕过生产时间;400金买建造者改良3块资源远胜躺着。
- **过剩奢侈品(>1份)主动卖AI**:先`propose_trade mode=test`确认报价,再全额匹配send(注意首付金币也要带上)。
- **战略资源改良后+2~3/回合**(骑士阶级政策再+1),长期复利。

### 科技与市政
- **科技落后是结果不是根因**:城市少→学院少→科研慢。扩张解决科技,而不是反过来。
- **封建主义(农场+1食物)优先于军事科技**:粮食是人口瓶颈,人口是产力/科研/金币的根。
- **Eureka/Inspiration主动触发**:改良资源/建区域/建城墙,顺手完成。

## Toolkit (决策辅助工具)

`scripts/civ6_tool.py`(纯计算,离线可用):
- `dist X1 Y1 X2 Y2` — hex距离(建城>3、射程、移动判断)
- `settle X Y` — 建城合法性(距所有城>3)+最近城
- `combat CS1 HP1 CS2 HP2` — 战斗伤害估算+胜负预判
- `status` / `plan` / `plan-save '[[x,y],...]'` — 帝国概览、扩张进度、记录建城目标

`scripts/civ6_assist.py`(决策助手,直连MCP会话):
- `precheck` — end_turn前预检:阻塞项(生产/研究/政策/使者/外交/WC)+机会项(卖奢侈品/改良/Eureka)+单位状态
- `units` — 单位全景(可行动/已行动/可升级/可建)
- `promotions` — 批量晋升检查(替代手动循环)
- `expansion` — 城市/合法建城点/未改良资源
- `threats` — 威胁排序(阵营+CS+HP+距城)
- `cities` — 城市队列/增长/掠夺/城墙诊断

**每回合流程**:precheck → 修阻塞 → threats+combat集火 → expansion选址+settle验证 → 处理机会(卖奢侈品/改良/Eureka) → units确认 → skip_remaining_units → end_turn。

### 已知工具坑(本次运行实测)
- **end_turn神级AI回合5-10分钟**:必须一次通过,失败循环=巨量浪费。
- **AI交易/外交提议时效极短**:end_turn返回时立即响应,否则NO_DEAL/NO_SESSION。
- **新生产/购买的单位当回合不能移动**(NO_MOVES),下一回合才行。
- **军事单位不能与驻军同城堆叠**(STACKING_CONFLICT),城内已有驻军就移到邻格。
- **攻击后立即重查get_units再补刀**:目标可能已被击杀(NO_ENEMY)。
- **使者令牌死锁**:`send_envoy`在无UI环境可能不消耗令牌,`end_turn`被GIVE_INFLUENCE_TOKEN永久卡住;重启游戏(restart_and_load)可解(挂起的UI操作会在重启后执行)。
- **升级单位后unit_id变化**:需重新get_units拿新id。
- **建城通知显示"开拓者killed"是正常消耗**(单位变为城市),不是损失。
- **地形移动成本**:洪泛区/丘陵/森林/丛林=2移动,易STOPPED_MID_PATH;远距离先get_pathing_estimate。

## Strategic Patterns

### Moving Civilians
Before moving a builder, settler, or trader to a new tile, `get_map_area` (radius 2) around the destination is worth the query. Civilians have zero combat strength — a single barbarian scout captures them. The cost of losing a builder (5-7 turns of production + charges) is almost always worse than taking one extra turn to check or escort.

Hills cost 2 movement, forests/jungles cost 2, and they stack (forest-hills = 3+). A settler or builder with 2 base moves arriving on forest-hills uses all movement and can't act until next turn. Route through flat terrain when possible, or plan to arrive one turn early.

`get_pathing_estimate(unit_id, target_x, target_y)` estimates how many turns a unit needs to reach a destination, using the game's actual pathfinding. Use it before committing units to long marches.

### Builder Management
Idle builders are wasted production. `get_builder_tasks` shows all tiles needing improvements across your empire, prioritized (URGENT > HIGH > NORMAL), with the nearest idle builder for each task. Call it once per turn during the builder phase, then dispatch builders top-down by priority.

Don't skip builders that are 3-4 tiles from a task — a few turns of walking is better than sitting idle forever. For long-distance dispatches, use `get_pathing_estimate` to verify the route. Map tiles now show movement cost (`[mv:2]`, `[mv:3]`) and road presence — route builders along roads when possible.

After context compaction, call `get_builder_tasks` again to reconstruct your builder situation. The tool provides a fresh snapshot — no need to remember previous assignments.

### Spending Gold & Faith
Gold and faith sitting idle lose value over time. `purchase_item(city_id, item_type, item_name)` buys units/buildings instantly with gold (or faith via `yield_type="YIELD_FAITH"`). `purchase_tile(city_id, x, y)` buys a specific tile. `patronize_great_person` buys a GP outright. If you're saving, name the item and the turn — otherwise, deploy it.

### Expansion
Each city multiplies your districts, yields, and Great Person generation. The gap between a 3-city and 5-city empire by the Medieval era is hard to recover from. If city count is lagging, a settler is typically the highest-impact production choice — more so than most infrastructure in existing cities. Check loyalty before settling: negative-loyalty sites near rivals need a governor assigned immediately via `assign_governor(governor_type, city_id)` or they'll flip.

### Growth
Stagnant cities fall behind exponentially. If any city has food surplus ≤ 0, that's worth fixing this turn (Farm, Granary, domestic Trade Route, or `set_city_focus(city_id, "FOOD")`). Turns-to-growth over 15 is a signal the city needs food infrastructure.

### Exploration
You can't settle what you can't see, and you can't counter threats you don't know exist. A scout set to `automate` is one of the best investments in the early game. If a scout is lost or stuck, replacing it early keeps the information flow going.

### Diplomacy
Diplomacy generates yield: each alliance +1 favor/turn per alliance level, each suzerainty +1 favor/turn. Government tier also gives favor. This compounds. Friendships don't give favor directly but enable alliances (which do). Delegations (25g) are cheap on first meeting. Friendships open up when a civ is Friendly. Alliances require friendship (30+ turns) and Diplomatic Service civic. Embassies are available once Writing is researched.

If favor is accumulating above 100 with no World Congress imminent, it's worth thinking about whether it could be better deployed in trade or alliance building.

### War Declaration
War declarations take effect for diplomacy immediately but the **combat engine does not sync until the next turn**. After declaring war via `send_diplomatic_action`, units cannot attack the new enemy until the following turn. Plan accordingly: declare war on turn N, position units adjacent to targets, then attack on turn N+1. Do not reload or retry if attacks return `NO_ENEMY` on the declaration turn — this is expected behavior.

### Wartime
During war, keeping a military unit garrisoned in or near each city is worth the tradeoff against offensive strength. Cities with walls can fire at enemies via `city_action(city_id, "attack", target_x, target_y)` (range 2). Cities that fall are expensive to recover — when you capture a city, `city_action` with `keep`, `raze`, or `liberate_founder`/`liberate_previous` resolves the decision. If your military strength is significantly below an enemy's and you're not making progress, `propose_peace(player_id)` — available after a 10-turn cooldown — is usually better than a war of attrition while the rest of the map moves on.

### Military Readiness
Check rival military strength in `get_diplomacy` periodically. A neighbor at 2x+ your strength who isn't a friend or ally is a risk worth taking seriously. Minimum useful peacetime: 1 garrison per city plus a mobile unit. Units become progressively weaker relative to rivals if not upgraded (Slinger→Archer with Archery, Warrior→Swordsman with Iron Working) — use `upgrade_unit`.

### Barbarian Camps
Camps upgrade with the era — an Ancient-era camp spawns Warriors; the same camp in the Medieval era spawns Man-at-Arms. Clearing a camp within a few turns of finding it is almost always easier than fighting the units it produces over many turns.

### Religion
Religious victory is the easiest win condition to miss because it produces no notifications and unfolds slowly. `get_religion_spread` shows the picture. If a rival religion reaches majority in most civs, the window for a response narrows quickly. Religious units bought from a city carry **that city's majority religion** — buy them from cities where your own religion is majority, not a converted city.

To found a religion: build a Holy Site → earn a Great Prophet → `get_religion_beliefs()` to see available beliefs → `found_religion(name, beliefs)`. The Great Prophet pool fills early (roughly half the major civs).

Trade routes spread the origin city's religion to the destination — worth factoring into routing decisions if conversion pressure is a concern.

### Victory Path Viability
Some paths close. It's worth checking periodically via `get_victory_progress`:

- **Science**: Campuses → Universities → Spaceport → 4 space projects. Research Alliances and Great Scientists accelerate.
- **Culture**: Tourism (offense) vs rival domestic tourists (defense). Theater Squares, Great Works, Wonders, Open Borders (+25%), Trade Routes (+25%). Late-game: National Parks, Rock Bands, Seaside Resorts.
- **Religious**: Requires a founded religion (Great Prophet pool fills early). Missionaries spread; Apostles fight theological combat (killing = 250 pressure in 10-tile radius). Buy religious units only from cities where your religion is majority.
- **Diplomatic**: 20 DVP. World Congress resolutions, scored competitions, wonders. Favor from government tier, alliances, suzerainties. If a DVP-stripping resolution targets you, vote Option B on yourself (net 0 vs -2).

## Combat Quick Reference

| Unit | CS | RS | Range |
|------|----|----|-------|
| Warrior | 20 | — | — |
| Slinger | 5 | 15 | 1 |
| Archer | 25 | 25 | 2 |
| Barbarian Warrior | 20 | — | — |

- Ranged attacks don't take damage; melee attacks do
- Forests/mountains block ranged LOS — targets with blocked LOS are filtered from `get_units` attack lists
- Fortified units: +4 defense, heal each turn
- Combat estimates include promotion CS bonuses, flanking (+2 per adjacent friendly to defender), support (+2 per defender's adjacent friendly), and forest/jungle defense (+3)

## Unit Actions Reference

| Action | Effect | Notes |
|--------|--------|-------|
| `move` | Move to tile | target_x, target_y required |
| `attack` | Attack enemy | Shows damage estimate; melee/ranged auto-detected |
| `fortify` | +4 defense, heals | Military only |
| `heal` | Fortify until full HP | Auto-wakes at full HP |
| `alert` | Sleep, wake on enemy | Sentry use |
| `sleep` | Sleep indefinitely | Manual wake required |
| `skip` | End unit's turn | Always works |
| `automate` | Auto-explore | Scouts only |
| `delete` | Disband unit | Removes maintenance |
| `found_city` | Settle | Settlers only |
| `improve` | Build improvement | Builders and Military Engineers; see improvements below |
| `remove_feature` | Chop/harvest feature | Builders only; removes forest, jungle, or marsh from tile |
| `build_route` | Build road/railroad | Military Engineers only; on current tile; no charges used |
| `trade_route` | Start route | Traders; target_x/y of destination city |
| `teleport` | Move idle trader | Traders only; target_x/y of city |
| `activate` | Use Great Person | Must be on completed matching district |
| `spread_religion` | Spread religion | Missionaries/Apostles |

Common improvements: `IMPROVEMENT_FARM`, `IMPROVEMENT_MINE`, `IMPROVEMENT_QUARRY`, `IMPROVEMENT_PLANTATION`, `IMPROVEMENT_PASTURE`, `IMPROVEMENT_CAMP`, `IMPROVEMENT_FISHING_BOATS`, `IMPROVEMENT_LUMBER_MILL`

Feature removal: Forest, jungle, and marsh tiles block most improvements (e.g. Farm). Use `remove_feature` to chop/harvest the feature first, then `improve` to build. Lumber Mill and Camp work on forest/jungle without removal. Check `valid_improvements` in `get_units` output — if FARM isn't listed on a tile you expect it, the tile likely has a blocking feature.

Builders repair tile improvements. Pillaged **district buildings** (Workshop, Arena, etc.) are repaired via `set_city_production`.

`get_cities` shows unimproved resource tiles and pillaged improvements/districts per city — use this to prioritize builder work without needing to scan `get_map_area` manually.

Military Engineers (requires Encampment + Armory): `build_route` builds a railroad on the current tile (no charges consumed; costs 1 Iron + 1 Coal per tile). `improve` with `IMPROVEMENT_FORT` or `IMPROVEMENT_AIRSTRIP` uses charges. Building a railroad consumes all movement — one tile per engineer per turn.

| Other unit tools | |
|--------|--------|
| `skip_remaining_units` | Skip all units with remaining moves (useful after diplomacy) |
| `upgrade_unit(unit_id)` | Upgrade to next type (requires tech + resources + gold) |

## End Turn Blockers

`end_turn` resolves blockers before advancing. If it returns a blocker:
- **Units**: unmoved units need orders (move / skip / fortify)
- **Production**: city queue empty — set new production
- **Research/Civic**: completed — choose next
- **Governor**: point available — `get_governors` → `appoint_governor` / `assign_governor(governor_type, city_id)` / `promote_governor(governor_type, promotion_type)`
- **Promotion**: unit has XP — `get_unit_promotions` → `promote_unit`
- **Policy Slot**: empty — `get_policies` → `set_policies`
- **Pantheon/Religion**: faith threshold reached — `get_pantheon_beliefs` → `choose_pantheon`; for founding: `get_religion_beliefs` → `found_religion`
- **Envoys**: tokens available — `get_city_states` → `send_envoy`
- **Dedication**: new era — `get_dedications` → `choose_dedication`
- **City Capture**: conquered or disloyal city — `city_action(city_id, "keep"/"raze"/"liberate_founder"/"liberate_previous")`
- Move responses show the **target tile**, not arrival position (async pathfinding)

## Diplomacy

**Reactive (AI-initiated):** AI encounters block turn progression. Use `get_pending_diplomacy` to check for open sessions, then `respond_to_diplomacy` (POSITIVE/NEGATIVE, 2-3 rounds). Diplomacy sessions do not affect unit movement or orders — continue commanding units normally afterward.

**Proactive:**
- `send_diplomatic_action(action="DIPLOMATIC_DELEGATION")` — 25g, worth sending on first meeting
- `send_diplomatic_action(action="DECLARE_FRIENDSHIP")` — requires Friendly status
- `send_diplomatic_action(action="RESIDENT_EMBASSY")` — requires Writing tech
- `form_alliance(player_id, type)` — types: MILITARY/RESEARCH/CULTURAL/ECONOMIC/RELIGIOUS; requires friendship 30t + Diplomatic Service civic
- `propose_trade(player_id, ...)` — trade gold/GPT/resources/favor/open borders/cities. Use `mode="test"` first to see the AI's counter-offer without committing, then `mode="send"` to finalize. Cities use `city_id` from `get_trade_options`.
- `propose_peace(player_id)` — white peace; 10t war cooldown required
- `get_trade_options(other_player_id)` — see what a civ has available to trade (gold, resources, favor, cities, agreements)
- `get_pending_trades` — check incoming trade offers; `respond_to_trade(player_id, accept)` to accept/reject
- Check `get_diplomacy` for defensive pacts before declaring war
- `get_diplomacy` shows leader agendas — historical agendas are always visible; random agendas require Secret diplomatic visibility (spy in their capital or alliance). Use agendas to predict AI behavior and avoid relationship penalties.

**Espionage:** `get_spies` → `spy_action(spy_id, action, ...)`. Actions: `travel` to a city first, then run operations (steal tech, neutralize governors, etc.). Offensive missions only work after the spy arrives.

**City-states:** `get_city_states` → `send_envoy`. Suzerainty = +1 favor/turn. Types: Scientific/Industrial/Trade/Cultural/Religious/Militaristic.

**Diplomatic Favor:** earned from government tier (base +1, scales with tier), alliances (+1/t per level), suzerainties (+1/t). Spend in World Congress for Diplomatic Victory Points.

## Production & Research

Wonders — high-production cities can slot these between infrastructure. Use `get_wonder_advisor(city_id, wonder_name)` for placement, then `set_city_production` with target_x/y. Science: Great Library, Oxford University, Kilwa Kisiwani. Culture: Chichen Itza, Forbidden City. General: Ancestral Hall, Pyramids.

**Research:** `get_tech_civics` sorts by turns ascending; items ≤ 2 turns are flagged `!! GRAB THIS` — cheap boosted techs are easy to miss and can unblock entire production chains.

**Purchasing:** `purchase_item(city_id, item_type, item_name)` — buy units or buildings instantly with gold (default) or faith (`yield_type="YIELD_FAITH"`). `get_city_production` shows purchasable items and costs.

**Tiles:** `get_purchasable_tiles(city_id)` → `purchase_tile(city_id, x, y)` — buy border tiles with gold for strategic resources or district placement.

## District Placement

Use `get_district_advisor(city_id, district_type)` for ranked tiles. Then `set_city_production` with target_x/y.

| District | Adjacency bonuses |
|----------|------------------|
| Campus | +1 per mountain, +1 per 2 jungles, +2 geothermal/reef |
| Holy Site | +1 per mountain, +1 per 2 forests, +2 natural wonder |
| Industrial Zone | +1 per mine/quarry, +2 aqueduct |
| Commercial Hub | +2 adjacent river, +2 harbor |
| Theater Square | +1 per wonder, +2 Entertainment Complex |
| Encampment | cannot be adjacent to city center |

## Trade Routes

- `get_trade_routes` — see all active routes and idle traders
- `get_trade_destinations(unit_id)` → available destinations
- `unit_action(action='trade_route', target_x, target_y)` → start route
- Domestic routes: food + production to new cities. International: gold.
- Capacity: 1 from Foreign Trade civic, +1 per Market/Lighthouse
- Idle routes are free yields going uncollected

## Great People

- `get_great_people` — candidates, recruitment progress, and costs
- `recruit_great_person(individual_id)` — recruit with accumulated GP points (check `[CAN RECRUIT]`)
- `patronize_great_person(individual_id)` — buy instantly with gold or faith
- `reject_great_person(individual_id)` — pass, advance to next candidate in that class
- Rivals will recruit what you pass on — recruiting quickly tends to be worth it
- Once recruited, move the GP to its matching completed district; `unit_action(action='activate')`
- If activation fails, the error message includes the requirements (district type, buildings needed)
- Don't delete GPs — they show 0 builder charges but that's a different system; they're not consumed until activated

## World Congress

WC fires synchronously inside `end_turn()` — register votes **before** calling end_turn.

**Voting flow:**
1. `get_world_congress()` — when `turns_until_next = 0`, WC fires this turn
2. Review resolutions (options A/B, target list, favor costs)
3. `queue_wc_votes(votes='[{"hash": H, "option": 1, "target": 0, "votes": N}]')`
4. `end_turn()` — handler fires, votes deploy, turn advances

- `hash`: from `get_world_congress`; `option`: 1=A / 2=B; `target`: player_id resolved to list index at runtime; `votes`: max to spend
- 1 free vote per resolution (costs nothing — worth casting)
- Extra votes cost 6/18/36/60/90/126... cumulative favor
- Keeping 50-100 favor in reserve between sessions provides flexibility for the next session
- DVP resolutions: read what each option actually awards before voting. Concentrate favor on the single most impactful resolution rather than spreading thin. Verify your vote blocks the rival, not accidentally helps them

## Victory Conditions

| Victory | Win Condition | Monitor Via |
|---------|---------------|-------------|
| Science | 4 space projects complete | `get_victory_progress` |
| Domination | Own all rival original capitals | military strength in `get_diplomacy` |
| Culture | Foreign tourists > every civ's domestic | tourism in `get_victory_progress` |
| Religious | Your religion majority in ALL civs | `get_religion_spread` regularly |
| Diplomatic | 20 diplomatic victory points | World Congress votes |
| Score | Highest score at turn limit | fallback |

All victories trigger immediately when the condition is met — they do not wait for a turn boundary or WC session. A rival reaching 20 DVP wins before your next turn. The only counter is stripping DVP at a World Congress *before* they reach 20.

`end_turn` runs a victory proximity scan every turn and a full snapshot every 10 turns. These warnings are the primary signal for invisible victories — worth paying attention to.

## Game Recovery

**MCP autosaves:** `end_turn` automatically saves every turn as `0_MCP_NNNN` (last 5 kept). These are your primary recovery points.

**Load by name** (preferred — no `list_saves` needed):
```
load_game_save("0_MCP_0079")  # load specific turn (~5s via Lua, ~90s via menu fallback)
get_game_overview              # verify load
```

**When the game hangs** (AI turn loop):
```
restart_and_load("0_MCP_NNNN")   # kill + relaunch + load (~90s)
get_game_overview                 # verify load
```

**Turn regression detection:** If you accidentally load a wrong save (e.g. the T1 scenario save instead of your autosave), `end_turn` will emit a CRITICAL warning with the correct autosave name to reload.

Other tools: `list_saves`, `load_save(index)`, `kill_game`, `launch_game`, `load_save_from_menu(name)`.
Save names omit extension: `"AutoSave_0221"` not `"AutoSave_0221.Civ6Save"`.
