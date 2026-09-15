# auto-civ6 本机部署与运行记录

时间：2026-09-14　工作目录：`S:\vibe_coding`

## 结论速览

| 目标 | 状态 |
|---|---|
| 拉取仓库 | ✅ `S:\vibe_coding\auto-civ6`（`main` @ `9384754`） |
| 读取地图数据 | ✅ 解码 2280 格 + 138 回合快照，见 `report.md` / `civ6_map.html` |
| 在本机运行对局 | ✅ Civ VI 已进入对局（回合 105），FireTuner `127.0.0.1:4318` 正常 |
| 载入 `ai-civ6-map-01` 那张地图 | ❌ 不可能——仓库里只有建局**配置**，没有可游玩的**存档** |

## 一、仓库与数据

- 仓库默认分支 `main`，克隆后又前进了一个提交（`9384754`「上传对局 ai-civ6-map-01 的原始地图/配置文件」），已 fast-forward。
- `map/` 是第 141 回合的导出快照：`mapstatic_turn141.json`（60×38 地形/归属/道路/37 城/11 玩家）、
  `mapturns_all.jsonl`（第 2–141 回合，138 回合城市与归属增量）。
- `map/original/` 是**建局配置**：`ai-civ6-map-01.Civ6Cfg` 与 `AutoConfigGame_01.Civ6Cfg`，
  SHA256 与 `SHA256SUMS.txt` 一致（已校验）。
- 对应存档 `0_MCP_0141.Civ6Save` **未上传**（`map/original/README.md` 明确说明它留在原采集机上）。

### 已解码的建局参数（从 `.Civ6Cfg` 明文读出）

| 项 | 值 |
|---|---|
| 地图 | `LOC_MAP_PANGAEA`（盘古大陆，`Pangaea.lua` / `StandardMaps`） |
| 规则集 | `RULESET_EXPANSION_2`（风云变幻） |
| 速度 | Quick（快速） |
| 起始时代 | Ancient（远古） |
| 我方 | `CIVILIZATION_SUMERIA` / `LEADER_GILGAMESH`（吉尔伽美什） |
| 对手 | 加拿大 `LEADER_LAURIER`、巴西 `LEADER_PEDRO`、瑞典 `LEADER_KRISTINA` |
| 启用模式/Mod | BarbarianClansMode、TreeRandomizer（科技与市政随机）、ScoutCat、Aztec、WarMachine、ColdWar、Pirates、BlackDeath、CivRoyale、AncientRivals、ReligiousCombat、Napoleon 等 |

这些与地图快照里的玩家列表完全吻合，可确认配置就是那一局。

## 二、为什么 `ai-civ6-map-01` 载不进去

`.Civ6Cfg` 不是存档容器，三条路都试过：

1. **FrontEnd API**（仓库 `load_save_from_frontend` → `Network.LoadGame`）返回成功但游戏毫无反应；
   把它改名成 `.Civ6Save` 再试，同样无效。
   （文件头对比：`.Civ6Cfg` 偏移 8 为 `0x12`，`.Civ6Save` 为 `0x22/0x23`；存档在偏移 28 有可读的 `GAME` 标记，配置没有。）
2. **游戏内「加载游戏」列表**：只列出 `AutoSave_*`，没有 `ai-civ6-map-01`。
3. **「创建游戏」界面**：只有「高级设置」，没有载入配置入口。

**要真正玩到那张地图，需要仓库作者补传 `0_MCP_0141.Civ6Save`。**

## 三、本机实际改动

| 文件 | 改动 | 备份 |
|---|---|---|
| `%LOCALAPPDATA%\Firaxis Games\Sid Meier's Civilization VI\AppOptions.txt` | `EnableTuner 0→1`、`PlayIntroVideo 1→0` | 同目录 `AppOptions.txt.bak-before-tuner` |
| `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` | 插入 `mcp-civ6`（`@deepseek-ai/dsh-mcp-client`） | 同目录 `cordis.patch.yml.bak-original` |
| `Documents\My Games\Sid Meier's Civilization VI\Saves\Single\` | 放入 `ai-civ6-map-01.Civ6Cfg`、`AutoConfigGame_01.Civ6Cfg` | 直接删除即可 |
| `auto-civ6` 仓库 | `uv sync`；`uv pip install civ6-belief-engine[launcher-windows] winrt-Windows.Globalization` | —— |

`AppOptions.txt` 恢复：把 `EnableTuner` 改回 `0`（成就统计会随之恢复）。
DSH 恢复：把 `cordis.patch.yml` 内容改回 `[]`，或还原 `.bak-original`。

## 四、本机环境要点（Windows 特有）

- Civ VI：`D:\SteamLibrary\steamapps\common\Sid Meier's Civilization VI`，版本 **1.0.12.68**，
  已含 Gathering Storm。**未安装 Civ 6 SDK**，但 `EnableTuner 1` 后游戏自身即监听 4318。
- 设置文件在 `%LOCALAPPDATA%\Firaxis Games\...\AppOptions.txt`，**不在** `Documents\My Games\...`。
- 存档目录：`Documents\My Games\Sid Meier's Civilization VI\Saves\Single\`（普通）、`\auto\`（自动），
  与仓库 `game_launcher.SINGLE_SAVE_DIR` / `SAVE_DIR` 常量一致。
- 仓库 `scripts/deepseek_harness` / `scripts/civ6_launch` **在本机用不了**：它们要求源码版 DSH
  checkout（`../deepseek-harness/apps/cli/lib/bin.js`），本机只有打包版桌面应用。
  因此改用 `~/.dsh/profiles/desktop/cordis.patch.yml` 挂载 MCP（`dsh --profile desktop --dump-config` 验证通过）。
- `scripts/launch_save.py` 是 **macOS 专用**（`import Quartz` / `screencapture` / `osascript`），Windows 上不可用。
- `git` 全局代理指向失效的 `socks5://127.0.0.1:10808`；直连 GitHub 也不稳定，
  可用 `127.0.0.1:7897`（本机 Clash 混合端口）：`git -c http.proxy=http://127.0.0.1:7897 fetch`。

## 五、Windows 下用 OCR 操作中文界面

仓库的 `game_launcher._ocr_winrt` 用 `OcrEngine.try_create_from_user_profile_languages()`，
在本机选中的是**英文识别器**，中文菜单只能读成乱码（如 `illiIJiitEJ*`），所以仓库自带的
OCR 导航在中文界面上找不到任何按钮。

本目录的 `gui.py` 改成显式使用 `zh-Hans-CN` 引擎，并在匹配前去掉 OCR 插入的字间空格
（`单 人 模 式` → `单人模式`），于是可以稳定驱动中文菜单。实际界面文字：

```
主菜单：单人模式 / 多人模式 / 游戏选项 / 额外内容 / 教程 / 测试程序 / 地图生成器 / 退回到桌面
单人模式子菜单：继续游戏 / 加载游戏 / 创建游戏 / 开始游戏
载入界面：自动保存（筛选）/ 以名称排序 / 加载游戏（按钮）/ 返回
```

> 注意：仓库 `_UI_LABEL_CANDIDATES` 里写的是「自动**存档**」，本机实际是「自动**保存**」，
> 所以仓库的 autosave 标签点击在本机会落空。

## 六、本目录脚本

| 脚本 | 用途 |
|---|---|
| `decode_map.py` | 解析 `auto-civ6/map/` 的全部地图数据，生成报告与可视化 |
| `load_map_game.py` | 一次性连接 FireTuner 查看 Lua 状态 / 调仓库的 FrontEnd API 读档 |
| `gui.py` | 中文界面 OCR 工具：`ocr` 读屏、`click <文字>` 点击、`focus` 切前台 |
| `nav_save.py` | 调仓库 `_navigate_to_save_sync` 做 OCR 菜单导航（本机因 OCR 语言问题不可用，保留作参考） |
| `ocr_screen.py` | 抓屏 OCR 小工具 |
| `probe_focus.py` | 连接后先切前台再查 GameCore/InGame，验证「失焦暂停」结论 |
| `read_state.py` | 通过 FireTuner 读局面（早期手写 Lua 版，仅作参考） |

## 七、两个必须知道的运行时坑（排查了很久）

### 1. 游戏窗口失焦 → 游戏核心暂停 → 所有 MCP 调用超时

**Civ VI 在窗口失去焦点时会暂停游戏核心。** 此时：

- 握手正常，125 个 Lua 状态都能列出；
- 但 `GameCore_Tuner` / `InGame` 里的 Lua **完全不执行**，表现是
  `CommandTimeoutError: … without receiving the "---END---" completion sentinel`；
- 前台的主菜单/前端状态仍然能执行 Lua，容易误判成「tuner 坏了」。

证据：`probe_focus.py` 在连接后主动把游戏切回前台，同一份代码立刻成功：

```
focus_game -> True
GameCore attempt1: OK -> ['GC_PONG']
InGame   attempt1: OK -> ['GC_PONG']
```

**所以跑对局时必须让文明 VI 保持前台。** 在 GUI 里让智能体打的时候，
发完指令就切到游戏窗口并留在那里，不要一直盯着 DSH 页面——浏览器窗口是前台时，
游戏核心是停的，每一次 `mcp__civ6__*` 调用都会超时。

### 2. FireTuner 经不起反复握手

每次「连接 → 断开」都会在 4318 留下 `TIME_WAIT`。连续几十次之后，tuner 会退化成
「握手成功、状态枚举返回 0」或「状态能列出但执行无输出」，且**不会自愈**，
必须重启游戏进程（与 `docs/agent-startup.md` 记录的一致）。

排查过程中我用脚本反复 `--list` 轮询，把 tuner 打坏过两次，两次都只能重启游戏。
**结论：对局期间只允许 MCP 这一个客户端，不要并行跑任何探测脚本。**

## 八、当前状态与下一步

- 文明 VI 正在运行，已载入 `AutoSave_0105`（**回合 105/500**，罗斯福 / 美利坚帝国），
  FireTuner 双状态齐全且执行正常（已用 `probe_focus.py` 验证）。
- 要在 DSH 里用 AI 打这一局：**新开一个会话**，会看到 `mcp__civ6__*` 工具，
  第一条调 `get_game_overview`；发完指令后**把焦点切回游戏窗口**。
- 若要让 AI 自己连续打到分出胜负，需要模型凭证（见下）。

### 无头智能体循环与凭证

仓库设计的无人值守循环（`scripts/civ6_agent` → `scripts/deepseek_harness`）在本机走不通，
因为 `scripts/deepseek_harness` 要求源码版 DSH checkout。等价做法是用本机打包版的 `dsh`：

```powershell
# 1) 从 headless 模板建一个专用 profile
dsh --profile civ6agent --from-default-profile headless --dump-config

# 2) 在仓库目录下，叠加仓库自己的两个 overlay 启动（process.cwd() 决定 MCP 的工作目录）
cd S:\vibe_coding\auto-civ6
dsh --profile civ6agent `
  --patch integrations\deepseek-harness\civ6.cordis.yml `
  --patch integrations\deepseek-harness\civ6-agent.cordis.yml `
  "请完成恰好 1 个回合……"
```

已验证：MCP 能启动、能连 FireTuner（125 状态）、能加载工具列表（走到 `ListToolsRequest`）。
**唯一缺的是 LLM 凭证**——`dsh` CLI 报
`MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"`。
桌面版走的是云账号会话（`~/.dsh/.credentials.yaml` 里只有 `client-connection/browser-session`），
CLI 用不了。要么在启动环境里 `export DEEPSEEK_API_KEY`，要么改用 GUI 会话（凭证已可用）。
