## 状态：已连接
当前回合和分数见 system-reminder。**下一步操作前先读日志确认状态。** 打完一局或放弃当前局时，调用 `launch_game` 返回主菜单。

## 铁律（不可协商）
1. **每回合调用 `end_turn` 之前，必须先输出全部 5 条反思。** 没有反思就调用必定被拒，白白浪费一次工具调用。下一轮 `get_game_overview` 会提供上一轮的真实数据，供你基于事实来写。
2. **在地图迷雾中，绝不猜坐标、绝不猜单位 ID、绝不猜城市 ID。** 先调用 `get_game_overview` 或 `get_units` 拿到真实数据再行动。连单位是否存在都不确定时绝不下命令。坐标或 ID 对不上当前回合的真实数据时，命令会静默失败。
3. **绝不假设前一个动作成功了。** 每个动作工具都会回读 FireTuner 状态进行验证，**动作工具返回之后**，单位位置、生产队列、研究状态就是权威的当前真实值——在这份返回之后绝不允许再凭空声明位置、生命值、敌人或可见格。需要发起后续动作时，从验证过的返回结果里取坐标和 ID；不知道就**重新读取**，不要编。
4. **绝不把"敌人不可见"说成"没有敌人"。** 单位进入视野时立刻声明。用 `war_state`（见 `get_game_overview`）判断谁处于战争状态——它覆盖可见与不可见的所有敌人，哪怕没有任何一个敌人单位在视野内。

## `end_turn` 前的 5 条反思（每条 ≥20 字符）
1. tactical：本回合的微观处理——每条单位命令、城市指令，以及结果如何
2. tooling：本回合工具使用情况——哪个好用、哪个不好用、发生了什么错误
3. strategic：方向层面——扩张、科研/文化路线、军事态势、胜利路径
4. planning：下一回合每个单位、每座城市分别要做什么
5. hypothesis：一条可检验的预测，以及下回合用什么数据来验证它

`end_turn` 被 blocker 拒绝后：全部 5 条必须重写，必须直接针对那次的 blocker，并引用更新后的真实数据——不许换几个字就重新提交。最小可用写法：tactical 给出具体的下一回合命令，strategic 把思路回锚到真实数据，planning 讲缓解方案，hypothesis 说明它如何改变下一步行动。

## 信息日志（Diary）—— 决策依据优先查这里，而非回合文本

**本局每一回合（不只是刚打完的那回合）都要调用 `get_diary`。** 情报和决策依据必须优先来自日志；回合文本只用于 cross-check 或日志没有覆盖的内容。被截断的输出不是故障——用 `turn`、`from_turn`/`to_turn`、`last_n` 参数翻页，把本局到目前为止的全部条目读完。日志就在那里，别漏掉条目。

## 区块结构（省下文）

工具返回的文本由 `◆` 或 `•` 前缀的区块标题组织。**先扫区块标题，只读当前决策需要的那几个区块。** 其余区块标题提供足够的上下文；忽视整条信息之前先读标题，再决定是否需要其内容。

## 状态格式
工具返回纯文本——概览是被分区的紧凑摘要（按区块标题阅读）；单位/城市/科技文化是逐项列表。**开始规划前先扫区块标题，只展开需要的区块。** 单位显示 HP、移动状态、所在格、一格内的威胁、可用动作。贸易画面（贸易、外交）只输出会发生变化的摘要行——不回显完整画面。

## 全局地图与空间智能
- `get_full_map` 拉取完整地图（自然奇观、城邦、资源、文明、城市）——首次调用拉全量，之后每 N 回合按需增量。**尽早调用一次**，后续决策才有个全局锚点。
- 任何单位/城市动作之后都会生成**战报**：用自然语言描述你的单位（和已知敌人）相对每个己方/敌方单位与城市的位置关系——例如"你的勇士在伦敦东北 3 格，被 2 个蛮族包围"。

## 动作后的验证规则
每次工具调用后：
1. 解析验证过的返回值（位置、队列、研究、协议）。
2. 本次调用之后的所有决策只允许使用这些**已验证的值**。
3. 只列出你本回合**实际读到**的格子、单位、敌人；明确区分**已验证的事实**与假设/传闻。
4. 上下文里没有的事实，**先读再声明**。

## 硬性规则
- **命令只能在同一回合内生效，且必须使用当时真实的位置/ID。** 回合切换后，把 `get_units`/`get_cities`/`get_game_overview` 当成唯一权威：单位会移动，敌人会出现或消失，城市队列会重置。绝不要在没有用当前回合的 `get_units` 数据复核的情况下，就命令一个单位"返回它上回合的位置"。**例外：`UNITCOMMAND_MOVE_TO` 在切回合后仍然可靠**——它对已下命令的单位是持久的，单位会继续朝目标走，即便迷雾中的威胁让你原本不会下这个命令。
- 不知道单位当前在哪，就不要下移动/攻击命令——先查。
- **例外：跟随、护航、进驻城市（单位或城市居中）可以安全引用，无需重新定位。**
- **绝不浪费回合**（见下方 stop-hook 反馈）。每条命令都要可解释、可追溯。
- 城市被占领时立即决定**保留/夷平/解放**（工具会返回 `capture_decision` 提示）。
- 如果你修改了代码，重启前先把改动写出来。

## 错误处理
- 工具的 `Error:` 前缀消息=失败。如果信息里写了"先调用 X"，就先调用 X。
- 同一个 blocker 不要带着同样的 payload 重试 `end_turn`——先修复 blocker（生产、科研、单位命令等）。
- 返回 `HANG:` 时游戏已自动恢复。最多重试当前回合 2 次，然后中断并报告。
- **FireTuner 断线不是失败。** 返回"connection lost / reconnecting"时，把报告写进反思然后重试。
- **绝不把 FireTuner 断线当作谎言、崩溃或卡死来报告。** 连接问题交给恢复流程处理，别编诊断。

## Stop-hook 反馈（"回合信息不足"）
含义：给 `end_turn` 的反思没有引用本回合的具体证据。
- 重写全部 5 条，引用真实单位、城市、敌方威胁、行动与结果。
- 每条 ≥20 字符；战术条目要带上具体的下一步命令。
- 写之前调用 `get_units` + `get_cities`；不许复述旧文本。

## 世界意识形态（宗教、战争、贸易）
- 宗教：追踪敌方宗教压力；决定何时/是否创立或支持万神殿与宗教。
- 战争：记录敌我位置、城防、胜负比；战争策略必须引用**已验证**的地图位置。
- 贸易：与城邦/文明签订有利的商路、开放边界、研究协议。

## 研究协议
- 只在回报足够高时签研究协议；签之前先想清楚钱从哪来。
- 在下一回合的规划/假设里写清楚每个协议怎么帮到你（共同科研提速、贸易、外交），并跟踪进度直到完成。

## 世界议会
- 每个会期都要跟踪议程与己方代表团；当投票能改善你的贸易/外交地位时，用 `queue_wc_votes` 投票，并在日志里注明。

## 区域放置
- 当放置会影响扩张、防御或长期收益时，用 `get_adjacency_map` 获取邻接加成。

## 宗教操作
- `get_religion_info` 跟踪万神殿、宗教、信念，从而选择宗教相关行动。

## 扩张
- 尽快铺到 3-4 城，优先奢侈品/战略资源与产能地块。
- 用 `get_game_overview` + `get_full_map` 做选址侦察，而不是瞎猜；用城市产出概览决定坐下之后的建造顺序。
- 大堡礁之类前期自然奇观可以撑起一座分城——如果它能提供独特产出或加成，就优先坐它。

## 战争
- 推进前集结 3-4 个单位（勇士/弓手）；有城墙就配破城槌，并带上近战+远程组合。
- 攻城时协调近战与远程；把战况和损失记进日志。

## 贸易
- 有商队容量就派商路；贸易协议优先签能保障补给与战略资源的。

## 万神殿 / 宗教
- 尽早用 `set_pantheon` 立万神殿；决定要强化哪个信念并在日志里跟踪宗教压力。

## 总督
- 尽早任命马格努斯或平伽拉，并把调动记进日志。

## 结社
- 要尽早决定要加入哪个结社，以及每个总督头衔往哪投——尤其是对军事、科研或文化路线有影响的，必须在思考里点名。

## 蛮族
- 蛮族对扩张是实打实的威胁；用 `get_map_area` 探明营地位置，在敌人骚扰边境之前清掉或绕开。

## 停战协议
- 战争超过 10 回合时考虑议和；用 `propose_peace` 结束战争，并用日志跟踪和平条款。

## 单位经验与晋升
- 用 `get_unit_details` 跟踪单位经验值；晋升选择要能显著改变战局的（如远程单位选 +1 射程）。

## 使用工具，别凭空想
- 放置区域前用 `get_adjacency_map` 探明邻接加成。
- `get_map_area` 需要地图中心的坐标——调用前先想清楚你的首都在哪。
- 让单位走到目标附近，再在思考/日志里评估打击目标。

## 工具一览
| 工具 | 用途 |
|---|---|
| get_game_overview | 回合、时代、分数、产出、议程、战争状态、资源（含净速率） |
| get_units | 位置、HP、移动、威胁、可用动作 |
| get_unit_details | 单个单位的详细信息——经验、晋升、装备 |
| get_cities | 城市、产出、建造队列、周边单位 |
| get_tech_civics | 科研/文化进度与可用项 |
| get_spies | 间谍任务 |
| get_diary | 分回合的日志 |
| get_city_production | 城市可建项 |
| get_policies | 政策槽 |
| get_governors | 总督 |
| get_trade_routes | 商路与收益 |
| get_diplomacy | 外交（画面差分） |
| get_diplomacy_sessions | 进行中的外交会话 |
| get_pending_deals | 待处理的交易 |
| get_adjacency_map | 区域放置的邻接加成 |
| get_map_area | 探明区域（蛮族营地） |
| get_full_map | 全局地图（自然奇观、城邦、资源、文明） |
| get_religion_info | 宗教/信念 |
| run_lua | 逃生舱——原始 Lua |
| load_game_save | 按名字读档 |
| list_saves / load_save / save_game | 存档管理 |
| set_research / set_civic | 选择科研/文化 |
| set_city_production | 城市建造队列 |
| purchase_item | 用金币/信仰购买 |
| set_pantheon | 立万神殿 |
| set_policies | 政策槽 |
| promote_governor / assign_governor | 总督操作 |
| propose_trade / respond_to_trade / diplomacy_respond | 外交 |
| unit_action | 移动、攻击、驻扎等 |
| city_action | 攻击、占领决策 |
| queue_wc_votes | 世界议会投票 |
| end_turn | 推进回合 |
| kill_game / launch_game / load_save_from_menu / restart_and_load | 生命周期控制 |

---

## 常见操作手册（Playbooks）
### 移动单位
1. `get_units` 拿位置和可用动作。
2. `unit_action(unit_id, 'move', x=目标x, y=目标y)`。
3. 在思考里确认新位置。
### 攻击敌人
1. 确认攻击方在射程内（近战 1 格，远程 2-3 格）。
2. `unit_action(攻击方, 'attack', x=敌人x, y=敌人y)`。
3. 记录伤害；需要就追击。
### 建城
1. `get_units` 找到定居者（settler）。
2. `unit_action(settler_id, 'found_city')`。
3. 下一回合用 `get_cities` 规划生产。
### 城市生产
1. `get_city_production(city_id)` 看可建项。
2. `set_city_production(city_id, 'UNIT', '建造者')` 等。
3. 队列卡住时，用 `purchase_item(city_id, 'BUILDING', 'MONUMENT')` 或同类操作兜底。
### 科技 / 文化
1. `get_tech_civics` 看可用项。
2. `set_research('采矿')` 或 `set_civic('技艺')`。
3. 在日志里记进度；完成时验证。
### 政策
1. `get_policies` 看可用政策。
2. `set_policies(['slot=政策名'])`。
### 万神殿 / 宗教
1. `set_pantheon('万神殿名')`。
2. 用 `get_religion_info` 跟踪宗教压力。
### 贸易
1. `propose_trade(目标id, {'gold': 5})`。
2. 检查返回；失败就重谈。
### 交易 / 外交
1. `get_pending_deals` 查看进来的交易。
2. `respond_to_trade(交易id, True|False)`。
3. `diplomacy_respond(玩家id, 'POSITIVE')` 推进对话。
### 世界议会
1. `get_game_overview` 看议程。
2. `queue_wc_votes('[{"vote":"A","count":1}]')` 并记下结果。
### 区域放置
1. `get_adjacency_map(城市id, 区域类型)`。
2. 在候选位置中选加成最高的。

### 响应 Blocker
- `end_turn` 返回 `BLOCKED:` 时——先修生产/科研/单位，再重新调用。
- **Production**：`set_city_production` 后重试。
- **Research/Civic**：`set_research`/`set_civic` 后重试。
- **Units**：给每个单位下命令或跳过，再调用 `end_turn`。
- **Policy**：`set_policies` 后重试。
- **Religion/Pantheon**：`set_pantheon` 或调整宗教后重试。
- **Spy escape**：解决后重试。

### 卡住时
1. `get_units` 拿可用动作。
2. 选一个没试过的。
3. 反复卡在同一单位上 → 用 `get_unit_details` 深挖。

---

## 游戏生命周期（通过 MCP）
`load_game_save` / `load_save` / `save_game` / `restart_and_load` / `kill_game` / `launch_game` / `load_save_from_menu` 已经可用，**并且都会自动发现并恢复卡死或崩溃**。绝不要替它们伪造回合指令。
- **异常或工具失败之后**：`load_game_save` 会自动恢复。
- **恢复也是完整的一环**：调用 `load_game_save` / `load_save` / `restart_and_load` 时，把它当作一等公民的操作步骤，不要手搓变通方案。
- **HANG 协议**：`HANG:…` 表示游戏自动恢复了。**最多重试当前回合 2 次**，然后中断并向系统报告。
- **绝不要写假的 `end_turn` 调用**——生命周期工具会完成恢复，之后的 `end_turn` 必须带真实反思。

---

## 启动与运行（命令行）
```bash
# 一键：启动游戏（Steam）+ FrontEnd API 读档 + 自动打 N 回合（无 OCR）
uv run python scripts/integration_turns.py --save 吉尔伽美什_turn_1 --turns 10

# 启动 MCP server（agent 通过 stdio 连接）
uv run civ-mcp
```
- 集成驱动会处理：启动游戏、等待 FireTuner 端口、连接重试、脚本读档、blocker 自动解决、异常记录。
- `scripts/launch_save.py` 是旧的 OCR 菜单导航方式，保留作备用，优先用上面的集成驱动。

## 存档加载（供诊断参考）
```bash
# 1) 在**游戏外**编辑 AppOptions.txt，启用 FireTuner：
#    /Users/zhuanzmima0000/Library/Application Support/Sid Meier's Civilization VI/AppOptions.txt
#    把 EnableFireTuner 0 改为 1，把 FireTunerWorkaround 0 改为 1
# 2) 通过 Steam 启动游戏；脚本会等待 FireTuner 端口（127.0.0.1:4318）。
# 3) 在游戏内读档：
uv run python - <<'EOF'
import asyncio
from civ_mcp.connection import GameConnection
from civ_mcp.game_lifecycle import load_game_save

async def main():
    conn = GameConnection()
    result = await load_game_save(conn, "quicksave")  # 换成你的存档名
    print(result)

asyncio.run(main())
EOF
```
- `load_game_save` 通过原生 FrontEnd API 读档（秒级），**仅限主菜单**；在游戏里会被明确拒绝（不静默重启、不做 GUI 导航）。
- **绝不要自己模拟鼠标点击来读档**——内置加载器已经处理了全部边缘情况。
- 读档后 MCP server 会自动重连。

## 工具输出看不懂时
1. 用 `run_lua` 重放精确的 FireTuner 负载：
   ```lua
   local p = Players[0]; print(p:GetUnits():GetCount());
   ```
2. 对照 `src/civ_mcp/lua/*.py` 里的 FireTuner 查询；现在大多数工具都在输出里嵌了原始 FireTuner 响应。
3. 工具坏了：修复底层查询或上报。

## 手动调优检查清单（顺序执行）
1. `pgrep -f Civ6`——确认游戏在运行（进程名是 `Civ6_Exe_Child`，不是 `CivilizationVI`）。
2. `lsof -iTCP:4318 -sTCP:LISTEN`——确认 FireTuner 已启用。
3. `uv run civ-mcp` 或 MCP 客户端——确认 stdio 已连接。
4. 触发一个工具调用并观察 stderr 日志；任何"Connection refused"都意味着 FireTuner 关闭或游戏尚未加载存档。

## 会话开始时
- 先读 `AGENTS.md` / `README.md` / 本文件——它们定义了工作流。

## 遥测与监控
- 结构化的每事件日志在 `runs/<run_id>/events.jsonl`（包含 turn/action/tool）。
- 摘要（决策数、错误数、速度）在 `runs/<run_id>/run_summary.json`。
- **不要**在日志里加大型 payload（>100KB）——遥测数据要小而精确。

## 汇报bug
在思考与日志里都要包含：`tool` 名称、原始错误文本、堆栈摘要，以及你认为正确的行为应该是什么。

## 参考文档
- `AGENTS.md`——本文件（统一工作流协议）。
- `docs/README.md`——文档索引：架构（`architecture-diagrams.md`）、可观测性（`observability.md`）、存档格式、Aspyr 启动器绕行、评测与设计研究。

---

## 禁止事项（坏习惯）
- 绝不无视 stop-hook（它会强制要求反思，别试图绕过）。
- 绝不谎报回合数（用概览里的真实数据）。
- **绝不假装看见东西**——看不见就直说看不见，绝不要编造敌人、格子、协议。
- **绝不谎报分数**——只用 `get_game_overview` 给的分数。
- 绝不跳过安全协议（文件编辑、reflection 等）。
- 绝不跳过 recovery 工具——只用它们，别手搓变通。
- 绝不在同一回合里重复同一个失败动作（先修复再重试）。

---

## 单位特性（供决策参考）

**近战**（勇士、剑客等）：相邻攻击（1 格）。高 HP 时适合贴脸。**例外：反骑兵单位（长矛兵、枪兵）对骑兵有加成，要优先用它们打骑兵。**

**远程**（弓手、弩手等）：攻击距离 2-3 格（不能贴脸）。HP 较低，要保护；集火高价值目标。

**攻城**（投石车、破城槌等）：对城市/区域有加成，对其他单位较弱。

**骑兵**（骑手、骑士等）：移动力高，克制远程/攻城单位。

**海军近战/远程**：规则同陆战，但在海上。

**支援**（医疗兵、观察气球等）：不能攻击，提供加成。

**平民**（定居者、建造者、侦察兵等）：不能攻击；定居者建城，建造者改良地块。

**宗教**（使徒、传教士等）：不能攻击；用于宗教传播/战斗。
