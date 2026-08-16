# 业务逻辑与数据流转总览（Mermaid）

本文用 Mermaid 图描述 `civ6-belief-engine` 的整体业务逻辑与数据流转。图例约定：

- **唯一运行链路**：`文明 VI → FireTuner 127.0.0.1:4318 → civ_mcp MCP 适配层 → DSH`。
- 数据遵循"观察 ≠ 信念"：原始结果归 transcript/遥测，信念引擎只保存规范化事实、指纹与关联。
- 所有游戏动作必须经过治理门禁与游戏自身规则校验，写游戏只有单一写者（`execute_mutation` 通道）。

---

## 1. 系统全景数据流

端到端数据流：LLM 智能体发起 MCP 工具调用 → 授权预检 → 生成 Lua → 经 FireTuner 进游戏执行 → 解析叙述返回；同时旁路记录信念事件与三类遥测流。

```mermaid
flowchart TB
    subgraph U["智能体层"]
        DSH["DeepSeek Harness (DSH)<br/>LLM 回合循环 · 决策者"]
        CLI["其他 MCP 客户端<br/>Claude Code / Codex / Gemini CLI"]
    end

    subgraph MCP["MCP 适配层 civ_mcp (src/civ_mcp)"]
        SRV["server/ 包<br/>工具注册 · stdio JSON-RPC 入口<br/>assembly.py 生命周期 / auto-resume"]
        PIPE["pipeline._logged 管道<br/>授权预检 → 执行 → 信念记录 → 结果过滤"]
        GS["game_state.py<br/>查询 / 动作 / 回合推进 / 弹窗管理"]
        LUA["lua/ 构建器 + 解析器 + 叙述器<br/>build_* → Lua 源码 → parse_* → narrate_*"]
        CONN["connection.py<br/>GameConnection · execute_read / write / in_state"]
        RF["result_filter<br/>模型面压缩副本（遥测保留原文）"]
    end

    subgraph DOMAIN["产品域包 civ6_belief_engine (src/civ6_belief_engine)"]
        BE["belief_engine.py 事件溯源<br/>observation / belief / hypothesis / prediction<br/>plan / simulation / decision / action / attribution"]
        DERIV["derivation.py 规则注册表<br/>自动派生信念与预测（derived 标签）"]
        GOV["governance/ 治理控制面<br/>goal → proposal → critic → council<br/>→ ActionIntent → 预算锁"]
        FC["forecast/ 预测层<br/>trend_forecast / bayesian rebalance"]
        GPH["graph/ 影子图<br/>world_entity + 稳定关系边"]
    end

    subgraph GAME["文明 VI 游戏进程"]
        FT["FireTuner 调试协议<br/>TCP 127.0.0.1:4318 · 单连接"]
        GC["GameCore_Tuner<br/>State 8 直接模拟读写<br/>查询 / 跳过单位 / 晋升 / 事后校验"]
        IG["InGame<br/>State 153 UI 指令层<br/>一切玩家动作（规则校验）"]
        ENG["游戏引擎<br/>模拟状态 · AI 回合 · 战斗 · 地图"]
    end

    subgraph STORE["持久化与遥测 (~/.civ6-mcp)"]
        BELIEFS["beliefs/belief_&lt;civ&gt;_&lt;seed&gt;.jsonl<br/>append-only 事件日志 · 墓碑语义 · 可重放"]
        DIARY["diary_*.jsonl + _cities<br/>每回合状态快照 + 五段反思"]
        LOG["log_*.jsonl<br/>全部工具调用 · 参数 · 耗时 · 结果"]
        SPATIAL["spatial_*.jsonl<br/>地块注意力追踪（研究用，不回馈智能体）"]
        SAVES["存档<br/>MCP 自动存档 0_MCP_* / AutoSave_*"]
        TEL["telemetry → Convex 同步<br/>web/ 成绩站点与复盘"]
    end

    DSH -->|stdio JSON-RPC 工具调用| SRV
    CLI -->|stdio JSON-RPC 工具调用| SRV
    SRV --> PIPE
    PIPE -->|授权预检：门禁判定| GOV
    GOV -->|council 决议 + hash-bound ActionIntent| PIPE
    PIPE -->|执行（唯一写者）| GS
    GS -->|build_* 生成 Lua 源码| LUA
    LUA --> CONN
    CONN -->|"二进制帧 [len][tag][CMD]"| FT
    FT -->|execute_read| GC
    FT -->|execute_write| IG
    GC --> ENG
    IG -->|RequestOperation 规则校验后| ENG
    ENG -->|"print() 输出流"| FT
    FT -->|O<NUL>context: 行| CONN
    CONN -->|收集至 ---END--- 哨兵| LUA
    LUA -->|parse_* → dataclass| GS
    GS -->|narrate_* → 叙述文本| PIPE
    PIPE -->|成功观测自动记录| BE
    DERIV -->|规则命中| BE
    GOV -->|同源事件日志| BE
    FC -->|simulation 分支| BE
    GPH -->|world_entity| BE
    PIPE -->|原始全文| RF
    RF -->|压缩后的模型面结果| DSH
    RF -->|压缩后的模型面结果| CLI
    BE -->|镜像事件| BELIEFS
    BE -->|遥测镜像| TEL
    PIPE --> LOG
    PIPE --> SPATIAL
    GS -->|end_turn 日记快照| DIARY
    GS --> SAVES
    SAVES -->|读档 / 自动恢复| GS
```

**读路径（查询）**：`DSH → SRV → PIPE → GS → LUA(构建) → CONN → FT → GameCore → 引擎`，返回路径 `引擎 → FT → CONN → LUA(解析) → GS(叙述) → PIPE → RF(压缩) → DSH`。

**写路径（动作）**：`DSH → SRV → PIPE(授权预检：必须持有已批准 ActionIntent) → GS → LUA → CONN → FT → InGame(规则校验) → 引擎`，`execute_mutation` 保证恰好发送一次，结果未知时抛 `MutationOutcomeUnknownError`，先读回验证再重试。

---

## 2. 一次工具调用的完整旅程（时序）

以 `get_units` 为例，查询工具遵循"构建 → 执行 → 解析 → 叙述"四步模式：

```mermaid
sequenceDiagram
    autonumber
    participant Agent as LLM 智能体 (DSH)
    participant MCP as server/ 包
    participant PIPE as pipeline._logged
    participant GS as game_state.py
    participant LQ as lua/ 构建+解析
    participant CONN as connection.py
    participant FT as FireTuner :4318
    participant Civ as 文明 VI 引擎

    Agent->>MCP: MCP 工具调用 get_units (JSON-RPC)
    MCP->>PIPE: 进入 _logged 管道
    PIPE->>PIPE: 授权预检（belief_mode 判定 / 治理门禁）
    alt 需要治理路由
        PIPE->>PIPE: route_belief_decision 消费已批准 ActionIntent<br/>hash-bound 校验失败则拒绝，不触碰游戏
    end
    PIPE->>GS: gs.get_units()
    GS->>LQ: build_units_query()
    LQ-->>GS: Lua 源码字符串
    GS->>CONN: execute_read(lua_code)（GameCore）
    CONN->>FT: 二进制帧 [4B长度][4B tag=3][CMD:8:code<NUL>]
    FT->>Civ: 在 GameCore 状态执行 Lua
    Civ-->>FT: print() 管道分隔输出行
    FT-->>CONN: O<NUL>GameCore_Tuner: UNIT|warrior|...|---END---
    CONN->>CONN: 收集至哨兵，剥离 O<NUL> 前缀
    CONN-->>GS: list[str] 行
    GS->>LQ: parse_units_response(lines)
    LQ-->>GS: list[UnitInfo] dataclass
    GS->>GS: narrate_units() → 人类可读叙述文本
    PIPE->>PIPE: 自动记录 Observation（规范化事实 + result_ref 指纹）<br/>derivation 规则尝试派生信念/预测
    PIPE->>PIPE: result_filter 压缩超大结果（模型面副本）
    PIPE-->>MCP: 压缩后的叙述文本
    MCP-->>Agent: MCP 工具结果
    Note over PIPE: _logged 同时写 log_*.jsonl（计时/结果）<br/>与 spatial_*.jsonl（地块注意力，try/except 不阻断）
```

---

## 3. 每回合业务循环与门禁

回合级数据流：回合强制入口 → 威胁评估 → 决策门禁 → 执行 → 回合简报 → 推进：

```mermaid
flowchart TD
    A["回合开始"] --> B["get_game_overview<br/>唯一强制入口 · 返回 RUNTIME POLICY<br/>+ enforce 模式下治理快照"]
    B --> C["get_barbarian_overview<br/>营地 = 刷兵源头，优先清剿"]
    C --> D{"有营地 / 蛮族单位？"}
    D -->|是| E["get_combat_estimate<br/>→ assess_route_combat_risk<br/>（完整量化评估才允许改路由信念）"]
    E --> F["按需查询 get_units / get_map_area / get_cities<br/>原始结果不入信念，只入遥测"]
    D -->|否| F
    F --> G{"该动作是否需治理门禁？"}
    G -->|是| H["治理流程：get_governance_brief<br/>→ upsert_strategic_goal<br/>→ submit_governance_proposal<br/>→ review_governance_proposal（critic）<br/>→ resolve_governance_council<br/>→ route_belief_decision"]
    H --> I["fast / verify_then_fast / slow 路由"]
    I -->|verify_then_fast| J["先执行 intent 指定的证据查询<br/>参数与 required_facts 必须精确匹配"]
    J --> K["执行 MCP 动作（execute_mutation 单一写者）"]
    I -->|fast| K
    I -->|slow| L["慢审 → cancel_routed_action 或重提议<br/>绝不绕过门禁"]
    K --> M["自动验证追踪：成功动作 → Observation<br/>Outcome 链接 decision_id<br/>record_action_verification 收口"]
    M --> N["material 新证据 / 动作结果 / 上下文恢复<br/>→ get_turn_brief（决策上下文）"]
    G -->|否| K
    N --> O["skip_remaining_units → end_turn"]
    O --> P["end_turn 机器：阻塞项解析 → 快照 diff →<br/>威胁扫描 → 胜利临近检查 → 五段反思日记"]
    P --> A
```

**硬规则**：`get_game_overview` 读取成功前不得执行动作或 `end_turn`；返回 `BELIEF_GATE_REQUIRED` 时按精确 intent 完成路由后只重试一次；`bypassed` 模式不调用路由工具。

---

## 4. 治理控制面数据流（提案 → 决议 → 执行）

治理层与信念引擎共用同一事件日志，不另设状态存储；各部委只能提交建议，不能写游戏：

```mermaid
flowchart LR
    SNAP["typed GameState 快照<br/>GameOverview / CityInfo / UnitInfo / CivInfo"]
    SNAP --> ENT["world_entity / 关系 / 指标"]
    ENT --> GOAL["upsert_strategic_goal<br/>国家目标"]
    GOAL --> PROP["部委 submit_governance_proposal<br/>约束 · 预算锁 · 收益/成本 · 机会成本<br/>+ 显式 action_intents"]
    PROP --> CRIT["review_governance_proposal<br/>critic：agree / agree_with_conditions / object<br/>object 必须引用 Observation 或给出替代方案"]
    CRIT --> COUNCIL["resolve_governance_council<br/>固定顺序：硬约束 → critic 条件 →<br/>预算锁 → 战略优先级 → Pareto 支配 →<br/>机会成本（无加权总分）"]
    COUNCIL --> ROUTE["route_belief_decision<br/>hash-bound ActionIntent + evidence_requirements"]
    ROUTE -->|authorized| WRITER["单一游戏写者<br/>execute_mutation 通道"]
    WRITER --> OUT["Outcome + Observation<br/>+ 信念复核"]
    OUT -->|slow 审失效 / 重试不理性| CANCEL["cancel_routed_action<br/>追加 cancelled 未执行 Outcome"]
    OUT -->|客户端断开 / outcome_unknown| VERIFY["record_action_verification<br/>读回验证后收口"]
```

---

## 5. 持久化数据流总表

| 数据 | 文件（`~/.civ6-mcp/`） | 生产者 | 消费者 | 用途 |
|---|---|---|---|---|
| 信念事件日志 | `beliefs/belief_{civ}_{seed}.jsonl` | `pipeline._logged` / 信念工具 / derivation | `get_belief_trace` / coverage 审计 / dashboard | 世界模型与预测评分，append-only + 墓碑 |
| 日记 | `diary_*.jsonl` + `_cities` | `end_turn` | `get_diary` 上下文恢复 | 每回合快照 + 五段反思 |
| 工具日志 | `log_*.jsonl` | `_logged` 钩子 | 分析 / 复盘 | 权威行为记录（含原始结果全文） |
| 空间注意力 | `spatial_*.jsonl` | `_logged` 钩子 | 传感器效应研究 | 不回流智能体 |
| 存档 | `0_MCP_*` / `AutoSave_*` | autosave / 手动 | 崩溃恢复 / 读档（epoch 作废旧授权） | 恢复点 |
| 遥测 | telemetry → Convex | 信念事件镜像 | `web/` 成绩站点 | 跨游戏分析 |
