# map/ — 对局地图数据快照

本目录是《文明 VI》对局 **`ai-civ6-map-01`**（游戏身份 `sumeria_-1162067840`，我方苏美尔）
在 **第 141 回合**的地图数据导出，用于离线查看、绘图与复盘。

## 文件

| 文件 | 内容 |
|---|---|
| `summary.json` | 摘要：对局 id、当前回合、地图尺寸、11 名玩家、我方 8 座城市及人口 |
| `mapstatic_turn141.json` | 静态地图：一次性完整 dump（地形、初始归属、初始道路、城市、玩家表） |
| `mapturns_turn141.jsonl` | 第 141 回合单回合快照（城市列表 + 归属/道路增量） |
| `mapturns_all.jsonl` | 本局**全部回合**快照合并（按 `turn` 去重排序，覆盖第 2–141 回合，共 138 回合） |

## 数据格式

`mapstatic_turn141.json`（来源：`src/civ_mcp/map_capture.py` 的 `mapstatic_*` 输出）

```jsonc
{
  "gridW": 60, "gridH": 38,          // 地图尺寸（60×38 = 2280 格）
  "terrain": [...],                   // 扁平地形数组
  "initialOwners": [ -1, 0, ... ],    // 每格归属玩家 id（-1 = 无主）
  "initialRoutes": [ -1, ... ],       // 每格道路
  "initialCities": [ {"x":14,"y":17,"pid":0,"pop":9,"name":"LOC_CITY_NAME_URUK"}, ... ],  // 37 座
  "players": [ {"pid":0,"civ":"CIVILIZATION_SUMERIA"}, ... ],                              // 11 名
  "initialTurn": 141
}
```

`mapturns_*.jsonl`：每行一个回合对象，`{"turn": N, "cities": [...], ...}`，
包含该回合的城市（坐标/归属/人口/名称）与归属、道路等增量。

坐标约定：`x` 越大越靠东，`y` 越大越靠南（与 `docs/agent-turn-loop.md` 一致）。

## 当前局面（来自 `summary.json`）

- 我方：苏美尔，8 城 —— 乌鲁克(14,17)、基什(24,18)、乌尔(27,16)、尼普尔(20,17)、
  西帕尔(18,20)、埃利都(11,18)、阿达卜(19,13)、拉拉克(13,21)
- 对手/城邦（11 名玩家）：加拿大、巴西、瑞典、卡霍基亚、库马西、桑给巴尔、
  巴比伦、拉帕努伊、墨西哥城、自由城市
- 地图：60×38，共 2280 格

## 这些文件是怎么来的

游戏过程中 MCP 的 `map_capture` 模块会把地图 dump 写到 `~/.civ6-mcp/`：

```
~/.civ6-mcp/mapstatic_<civ>_<seed>_<session>.json
~/.civ6-mcp/mapturns_<civ>_<seed>_<session>.jsonl
```

本目录是其中**本局最新 session**（`remnant-mahogany-chronicle-82`，导出时间 2026-09-14 03:30）
的静态地图与快照，并把本局所有 session 的回合快照合并成 `mapturns_all.jsonl`。

导出方式（可重复执行）：

```bash
cd civ6-mcp && mkdir -p map && python3 - <<'PY'
import json, glob, os, shutil
SRC, DST, GAME = os.path.expanduser("~/.civ6-mcp"), "map", "sumeria_-1162067840"
statics = sorted(glob.glob(f"{SRC}/mapstatic_{GAME}_*.json"), key=os.path.getmtime)
turns   = sorted(glob.glob(f"{SRC}/mapturns_{GAME}_*.jsonl"), key=os.path.getmtime)
shutil.copy2(statics[-1], f"{DST}/mapstatic_turn141.json")
shutil.copy2(turns[-1],   f"{DST}/mapturns_turn141.jsonl")
merged = {}
for p in turns:
    for line in open(p, encoding="utf-8", errors="replace"):
        try: rec = json.loads(line)
        except Exception: continue
        if isinstance(rec, dict) and "turn" in rec: merged[int(rec["turn"])] = rec
with open(f"{DST}/mapturns_all.jsonl", "w", encoding="utf-8") as fh:
    for t in sorted(merged): fh.write(json.dumps(merged[t], ensure_ascii=False) + "\n")
print("merged turns:", len(merged))
PY
```

## 对局存档

对局本身仍在存档里（未包含在本目录）：

```
~/Library/Application Support/Sid Meier's Civilization VI/Sid Meier's Civilization VI/Saves/Single/0_MCP_0141.Civ6Save
```

说明：第 141 回合（世界议会回合）在本机遇到引擎级卡死（详见 `docs/agent-recovery.md` 记录的
JobSet/TBB 生命周期缺陷），因此快照停在该回合。
