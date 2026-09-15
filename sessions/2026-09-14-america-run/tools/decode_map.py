#!/usr/bin/env python3
"""解析 auto-civ6 仓库 ``map/`` 目录下的《文明 VI》对局地图快照。

数据来源：``auto-civ6/map/`` —— 对局 ``ai-civ6-map-01``（游戏身份 ``sumeria_-1162067840``，
我方苏美尔）第 141 回合的导出。

每格在 ``mapstatic_*.json`` 的 ``terrain`` 扁平数组里占 6 个字段：

    [terrain, feature, hills, river, coastal, resource]

索引约定 ``idx = y * gridW + x``（与 web/src/components/strategic-map.tsx 一致）。

产出（写入 OUT 目录）：
    map_decoded.json  每格的解码结果
    report.md         中文分析报告
    civ6_map.html     自包含的 SVG 六边形地图（可用浏览器打开）
"""

from __future__ import annotations

import collections
import json
import math
import os

REPO = r"S:\vibe_coding\auto-civ6"
SRC = os.path.join(REPO, "map")
OUT = os.path.dirname(os.path.abspath(__file__))

# ── 地形 ID（来自 web/src/lib/terrain-colors.ts 的 GameInfo.Terrains 索引序）──
TERRAIN_NAMES: dict[int, tuple[str, str]] = {
    0: ("GRASS", "草原"),
    1: ("GRASS_HILLS", "草原丘陵"),
    2: ("GRASS_MOUNTAIN", "草原山脉"),
    3: ("PLAINS", "平原"),
    4: ("PLAINS_HILLS", "平原丘陵"),
    5: ("PLAINS_MOUNTAIN", "平原山脉"),
    6: ("DESERT", "沙漠"),
    7: ("DESERT_HILLS", "沙漠丘陵"),
    8: ("DESERT_MOUNTAIN", "沙漠山脉"),
    9: ("TUNDRA", "苔原"),
    10: ("TUNDRA_HILLS", "苔原丘陵"),
    11: ("TUNDRA_MOUNTAIN", "苔原山脉"),
    12: ("SNOW", "雪原"),
    13: ("SNOW_HILLS", "雪原丘陵"),
    14: ("SNOW_MOUNTAIN", "雪原山脉"),
    15: ("COAST", "浅海"),
    16: ("OCEAN", "远洋"),
}

# 地形配色，与 web/src/lib/terrain-colors.ts 保持一致
TERRAIN_COLORS: dict[int, str] = {
    0: "#5a8a4a", 1: "#4a7a3a", 2: "#6e6e6e",
    3: "#9a8a5a", 4: "#8a7a4a", 5: "#6e6e6e",
    6: "#c4a94a", 7: "#b49a3a", 8: "#6e6e6e",
    9: "#7a8a7a", 10: "#6a7a6a", 11: "#6e6e6e",
    12: "#d0d0d0", 13: "#b8b8b8", 14: "#6e6e6e",
    15: "#2a6a9c", 16: "#1a3a5c",
}

# ── 地貌 ID ────────────────────────────────────────────────────────────────
# 权威来源：按游戏数据库加载顺序拼接
#   Base/Assets/Gameplay/Data/Features.xml                        → 0–17
#   DLC/Expansion2/Data/Expansion1_Features.xml（R&F，仅新增 1 个） → 18
#   DLC/Expansion2/Data/Expansion2_Features.xml（GS，新增 12 个）   → 19–30
#
# 注意：仓库 web/src/lib/terrain-colors.ts 里的 FEATURE_OVERLAY_COLORS 与此
# **完全不符**（它把 0/1 标成 FOREST/JUNGLE）。已用两处独立证据交叉验证下面这张表：
#   1) 索引 19 的 46 格全部落在草原且 46/46 带河流 → 草原泛滥平原
#   2) 索引 20 的 19 格全部落在平原且 19/19 带河流 → 平原泛滥平原
#   3) 索引 1 的 526 格中 474 格在远洋 → 海冰（不可能是雨林）
#   4) 索引 18 的 24 格全部在浅海 → R&F 新增的 FEATURE_REEF
FEATURE_NAMES: dict[int, tuple[str, str]] = {
    0: ("FEATURE_FLOODPLAINS", "泛滥平原（沙漠）"),
    1: ("FEATURE_ICE", "海冰"),
    2: ("FEATURE_JUNGLE", "雨林"),
    3: ("FEATURE_FOREST", "森林"),
    4: ("FEATURE_OASIS", "绿洲"),
    5: ("FEATURE_MARSH", "沼泽"),
    6: ("FEATURE_BARRIER_REEF", "大堡礁"),
    7: ("FEATURE_CLIFFS_DOVER", "多佛白崖"),
    8: ("FEATURE_CRATER_LAKE", "火山口湖"),
    9: ("FEATURE_DEAD_SEA", "死海"),
    10: ("FEATURE_EVEREST", "珠穆朗玛峰"),
    11: ("FEATURE_GALAPAGOS", "加拉帕戈斯群岛"),
    12: ("FEATURE_KILIMANJARO", "乞力马扎罗山"),
    13: ("FEATURE_PANTANAL", "潘塔纳尔湿地"),
    14: ("FEATURE_PIOPIOTAHI", "皮奥皮奥塔希"),
    15: ("FEATURE_TORRES_DEL_PAINE", "百内国家公园"),
    16: ("FEATURE_TSINGY", "黥基·德·贝马拉哈"),
    17: ("FEATURE_YOSEMITE", "优胜美地"),
    18: ("FEATURE_REEF", "珊瑚礁"),
    19: ("FEATURE_FLOODPLAINS_GRASSLAND", "泛滥平原（草原）"),
    20: ("FEATURE_FLOODPLAINS_PLAINS", "泛滥平原（平原）"),
    21: ("FEATURE_GEOTHERMAL_FISSURE", "地热裂缝"),
    22: ("FEATURE_VOLCANO", "火山"),
    23: ("FEATURE_VOLCANIC_SOIL", "火山土"),
    24: ("FEATURE_CHOCOLATEHILLS", "巧克力山"),
    25: ("FEATURE_DEVILSTOWER", "魔鬼塔"),
    26: ("FEATURE_GOBUSTAN", "戈布斯坦"),
    27: ("FEATURE_IKKIL", "伊克利"),
    28: ("FEATURE_PAMUKKALE", "棉花堡"),
    29: ("FEATURE_VESUVIUS", "维苏威火山"),
    30: ("FEATURE_WHITEDESERT", "白色沙漠"),
}

# 地貌覆盖层配色（半透明叠加在地形上）
FEATURE_COLORS: dict[int, str] = {
    0: "#aacccc",   # 沙漠泛滥平原
    1: "#ffffff",   # 海冰
    2: "#14501a",   # 雨林
    3: "#2c6b2c",   # 森林
    4: "#2a6aaa",   # 绿洲
    5: "#3a6a5a",   # 沼泽
    18: "#5fd0e6",  # 珊瑚礁
    19: "#aacccc", 20: "#aacccc",
    21: "#d0a060", 22: "#8a3a28", 23: "#8a6a4a",
    6: "#7fd8e8", 7: "#e8e8e8", 8: "#3a6a9c", 9: "#4a8ab0",
    10: "#d8d8d8", 11: "#3a9ab0", 12: "#c8c8c8", 13: "#4a8a6a",
    14: "#8a7a5a", 15: "#b0a080", 16: "#9a8a7a", 17: "#8a9a6a",
    24: "#8a6a4a", 25: "#8a7a6a", 26: "#a09070", 27: "#90a0a0",
    28: "#e0e0e0", 29: "#7a3a2a", 30: "#e0dcc8",
}

ROUTE_NAMES: dict[int, str] = {
    0: "古代道路",
    1: "古典道路",
    2: "工业铁路",
    3: "铁路",
}

# 城邦类型配色（web/src/lib/hex-geometry.ts）
CS_TYPE_COLORS: dict[str, str] = {
    "Scientific": "#4A90D9",
    "Cultural": "#9B59B6",
    "Militaristic": "#CA1415",
    "Religious": "#F9F9F9",
    "Trade": "#F7D801",
    "Industrial": "#FF8112",
}

# 每名玩家的显示名与配色（我方苏美尔用醒目的青色）
PLAYER_META: dict[int, tuple[str, str, str]] = {
    0: ("苏美尔", "CIVILIZATION_SUMERIA", "#00C2A8"),
    1: ("加拿大", "CIVILIZATION_CANADA", "#D64545"),
    2: ("巴西", "CIVILIZATION_BRAZIL", "#2E9E4F"),
    3: ("瑞典", "CIVILIZATION_SWEDEN", "#3B7DD8"),
    4: ("卡霍基亚", "CIVILIZATION_CAHOKIA", "#E8B33A"),
    5: ("库马西", "CIVILIZATION_KUMASI", "#B5651D"),
    6: ("桑给巴尔", "CIVILIZATION_ZANZIBAR", "#7FD1C1"),
    7: ("巴比伦", "CIVILIZATION_BABYLON", "#8E44AD"),
    8: ("拉帕努伊", "CIVILIZATION_RAPA_NUI", "#E67E22"),
    9: ("墨西哥城", "CIVILIZATION_MEXICO_CITY", "#95A5A6"),
    62: ("自由城市", "CIVILIZATION_FREE_CITIES", "#555555"),
}

# 苏美尔城市的官方中文名
CITY_ZH: dict[str, str] = {
    "URUK": "乌鲁克", "KISH": "基什", "UR": "乌尔", "NIPPUR": "尼普尔",
    "SIPPAR": "西帕尔", "ERIDU": "埃利都", "ADAB": "阿达卜", "LARAK": "拉拉克",
}


def city_label(raw: str) -> str:
    """LOC_CITY_NAME_URUK -> 乌鲁克 / URUK"""
    key = raw.replace("LOC_CITY_NAME_", "")
    return CITY_ZH.get(key, key)


def load_static() -> dict:
    with open(os.path.join(SRC, "mapstatic_turn141.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_summary() -> dict:
    with open(os.path.join(SRC, "summary.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_turns() -> list[dict]:
    turns: list[dict] = []
    path = os.path.join(SRC, "mapturns_all.jsonl")
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and "turn" in rec:
                turns.append(rec)
    turns.sort(key=lambda r: r["turn"])
    return turns


def decode_tiles(static: dict) -> list[dict]:
    """把扁平 terrain 数组解码成每格一个 dict。"""
    w, h = static["gridW"], static["gridH"]
    flat = static["terrain"]
    owners = static["initialOwners"]
    routes = static["initialRoutes"]
    tiles: list[dict] = []
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            base = idx * 6
            tiles.append({
                "x": x,
                "y": y,
                "idx": idx,
                "terrain": flat[base],
                "feature": flat[base + 1],
                "hills": flat[base + 2],
                "river": flat[base + 3],
                "coastal": flat[base + 4],
                "resource": flat[base + 5],
                "owner": owners[idx],
                "route": routes[idx],
            })
    return tiles


def build_stats(static: dict, tiles: list[dict], turns: list[dict]) -> dict:
    terrain_hist = collections.Counter(t["terrain"] for t in tiles)
    feature_hist = collections.Counter(t["feature"] for t in tiles)
    resource_hist = collections.Counter(t["resource"] for t in tiles)
    owner_hist = collections.Counter(t["owner"] for t in tiles)
    route_hist = collections.Counter(t["route"] for t in tiles)

    # 地貌 × 地形 交叉表 —— 用来交叉验证地貌 ID 语义
    feature_terrain: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    feature_river: collections.Counter = collections.Counter()
    for t in tiles:
        if t["feature"] >= 0:
            feature_terrain[t["feature"]][t["terrain"]] += 1
            if t["river"]:
                feature_river[t["feature"]] += 1

    # 资源 × 地形 交叉表
    resource_terrain: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for t in tiles:
        if t["resource"] >= 0:
            resource_terrain[t["resource"]][t["terrain"]] += 1

    land = sum(v for k, v in terrain_hist.items() if k not in (15, 16))
    water = sum(v for k, v in terrain_hist.items() if k in (15, 16))

    # 回合数统计
    turn_city_counts = [(r["turn"], len(r.get("cities", []))) for r in turns]
    all_cities: dict[str, dict] = {}
    for r in turns:
        for c in r.get("cities", []):
            key = c.get("name") or f"{c['x']},{c['y']}"
            prev = all_cities.get(key)
            if prev is None or c.get("pop", 0) >= prev.get("pop", 0):
                all_cities[key] = c

    return {
        "grid_w": static["gridW"],
        "grid_h": static["gridH"],
        "turn": static["initialTurn"],
        "land": land,
        "water": water,
        "terrain_hist": dict(terrain_hist.most_common()),
        "feature_hist": dict(feature_hist.most_common()),
        "resource_hist": dict(resource_hist.most_common()),
        "owner_hist": dict(owner_hist.most_common()),
        "route_hist": dict(route_hist.most_common()),
        "feature_terrain": {k: dict(v.most_common()) for k, v in sorted(feature_terrain.items())},
        "feature_river": dict(feature_river),
        "resource_terrain": {k: dict(v.most_common()) for k, v in sorted(resource_terrain.items())},
        "rivers": sum(1 for t in tiles if t["river"]),
        "coastal_tiles": sum(1 for t in tiles if t["coastal"]),
        "hills_flag": sum(1 for t in tiles if t["hills"]),
        "mountains": sum(1 for t in tiles if t["terrain"] in (2, 5, 8, 11, 14)),
        "turn_city_counts": turn_city_counts,
        "all_cities": sorted(all_cities.values(), key=lambda c: -c.get("pop", 0)),
        "turns": turns,
    }


def write_decoded(static: dict, tiles: list[dict]) -> str:
    payload = {
        "game": "sumeria_-1162067840",
        "turn": static["initialTurn"],
        "gridW": static["gridW"],
        "gridH": static["gridH"],
        "players": static["players"],
        "tiles": tiles,
    }
    path = os.path.join(OUT, "map_decoded.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def tname(tid: int) -> str:
    en, zh = TERRAIN_NAMES.get(tid, (f"UNKNOWN_{tid}", f"未知{tid}"))
    return f"{zh}（{en}）"


def write_report(static: dict, summary: dict, stats: dict) -> str:
    L: list[str] = []
    add = L.append

    add("# auto-civ6 地图数据解析报告")
    add("")
    add("对局 **`ai-civ6-map-01`**（游戏身份 `sumeria_-1162067840`），我方 **苏美尔**，"
        f"快照于 **第 {stats['turn']} 回合**。")
    add("")
    add(f"- 地图尺寸：**{stats['grid_w']} × {stats['grid_h']}** = "
        f"{stats['grid_w'] * stats['grid_h']} 格")
    add(f"- 陆地 {stats['land']} 格 / 水域 {stats['water']} 格"
        f"（陆地占比 {stats['land'] / (stats['grid_w'] * stats['grid_h']) * 100:.1f}%）")
    add(f"- 河流经过 {stats['rivers']} 格，丘陵 {stats['hills_flag']} 格，"
        f"山脉 {stats['mountains']} 格，沿海 {stats['coastal_tiles']} 格")
    add(f"- 玩家 {len(static['players'])} 名，城市 {len(static['initialCities'])} 座")
    add(f"- 回合快照覆盖第 {summary['turn_range'][0]}–{summary['turn_range'][1]} 回合，"
        f"共 {summary['merged_turns']} 回合")
    add("")

    # ── 玩家 ──
    add("## 玩家")
    add("")
    add("| pid | 文明 | 类型 | 领土格数 |")
    add("|---|---|---|---|")
    for p in static["players"]:
        pid = p["pid"]
        zh, _, _ = PLAYER_META.get(pid, (p["civ"], p["civ"], "#888"))
        kind = f"城邦（{p['csType']}）" if p.get("csType") else ("我方" if pid == 0 else "主要文明")
        add(f"| {pid} | {zh} | {kind} | {stats['owner_hist'].get(pid, 0)} |")
    add("")

    # ── 地形 ──
    add("## 地形分布")
    add("")
    add("| 地形 | ID | 格数 | 占比 |")
    add("|---|---:|---:|---:|")
    total = stats["grid_w"] * stats["grid_h"]
    for tid, n in stats["terrain_hist"].items():
        add(f"| {tname(tid)} | {tid} | {n} | {n / total * 100:.1f}% |")
    add("")

    # ── 地貌 ──
    add("## 地貌（Feature）分布")
    add("")
    add("> **重要**：仓库 `web/src/lib/terrain-colors.ts` 的 `FEATURE_OVERLAY_COLORS` "
        "把 0/1/2/3 标成了 FOREST/JUNGLE/MARSH/OASIS，**与游戏实际数据不符**。"
        "下表按游戏数据库加载顺序重建，依据见文末「地貌 ID 映射勘误」。")
    add("")
    add("| ID | 地貌（勘误后） | 格数 | 其中带河流 | 主要分布地形 |")
    add("|---:|---|---:|---:|---|")
    for fid, n in stats["feature_hist"].items():
        if fid < 0:
            continue
        if fid in FEATURE_NAMES:
            en, zh = FEATURE_NAMES[fid]
            name = f"{zh}（{en}）"
        else:
            name = f"ID {fid}（未确认）"
        tt = stats["feature_terrain"].get(fid, {})
        top = "、".join(
            f"{TERRAIN_NAMES.get(k, (str(k),))[0]} {v}"
            for k, v in sorted(tt.items(), key=lambda kv: -kv[1])[:3]
        )
        rv = stats["feature_river"].get(fid, 0)
        add(f"| {fid} | {name} | {n} | {rv} | {top} |")
    none_feature = stats["feature_hist"].get(-1, 0)
    add(f"| -1 | 无地貌 | {none_feature} | — | — |")
    add("")

    # ── 资源 ──
    add("## 资源分布")
    add("")
    add(f"共 {len([k for k in stats['resource_hist'] if k >= 0])} 种不同资源 ID，"
        f"占地 {total - stats['resource_hist'].get(-1, 0)} 格。")
    add("")
    add("> 仓库内没有任何 **资源 ID → 名称** 的映射表（游戏侧按 `RESOURCE_*` 字符串处理），"
        "本机也未找到可用于还原索引序的游戏数据库，故此处只报 ID。")
    add("")
    add("| 资源 ID | 格数 | 主要分布地形 |")
    add("|---:|---:|---|")
    for rid, n in stats["resource_hist"].items():
        if rid < 0:
            continue
        tt = stats["resource_terrain"].get(rid, {})
        top = "、".join(
            f"{TERRAIN_NAMES.get(k, (str(k),))[0]} {v}"
            for k, v in sorted(tt.items(), key=lambda kv: -kv[1])[:3]
        )
        add(f"| {rid} | {n} | {top} |")
    add("")

    # ── 道路 ──
    add("## 道路")
    add("")
    add("| route ID | 含义 | 格数 |")
    add("|---|---|---:|")
    for rid, n in stats["route_hist"].items():
        add(f"| {rid} | {ROUTE_NAMES.get(rid, '无道路' if rid == -1 else '未知')} | {n} |")
    add("")

    # ── 城市 ──
    add("## 城市（快照时点）")
    add("")
    add("| 城市 | 坐标 (x,y) | 归属 | 人口 |")
    add("|---|---|---|---:|")
    cities = sorted(static["initialCities"], key=lambda c: (c["pid"], -c["pop"]))
    for c in cities:
        pid = c["pid"]
        zh, _, _ = PLAYER_META.get(pid, (f"pid{pid}", "", "#888"))
        add(f"| {city_label(c['name'])} | ({c['x']}, {c['y']}) | {zh} | {c['pop']} |")
    add("")

    our = [c for c in static["initialCities"] if c["pid"] == 0]
    add(f"我方（苏美尔）共 {len(our)} 座城市，合计人口 {sum(c['pop'] for c in our)}，"
        f"平均 {sum(c['pop'] for c in our) / len(our):.1f}。")
    add("")

    # ── 回合时间线 ──
    add("## 回合时间线（城市数量变化）")
    add("")
    tcc = stats["turn_city_counts"]
    add(f"从第 {tcc[0][0]} 回合的 {tcc[0][1]} 座城市，到第 {tcc[-1][0]} 回合的 {tcc[-1][1]} 座城市。")
    add("")
    add("| 回合 | 城市总数 |")
    add("|---:|---:|")
    for turn, n in tcc:
        if turn % 10 == 0 or turn in (tcc[0][0], tcc[-1][0]):
            add(f"| {turn} | {n} |")
    add("")

    add("## 地貌 ID 映射勘误")
    add("")
    add("仓库 `web/src/lib/terrain-colors.ts` 中声明的映射是：")
    add("")
    add("```text")
    add("0=FEATURE_FOREST  1=FEATURE_JUNGLE  2=FEATURE_MARSH  3=FEATURE_OASIS")
    add("4=FEATURE_FLOODPLAINS  5=FLOODPLAINS_GRASSLAND  6=FLOODPLAINS_PLAINS")
    add("```")
    add("")
    add("本机游戏数据（Gathering Storm 已装）显示的真实加载顺序是：")
    add("")
    add("```text")
    add("Base/Assets/Gameplay/Data/Features.xml                   → 索引 0–17")
    add("DLC/Expansion2/Data/Expansion1_Features.xml  (R&F 新增)  → 索引 18（仅 FEATURE_REEF）")
    add("DLC/Expansion2/Data/Expansion2_Features.xml  (GS 新增)   → 索引 19–30")
    add("```")
    add("")
    add("即 `0=FEATURE_FLOODPLAINS`、`1=FEATURE_ICE`、`2=FEATURE_JUNGLE`、"
        "`3=FEATURE_FOREST`、`4=FEATURE_OASIS`、`5=FEATURE_MARSH`、`6=FEATURE_BARRIER_REEF`。")
    add("")
    add("四条独立证据支持勘误后的映射（不是猜测）：")
    add("")
    add("1. 索引 **1** 共 526 格，其中 **474 格落在远洋、52 格落在浅海** —— 只可能是海冰，"
        "不可能是仓库所说的雨林。")
    add("2. 索引 **19** 共 46 格，**全部为草原且 46/46 带河流** —— 正是草原泛滥平原的生成条件。")
    add("3. 索引 **20** 共 19 格，**全部为平原且 19/19 带河流** —— 正是平原泛滥平原的生成条件。")
    add("4. 索引 **18** 共 24 格，**全部落在浅海** —— 与 R&F 唯一新增的 `FEATURE_REEF` 一致。")
    add("")
    add("影响：`strategic-map.tsx` 用 `FEATURE_OVERLAY_COLORS` 给地貌叠色，"
        "所以网页版战略地图会把 **海冰画成绿色植被**、把森林/绿洲/沼泽全部画错颜色。"
        "此外 `getFeatureOverlay()` 只覆盖 ID 0–6，索引 ≥7 的地貌（含全部自然奇观、"
        "火山、地热裂缝、珊瑚礁、泛滥平原）在网页上**完全没有叠加显示**。")
    add("")

    add("## 数据来源与已知限制")
    add("")
    add("- 目录由 MCP 的 `src/civ_mcp/map_capture.py` 在游戏过程中 dump 到 `~/.civ6-mcp/`，"
        "再由仓库作者合并进 `map/`。")
    add("- 本机 `~/.civ6-mcp/` **不存在**，说明这些数据是在**另一台机器（macOS）**上采集的，"
        "随仓库分发；对应的存档 `0_MCP_0141.Civ6Save` 未包含在仓库里。")
    add("- `map/README.md` 记录：第 141 回合（世界议会回合）在原采集机上遇到引擎级卡死，"
        "因此快照停在该回合。")
    add("- 资源 ID 缺少 ID→名称 的权威映射（游戏侧按 `RESOURCE_*` 字符串处理，仓库里没有 ID 表），"
        "报告只做 ID 级呈现；地貌 ID 已按游戏数据库加载顺序重建，见上一节。")
    add("")

    path = os.path.join(OUT, "report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    return path


# ── HTML 渲染 ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>civ6 地图 — 第 __TURN__ 回合（苏美尔）</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#12161c; color:#e6e6e6;
         font:13px/1.5 "Segoe UI",system-ui,sans-serif; display:flex; }
  #side { width:290px; flex:none; padding:16px; overflow-y:auto;
          height:100vh; box-sizing:border-box; background:#171c24;
          border-right:1px solid #2a323d; }
  #stage { flex:1; overflow:auto; padding:16px; }
  h1 { font-size:15px; margin:0 0 4px; }
  h2 { font-size:12px; margin:18px 0 6px; color:#8fa3b8;
       text-transform:uppercase; letter-spacing:.06em; }
  .sub { color:#7d8b9c; font-size:12px; margin-bottom:12px; }
  label { display:flex; align-items:center; gap:7px; padding:3px 0; cursor:pointer; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  td { padding:2px 4px; }
  td.n { text-align:right; color:#9fb0c2; }
  .sw { width:11px; height:11px; border-radius:2px; display:inline-block;
        vertical-align:-1px; margin-right:5px; }
  #tip { position:fixed; pointer-events:none; background:#0b0f14f2;
         border:1px solid #3a4654; border-radius:5px; padding:7px 9px;
         font-size:12px; line-height:1.55; display:none; z-index:9;
         box-shadow:0 4px 14px #0009; max-width:280px; }
  .legend-row { display:flex; justify-content:space-between; padding:2px 0; font-size:12px; }
  button { background:#232c38; color:#dbe5f0; border:1px solid #3a4654;
           border-radius:4px; padding:5px 9px; cursor:pointer; font-size:12px; }
  button:hover { background:#2d3846; }
</style>
</head>
<body>
<div id="side">
  <h1>苏美尔 · 第 __TURN__ 回合</h1>
  <div class="sub">__GRIDW__ × __GRIDH__ 格 · __TILE__ 格</div>

  <div style="display:flex;gap:6px;flex-wrap:wrap">
    <button onclick="setScale(0.7)">缩小</button>
    <button onclick="setScale(1.0)">原始</button>
    <button onclick="setScale(1.5)">放大</button>
    <button onclick="toggleFlip()">南北翻转</button>
  </div>

  <h2>图层</h2>
  <label><input type="checkbox" id="ly-terrain" checked>地形</label>
  <label><input type="checkbox" id="ly-owner" checked>领土归属</label>
  <label><input type="checkbox" id="ly-route" checked>道路</label>
  <label><input type="checkbox" id="ly-feature" checked>地貌（森林/雨林/沼泽…）</label>
  <label><input type="checkbox" id="ly-resource" checked>资源</label>
  <label><input type="checkbox" id="ly-river" checked>河流</label>
  <label><input type="checkbox" id="ly-city" checked>城市</label>

  <h2>地形</h2>
  <div id="terrain-legend"></div>

  <h2>领土</h2>
  <div id="owner-legend"></div>
</div>

<div id="stage">
  <svg id="map" xmlns="http://www.w3.org/2000/svg"></svg>
</div>
<div id="tip"></div>

<script>
const TILES = __TILES__;
const CITIES = __CITIES__;
const PLAYERS = __PLAYERS__;

const W = __GRIDW__, H = __GRIDH__;
const SQRT3 = Math.sqrt(3);
let HEX = 13, FLIP = true, scale = 1;

const TERRAIN_NAMES = __TERRAIN_NAMES__;
const TERRAIN_COLORS = __TERRAIN_COLORS__;
const FEATURE_NAMES = __FEATURE_NAMES__;
const FEATURE_COLORS = __FEATURE_COLORS__;
const ROUTE_NAMES = __ROUTE_NAMES__;

// 与 web/src/lib/hex-geometry.ts 的 hexCenter 完全一致（pointy-top, odd-r offset）
function hexCenter(col, row, s) {
  const flippedRow = H - 1 - row;
  const r = FLIP ? flippedRow : row;
  const cx = SQRT3 * s * (col + 0.5 * (row & 1)) + (SQRT3 * s) / 2;
  const cy = 1.5 * s * r + s;
  return [cx, cy];
}
function hexVerts(cx, cy, s) {
  const h = (SQRT3 / 2) * s;
  return [[cx,cy-s],[cx+h,cy-s/2],[cx+h,cy+s/2],[cx,cy+s],[cx-h,cy+s/2],[cx-h,cy-s/2]];
}
const pts = (cx, cy, s) => hexVerts(cx, cy, s).map(p => p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');

const svg = document.getElementById('map');
const tip = document.getElementById('tip');

function tColor(id) { return TERRAIN_COLORS[id] || '#555'; }
function tName(id)  { return (TERRAIN_NAMES[id] || ('未知'+id)); }
function fName(id)  { return id < 0 ? '无' : (FEATURE_NAMES[id] || ('ID '+id)); }
function pName(pid) { const p = PLAYERS.find(p => p.pid === pid); return p ? p.zh : ('pid'+pid); }
function pColor(pid){ const p = PLAYERS.find(p => p.pid === pid); return p ? p.color : '#666'; }

function draw() {
  const tile = TILES.find(t => t.x === 0 && t.y === 0) || TILES[0];
  svg.innerHTML = '';
  svg.setAttribute('width',  (SQRT3 * HEX * (W + 0.5) + 4) * scale);
  svg.setAttribute('height', (1.5 * HEX * H + HEX * 2) * scale);
  svg.setAttribute('viewBox', `0 0 ${SQRT3*HEX*(W+0.5)+4} ${1.5*HEX*H+HEX*2}`);

  const on = id => document.getElementById(id).checked;
  const layers = {
    terrain: on('ly-terrain'), owner: on('ly-owner'), route: on('ly-route'),
    feature: on('ly-feature'), resource: on('ly-resource'), river: on('ly-river'),
    city: on('ly-city'),
  };

  const frag = document.createDocumentFragment();

  // 1) 地形底色 + 领土覆盖
  for (const t of TILES) {
    const [cx, cy] = hexCenter(t.x, t.y, HEX);
    if (layers.terrain) {
      const p = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
      p.setAttribute('points', pts(cx, cy, HEX));
      p.setAttribute('fill', tColor(t.terrain));
      p.setAttribute('stroke', '#0d1117');
      p.setAttribute('stroke-width', '0.4');
      frag.appendChild(p);
    }
    if (layers.owner && t.owner >= 0) {
      const o = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
      o.setAttribute('points', pts(cx, cy, HEX));
      o.setAttribute('fill', pColor(t.owner));
      o.setAttribute('opacity', '0.30');
      frag.appendChild(o);
    }
    if (layers.feature && t.feature >= 0) {
      const f = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
      f.setAttribute('points', pts(cx, cy, HEX));
      f.setAttribute('fill', FEATURE_COLORS[t.feature] || '#888888');
      f.setAttribute('opacity', t.feature === 1 ? '0.55' : '0.42');
      frag.appendChild(f);
    }
    if (layers.river && t.river) {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('cx', cx); c.setAttribute('cy', cy);
      c.setAttribute('r', HEX * 0.18);
      c.setAttribute('fill', '#4fa8e0');
      c.setAttribute('opacity', '0.85');
      frag.appendChild(c);
    }
    if (layers.resource && t.resource >= 0) {
      const r = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      r.setAttribute('cx', cx); r.setAttribute('cy', cy);
      r.setAttribute('r', HEX * 0.22);
      r.setAttribute('fill', 'none');
      r.setAttribute('stroke', '#ffd34d');
      r.setAttribute('stroke-width', '1.6');
      frag.appendChild(r);
    }
    if (layers.route && t.route >= 0) {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('cx', cx); c.setAttribute('cy', cy);
      c.setAttribute('r', HEX * 0.13);
      c.setAttribute('fill', t.route >= 2 ? '#c9c9c9' : '#b09870');
      frag.appendChild(c);
    }
  }

  // 2) 城市
  if (layers.city) {
    for (const c of CITIES) {
      const [cx, cy] = hexCenter(c.x, c.y, HEX);
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      const r = HEX * (0.42 + Math.sqrt(c.pop) * 0.06);
      const circ = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circ.setAttribute('cx', cx); circ.setAttribute('cy', cy); circ.setAttribute('r', r);
      circ.setAttribute('fill', pColor(c.pid));
      circ.setAttribute('stroke', '#fff'); circ.setAttribute('stroke-width', '1.3');
      g.appendChild(circ);
      const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      txt.setAttribute('x', cx); txt.setAttribute('y', cy + 3);
      txt.setAttribute('text-anchor', 'middle');
      txt.setAttribute('font-size', Math.max(7, HEX * 0.62));
      txt.setAttribute('fill', '#0b0f14');
      txt.setAttribute('font-weight', '700');
      txt.setAttribute('pointer-events', 'none');
      txt.textContent = c.pop;
      g.appendChild(txt);
      g.style.cursor = 'pointer';
      g.addEventListener('mousemove', ev => {
        tip.style.display = 'block';
        tip.style.left = (ev.clientX + 14) + 'px';
        tip.style.top  = (ev.clientY + 14) + 'px';
        tip.innerHTML = `<b>${c.label}</b><br>归属：${pName(c.pid)}<br>`
                      + `坐标：(${c.x}, ${c.y})<br>人口：${c.pop}`;
      });
      g.addEventListener('mouseleave', () => tip.style.display = 'none');
      frag.appendChild(g);
    }
  }

  // 3) 逐格悬停热区
  for (const t of TILES) {
    const [cx, cy] = hexCenter(t.x, t.y, HEX);
    const hit = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    hit.setAttribute('points', pts(cx, cy, HEX));
    hit.setAttribute('fill', 'transparent');
    hit.addEventListener('mousemove', ev => {
      tip.style.display = 'block';
      tip.style.left = (ev.clientX + 14) + 'px';
      tip.style.top  = (ev.clientY + 14) + 'px';
      let s = `<b>(${t.x}, ${t.y})</b> idx ${t.idx}<br>地形：${tName(t.terrain)}<br>`;
      if (t.feature >= 0)  s += `地貌：${fName(t.feature)}<br>`;
      if (t.river)         s += `河流：有<br>`;
      if (t.hills)         s += `丘陵：有<br>`;
      if (t.coastal)       s += `沿海：是<br>`;
      if (t.resource >= 0) s += `资源：ID ${t.resource}<br>`;
      if (t.route >= 0)    s += `道路：${ROUTE_NAMES[t.route] || ('ID '+t.route)}<br>`;
      s += `归属：${t.owner < 0 ? '无主' : pName(t.owner)}`;
      tip.innerHTML = s;
    });
    hit.addEventListener('mouseleave', () => tip.style.display = 'none');
    frag.appendChild(hit);
  }

  svg.appendChild(frag);
}

function setScale(s) { scale = s; draw(); }
function toggleFlip() { FLIP = !FLIP; draw(); }

for (const id of ['ly-terrain','ly-owner','ly-route','ly-feature','ly-resource','ly-river','ly-city']) {
  document.getElementById(id).addEventListener('change', draw);
}

// 图例
(function legends() {
  const tl = document.getElementById('terrain-legend');
  const counts = {};
  TILES.forEach(t => counts[t.terrain] = (counts[t.terrain] || 0) + 1);
  Object.keys(counts).sort((a,b) => counts[b]-counts[a]).forEach(id => {
    const d = document.createElement('div');
    d.className = 'legend-row';
    d.innerHTML = `<span><i class="sw" style="background:${tColor(+id)}"></i>${tName(+id)}</span>`
                + `<span>${counts[id]}</span>`;
    tl.appendChild(d);
  });
  const ol = document.getElementById('owner-legend');
  const oc = {};
  TILES.forEach(t => { if (t.owner >= 0) oc[t.owner] = (oc[t.owner]||0)+1; });
  Object.keys(oc).sort((a,b)=>oc[b]-oc[a]).forEach(pid => {
    const d = document.createElement('div');
    d.className = 'legend-row';
    d.innerHTML = `<span><i class="sw" style="background:${pColor(+pid)}"></i>${pName(+pid)}</span>`
                + `<span>${oc[pid]}</span>`;
    ol.appendChild(d);
  });
})();

draw();
</script>
</body>
</html>
"""


def write_html(static: dict, tiles: list[dict], stats: dict) -> str:
    cities = [{
        "x": c["x"], "y": c["y"], "pid": c["pid"], "pop": c["pop"],
        "label": city_label(c["name"]),
    } for c in static["initialCities"]]

    players = []
    for p in static["players"]:
        pid = p["pid"]
        zh, _, color = PLAYER_META.get(pid, (p["civ"], p["civ"], "#888888"))
        players.append({"pid": pid, "zh": zh, "civ": p["civ"], "color": color})

    tnames = {str(k): f"{zh}（{en}）" for k, (en, zh) in TERRAIN_NAMES.items()}
    fnames = {str(k): f"{zh}（{en}）" for k, (en, zh) in FEATURE_NAMES.items()}

    html = (HTML_TEMPLATE
            .replace("__TILES__", json.dumps(tiles, ensure_ascii=False))
            .replace("__CITIES__", json.dumps(cities, ensure_ascii=False))
            .replace("__PLAYERS__", json.dumps(players, ensure_ascii=False))
            .replace("__TERRAIN_NAMES__", json.dumps(tnames, ensure_ascii=False))
            .replace("__TERRAIN_COLORS__", json.dumps(TERRAIN_COLORS))
            .replace("__FEATURE_NAMES__", json.dumps(fnames, ensure_ascii=False))
            .replace("__FEATURE_COLORS__", json.dumps({str(k): v for k, v in FEATURE_COLORS.items()}))
            .replace("__ROUTE_NAMES__", json.dumps({str(k): v for k, v in ROUTE_NAMES.items()},
                                                   ensure_ascii=False))
            .replace("__TURN__", str(static["initialTurn"]))
            .replace("__GRIDW__", str(static["gridW"]))
            .replace("__GRIDH__", str(static["gridH"]))
            .replace("__TILE__", str(static["gridW"] * static["gridH"])))

    path = os.path.join(OUT, "civ6_map.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


def main() -> None:
    static = load_static()
    summary = load_summary()
    turns = load_turns()
    tiles = decode_tiles(static)
    stats = build_stats(static, tiles, turns)

    p1 = write_decoded(static, tiles)
    p2 = write_report(static, summary, stats)
    p3 = write_html(static, tiles, stats)

    print(f"解码格数        : {len(tiles)}")
    print(f"陆地 / 水域     : {stats['land']} / {stats['water']}")
    print(f"城市            : {len(static['initialCities'])}")
    print(f"回合快照        : {len(turns)}（第 {turns[0]['turn']}–{turns[-1]['turn']} 回合）")
    print(f"地形种类        : {len([k for k in stats['terrain_hist'] if k >= 0])}")
    print(f"资源种类        : {len([k for k in stats['resource_hist'] if k >= 0])}")
    print(f"写出            : {p1}")
    print(f"写出            : {p2}")
    print(f"写出            : {p3}")


if __name__ == "__main__":
    main()
