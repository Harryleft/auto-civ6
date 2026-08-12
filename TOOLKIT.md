# Civ 6 决策辅助工具包

基于 T77→T131 运行的 772 次工具调用日志分析,把**重复、易错、依赖记忆**的操作自动化成两个工具。
目标:减少失误、偷回合、提高决策质量。

---

## 1. 可自动化的操作清单(对照日志问题)

| 操作 | 手动做法(本次运行的浪费) | 工具替代 |
|------|--------------------------|----------|
| 晋升检查 | 每回合手动 for 循环调用 get_unit_promotions × 2-6 次 | `civ6_assist.py promotions` 一次批量 |
| 单位状态确认 | 移动/攻击后反复 get_units(59 次,其中大量因不验证而失败) | `civ6_assist.py units` 一次全景 |
| 建城距离验证 | 心算 hex 距离 → 本次 (24,17) 选错白走 3 回合 | `civ6_tool.py settle X Y` 自动验证 >3 |
| 战斗伤害预估 | 依赖游戏逐次 combat estimate | `civ6_tool.py combat CS1 HP1 CS2 HP2` 先算 |
| end_turn 预检 | end_turn 返回 blocker 才发现(每次失败=10 分钟 AI 回合) | `civ6_assist.py precheck` 先修 blocker |
| 威胁排序 | 手动读 get_units 威胁段判断优先级 | `civ6_assist.py threats` 按距城+CS 排序 |
| 扩张进度 | 记忆城市数/目标 | `civ6_assist.py expansion` 城市+建城点+未改良资源 |
| 城市诊断 | 逐城读 get_cities | `civ6_assist.py cities` 队列/增长/掠夺/城墙 |
| 资源机会 | 漏卖过剩奢侈品、漏改良 | `precheck` 自动列出(可卖/未改良/Eureka) |
| 研究/市政空 | end_turn 被 blocker 才发现 | `precheck` 自动检测 |

---

## 2. 工具一:`scripts/civ6_tool.py`(计算器)

```bash
python3 scripts/civ6_tool.py dist 18 16 25 17     # hex距离(建城>3、射程、移动)
python3 scripts/civ6_tool.py settle 25 17          # 建城合法性(距所有城>3)+最近城
python3 scripts/civ6_tool.py combat 25 85 45 60    # 战斗伤害估算+胜负预判
python3 scripts/civ6_tool.py status                # 帝国概览+城市数
python3 scripts/civ6_tool.py plan                  # 扩张进度(4/10)
python3 scripts/civ6_tool.py plan-save '[[25,17]]' # 记录建城目标
```

## 3. 工具二:`scripts/civ6_assist.py`(决策助手,直连 MCP 会话)

```bash
python3 scripts/civ6_assist.py precheck     # end_turn 前:阻塞项+机会项+单位状态
python3 scripts/civ6_assist.py units        # 单位全景(可行动/已行动/可升级/可建)
python3 scripts/civ6_assist.py promotions   # 批量晋升检查(替代手动循环)
python3 scripts/civ6_assist.py expansion    # 扩张:城市/合法建城点/未改良资源
python3 scripts/civ6_assist.py threats      # 威胁排序(阵营+CS+HP+距城)
python3 scripts/civ6_assist.py cities       # 城市:队列/增长/掠夺/城墙诊断
```

游戏离线时工具会快速报错退出(不挂起)。

---

## 4. 推荐每回合流程(把工具嵌入回合循环)

```
1. civ6_assist.py precheck     ← 一次看清:blocker + 机会 + 单位
2. 按 precheck 顺序处理阻塞项:
   - 生产空 → set_city_production
   - 研究/市政空 → set_research
   - 政策空 → set_policies
   - 使者令牌 → send_envoy
   - 外交会话 → respond_to_diplomacy/trade
   - 世界议会 → queue_wc_votes
3. 战斗:threats 排序 → 用 civ6_tool.py combat 预判 → 集火
4. 扩张:expansion 选点 → settle 验证距离 → 移动开拓者
5. 机会:precheck 的"机会项"(卖奢侈品/改良资源/Eureka)逐条做
6. 单位:units 确认无遗漏 → skip_remaining_units
7. end_turn(一次性通过,避免 10 分钟失败循环)
```

---

## 5. 针对本次运行教训的工具化修正

| 本次失误 | 工具如何阻止 |
|----------|-------------|
| 新单位当回合移动→NO_MOVES(27次) | units 显示 moves 0 即不指挥 |
| 军事单位堆叠→STACKING(17次) | settle/units 显示目标格占用 |
| 重复攻击死目标→NO_ENEMY(6次) | attack 前 threats/units 确认目标存活 |
| 建城距离不足→CANNOT_FOUND(8次) | settle 先验证 >3 |
| 建筑/研究 blocker 反复 | precheck 先修 |
| 皮草交易参数不全被拒 | propose_trade 用 test 确认完整报价 |

---

## 6. 未来可扩展(尚未实现)

- **战斗规划器**:输入敌方列表,自动算出最优击杀组合(集火计算)
- **研究路线规划**:给定目标科技/市政,输出最短路径+Eureka 触发点
- **自动选址评分**:建城顾问分数 + 忠诚压力 + 距离 + 资源加权
- **end_turn 全自动预检执行**:检测到 blocker 直接修(生产/研究/政策自动化)
- **外交动作建议**:关系值→代表团/好友/联盟时机表
