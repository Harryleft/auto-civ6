# 对局复盘归档：2026-09-14 america_-1105557637

本目录是 **2026-09-14** 一次本机实测的完整归档：用本仓库的 MCP 服务驱动《文明 VI》自动推进，
从**回合 105 打到 175**，中途遭遇引擎崩溃与多类卡点，最终停在回合 175。

- 对局 id：`america_-1105557637`
- 我方：**美国 / 西奥多·罗斯福**，国王难度，标准速度，风云变幻规则集
- 起止：**回合 105（得分 132）→ 回合 175（得分 131）**
- 峰值：回合 129 得分 **150**；随后被**苏美尔**夺走华盛顿与费城，只剩波士顿 1 城，得分一度跌到 114
- 结果：**未分出胜负**，差 325 回合

---

## 目录说明

| 目录 | 内容 |
|---|---|
| `reports/` | 四份报告，**建议先读 `复盘.md` 与 `二次审核.md`** |
| `tools/` | 本次为驱动对局编写的 12 个脚本 + 3 个调用参数文件 |
| `harness/` | 我这一侧（守护进程／执政官／总控）的运行日志 |
| `mcp/` | **MCP 服务自己写的对局日志**——真正的游戏日志，见下 |
| `data/` | 地图解码产物、可视化 HTML、建局配置字符串 |

---

## 日志怎么读（`mcp/` 是重点）

`mcp/` 下的文件按 `类型_对局id_会话名` 命名。本次共出现 9 个守护进程会话
（`lone-flax-forge-32`、`remnant-vermil-minaret-31`、`azure-vermil-palisade-60` …），
每次进程重启开启一个新会话。

| 前缀 | 含义 |
|---|---|
| `log_*.jsonl` | **每次 MCP 工具调用的完整记录**（最接近"游戏日志"） |
| `beliefs_*.jsonl` | 信念引擎账本（每个会话一份） |
| `belief_*.jsonl` | 信念引擎主账本（跨会话，3.6 MB，最大） |
| `diary_*.jsonl` | 日记：每回合的决策记录 |
| `diary_*_cities.jsonl` | 城市逐回合快照 |
| `mapstatic_*.json` | 地图静态 dump（地形／归属／道路／城市／玩家） |
| `mapturns_*.jsonl` | 逐回合归属与道路增量 |
| `spatial_*.jsonl` | 空间注意力数据 |

`harness/` 里：

| 文件 | 含义 |
|---|---|
| `_play.log` | 自动执政官日志。**注意：只剩最后 10 行**——`supervise.py` 的 `start_daemon()` 每轮会删除重建它，70 回合的完整记录因此丢失（这是本次的一个失误） |
| `_supervise.log` | 总控日志，保留了完整的轮次与恢复记录（32 行） |
| `_daemon.log` | 守护进程日志，最后一次运行（18 行） |
| `_out.jsonl` | 工具调用结果（16 条，48 KB） |
| `_cmd.jsonl` | 工具调用请求（16 条） |
| `_tools.txt` | MCP 暴露的 112 个工具名 |

> 想要完整的逐回合记录，请以 `mcp/log_*.jsonl` 和 `mcp/diary_*.jsonl` 为准，
> 它们不受 `harness/` 那个删除 bug 影响。

---

## 本次发现的问题

### 仓库侧（详见 `reports/二次审核.md`）

| # | 文件 | 问题 |
|---|---|---|
| 1 | `web/src/lib/terrain-colors.ts` | `FEATURE_OVERLAY_COLORS` 地貌 ID 映射错误（`0/1` 写成 FOREST/JUNGLE，实际是 FLOODPLAINS/ICE） |
| 2 | 同上 | `ROAD_COLORS` 标签错位且缺索引 3（`ROUTE_MODERN_ROAD`），而它是快照里占比最大的道路 |
| 3 | `web/src/lib/diary-types.ts` | `DIPLO_STATE_NAMES` 漏了 `DECLARED_FRIEND`，索引 2 起整体前移一位；外交面板会把"友好"显示成"中立" |
| 4 | `docs/agent-recovery.md` | 写"保留最新 5 个"MCP 存档，代码传 8，实机剩 9 |

> 根因是**「手写数字 ID 表」这个模式本身**：游戏数据库的 ID 会因资料片／DLC 加载顺序整体偏移。
> 本仓库已有 `map/` 真实快照，建议加一个用快照校验 ID 表的 CI 测试。

### 运行环境侧（详见 `reports/复盘.md`、`reports/SETUP.md`）

1. **游戏窗口失焦会暂停游戏核心** —— 握手与状态枚举正常，但 `GameCore_Tuner`/`InGame` 的 Lua 完全不执行。
   仓库文档未记录此现象。
2. **世界议会会让 `end_turn` 永久停下**，而 MCP 是串行架构，挂住后无法自恢复。
   正解是用 `resolution_type` 作持久键**每回合预注册投票器**。
3. **`MaxJobThreads` 的缓解措施从未生效** —— 游戏启动时会重写 `AppOptions.txt`，
   必须每次拉起游戏前重新写入（仓库 `_ensure_job_thread_cap()` 的做法）。
4. FireTuner 经不起反复握手；连续几十次连接/断开后只能重启游戏。

---

## 已知缺陷（本次自写工具，未修）

| 脚本 | 缺陷 |
|---|---|
| `play.py` | 排产表 `PROD_PRIORITY`/`WAR_PRIORITY` **没有 `UNIT_SETTLER`**，执政官无法造开拓者 → 全程 1 座城 |
| `play.py` | 未处理「选择生产项目」弹窗 |
| `play.py` | `end_turn` 超时设成 1200 秒，卡一次白等 20 分钟 |
| `supervise.py` | `recover()` 无条件杀游戏重启；且不重新固定 `MaxJobThreads` |
| `supervise.py` | `start_daemon()` 会删除 `_play.log`，导致历史日志丢失 |
| `send.py` | 仍在读已改名的 `focused` 字段（daemon 现在写 `focus_failures`） |

---

## 复现方式

前置：文明 VI（含风云变幻）+ Civ 6 SDK，`AppOptions.txt` 里 `EnableTuner 1`。

```powershell
# 1) 环境
cd <repo>
uv sync
uv pip install 'civ6-belief-engine[launcher-windows]' winrt-Windows.Globalization

# 2) 启动游戏并进入一局，确认 127.0.0.1:4318 在监听

# 3) 常驻 MCP 守护进程
$env:CIV_MCP_BELIEF_MODE='observe'
uv run python sessions/2026-09-14-america-run/tools/mcp_daemon.py

# 4) 另开一个终端：自动执政官
uv run python sessions/2026-09-14-america-run/tools/play.py --max-turns 40

# 5) 或交给总控（含崩溃自动恢复）
uv run python sessions/2026-09-14-america-run/tools/supervise.py 12 40
```

> 注意：`mcp_daemon.py` 与 `play.py` 之间的路径常量写死在 `S:\vibe_coding\civ6-map-analysis`，
> 迁移到别处需要改这两个脚本顶部的 `HERE`。

---

## 结论

链路（FireTuner → MCP → 自建守护进程 → 自动执政官 → 总控恢复）**跑通了**，
但**只完成了 70/395 回合**，且把大部分时间花在了排查基础设施上：

| 阶段 | 占比 |
|---|---|
| 拉仓库、摸环境 | ~1 h |
| 读取地图数据 | ~40 min |
| 打通 FireTuner 与控制链路 | ~1.5 h |
| **搭建无人值守流水线（含 8 个卡点）** | **~2.5 h** |
| **实际推进对局** | **约 40 min** |

**最大的教训**：应在发现"目标地图无法载入"时立刻求证并调整目标，
而不是在别人的存档上继续搭自动化；以及**先手动打 5 回合验证策略，再谈无人值守**。
