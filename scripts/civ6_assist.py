#!/usr/bin/env python3
"""Civ 6 decision-assist toolkit — consolidates repeated checks into one call.

Usage:
  civ6_assist.py precheck            # end_turn 前完整预检:blocker + 资源机会
  civ6_assist.py units               # 单位全景(可行动/已行动/可晋升/可升级)
  civ6_assist.py promotions          # 批量晋升检查(替代手动循环)
  civ6_assist.py expansion           # 扩张规划:城市/建城点/未改良资源
  civ6_assist.py threats             # 威胁排序:距城距离+CS+类型
  civ6_assist.py cities              # 城市队列+增长+瓶颈诊断

All queries run through the persistent MCP session (scripts/civ6).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CIV6 = REPO / "scripts" / "civ6"
TOOL = REPO / "scripts" / "civ6_tool.py"

# ----------------------------- MCP bridge -----------------------------

def call(name: str, args: str = "{}", timeout: int = 45) -> str | None:
    """Call an MCP tool via the session wrapper; returns result text or None.
    Short timeout + fast failure so the toolkit never hangs when the game is offline.
    """
    try:
        r = subprocess.run([str(CIV6), name, args], capture_output=True, text=True, timeout=timeout)
        for line in r.stdout.strip().splitlines():
            try:
                d = json.loads(line)
                if d.get("ok"):
                    return d.get("result")
                if d.get("error"):
                    return f"TOOL_ERR:{d['error']}"
            except Exception:
                continue
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        return f"BRIDGE_ERR:{e}"
    return None


def game_online() -> bool:
    """Quick connectivity probe before running full checks."""
    ov = call("get_game_overview", timeout=20)
    return bool(ov) and "Cannot connect" not in str(ov) and "TOOL_ERR" not in str(ov) and "BRIDGE_ERR" not in str(ov)


def parse_units(raw: str) -> list[dict]:
    """Parse get_units output into structured unit list."""
    units = []
    for line in raw.splitlines():
        m = re.match(r"\s*([^\s(]+) \(UNIT_\w+\) at \((\d+),(\d+)\)", line)
        if not m:
            continue
        name, x, y = m.group(1), int(m.group(2)), int(m.group(3))
        mid = re.search(r"\[id:(\d+)", line)
        moves = re.search(r"moves (\d+)/", line)
        hp = re.search(r"HP: (\d+)/", line)
        unit = {
            "name": name, "x": x, "y": y,
            "id": int(mid.group(1)) if mid else None,
            "moves": int(moves.group(1)) if moves else 0,
            "hp": int(hp.group(1)) if hp else 100,
            "line": line.strip(),
            "can_attack": "CAN ATTACK" in line,
            "can_upgrade": "CAN UPGRADE" in line,
            "can_build": "Can build" in line,
            "route": "ON ROUTE" in line,
            "no_moves": "no moves" in line or (moves and moves.group(1) == "0"),
        }
        units.append(unit)
    return units


def parse_cities(raw: str) -> list[dict]:
    cities = []
    for line in raw.splitlines():
        m = re.match(r"\s*([^\s(]+) \(pop (\d+)\) at \((\d+),(\d+)\)", line)
        if not m:
            continue
        name, pop, x, y = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        city = {
            "name": name, "pop": pop, "x": x, "y": y,
            "building": re.search(r"Building: ([^|]*)", line),
            "growth": re.search(r"Growth: [\d/]+ \(([\d]+)t", line),
            "food_surplus": re.search(r"Food (\d+) Prod (\d+)", line),
            "needs_builder": "Needs builder" in line,
            "pillaged": "PILLAGED" in line,
            "stagnant": "STAGNANT" in line or "SLOW GROWTH" in line,
            "garrison": re.search(r"Gar:(\w+)", line),
            "walls": "Walls" in line,
        }
        cities.append(city)
    return cities


def hex_dist(x1, y1, x2, y2) -> int:
    return max(abs(x1 - x2), abs(y1 - y2))


# ----------------------------- Commands -----------------------------

def cmd_precheck() -> None:
    print("=== END_TURN 预检 ===\n")
    issues = []
    opps = []

    # 1. 城市生产队列
    raw = call("get_cities") or ""
    cities = parse_cities(raw)
    for c in cities:
        if c["building"] is None:
            issues.append(f"[生产] {c['name']}({c['x']},{c['y']}) 队列空 -> set_city_production")
        if c["stagnant"]:
            issues.append(f"[增长] {c['name']} 停滞/慢增长 -> 农场/商路/谷仓")

    # 2. 研究/市政
    tech = call("get_tech_civics") or ""
    if "No technology being researched" in tech:
        issues.append("[研究] 科技队列空 -> set_research")
    if "No civic being progressed" in tech:
        issues.append("[市政] 市政队列空 -> set_research(civic)")

    # 3. 政策槽
    pol = call("get_policies") or ""
    if "EMPTY" in pol:
        issues.append("[政策] 有空槽 -> set_policies")

    # 4. 使者
    cs = call("get_city_states") or ""
    m = re.search(r"Envoy tokens available: (\d+)", cs)
    if m and int(m.group(1)) > 0:
        issues.append(f"[使者] {m.group(1)} 令牌待派 -> send_envoy")

    # 5. 待处理外交
    dip = call("get_pending_diplomacy") or ""
    if "pending" in dip and "No pending" not in dip:
        issues.append("[外交] 有会话待处理 -> respond_to_diplomacy/respond_to_trade")

    # 6. 世界大会
    wc = call("get_world_congress") or ""
    if "FIRES THIS TURN" in wc:
        issues.append("[世界议会] 本回合召开 -> queue_wc_votes")

    # 7. 未改良资源(机会)
    res = call("get_empire_resources") or ""
    unimp = re.findall(r"!! (\w+) \u2014 UNIMPROVED at \((\d+),(\d+)\)", res)
    for name, x, y in unimp:
        opps.append(f"[改良] {name}@({x},{y}) 未改良 -> 派建造者")

    # 8. 过剩奢侈品(机会)
    lux = re.findall(r"(\w+): (\d+) \((\d+) tradeable\)", res)
    for name, total, tradable in lux:
        if int(tradable) > 0:
            opps.append(f"[交易] {name} 过剩 {tradable} 可卖 -> propose_trade test")

    # 9. 可触发 Eureka(机会)
    for m in re.finditer(r"(\w[\w\s]*) \(TECH_[\w]+\) [\d]+ turns BOOSTED.*?Boost: ([^\]|]+)", tech):
        if any(k in m.group(1) for k in ["IRON", "SALT", "HORS", "PEARL", "RICE", "WHEAT", "IRON", "STONE", "FISH", "CATTLE", "SHEEP"]):
            opps.append(f"[Eureka] {m.group(1).strip()} boost: {m.group(2).strip()}")

    # 10. 单位晋升
    uraw = call("get_units") or ""
    units = parse_units(uraw)
    promo_candidates = []
    for u in units:
        if u["id"] and u["can_attack"]:  # 有战斗经验的单位才可能晋升
            pr = call("get_unit_promotions", f'{{"unit_id": {u["id"]}}}')
            if pr and "Promotions for" in str(pr) and "No promotions" not in str(pr):
                promo_candidates.append(f"{u['name']}@{u['x']},{u['y']}")
    if promo_candidates:
        opps.append(f"[晋升] 可晋升单位: {', '.join(promo_candidates[:5])}")

    print("【阻塞项】(必须先处理):")
    for i in issues:
        print(f"  ! {i}")
    if not issues:
        print("  (无阻塞)")
    print("\n【机会项】(值得做):")
    for i in opps[:12]:
        print(f"  o {i}")
    if not opps:
        print("  (无)")

    # 11. 单位行动确认
    idle = [u for u in units if not u["no_moves"] and not u["route"] and u["name"] not in ("商人",)]
    print(f"\n【单位】{len(units)} 个, 未行动可指挥: {len(idle)}")
    for u in idle[:8]:
        tag = "UPGRADE" if u["can_upgrade"] else ("BUILD" if u["can_build"] else "")
        print(f"  {u['name']}@({u['x']},{u['y']}) moves {u['moves']} {tag}")


def cmd_units() -> None:
    raw = call("get_units") or ""
    units = parse_units(raw)
    can_act = [u for u in units if not u["no_moves"] and not u["route"]]
    done = [u for u in units if u["no_moves"] or u["route"]]
    print(f"=== 单位全景 ({len(units)}) ===\n")
    print("【可行动】")
    for u in can_act:
        extra = []
        if u["can_attack"]: extra.append("可攻击")
        if u["can_upgrade"]: extra.append("可升级")
        if u["can_build"]: extra.append("可建")
        hp = f" HP{u['hp']}" if u["hp"] < 100 else ""
        print(f"  {u['name']}@{u['x']},{u['y']} m{u['moves']}{hp} {' '.join(extra)}")
    print("\n【已行动/驻留】")
    for u in done[:10]:
        print(f"  {u['name']}@{u['x']},{u['y']} {'(商路)' if u['route'] else '(已行动)'}")


def cmd_promotions() -> None:
    raw = call("get_units") or ""
    units = parse_units(raw)
    print("=== 全单位晋升检查 ===")
    found = 0
    for u in units:
        if not u["id"]:
            continue
        pr = call("get_unit_promotions", f'{{"unit_id": {u["id"]}}}')
        if not pr or "No promotions" in str(pr) or "TOOL_ERR" in str(pr):
            continue
        if "Promotions for" in str(pr):
            opts = [l.strip() for l in str(pr).splitlines() if "(" in l and "PROMOTION_" in l]
            if opts:
                found += 1
                print(f"\n{u['name']}@{u['x']},{u['y']}:")
                for o in opts:
                    print(f"  {o}")
    if not found:
        print("  (无可晋升单位)")


def cmd_expansion() -> None:
    from civ6_tool import parse_cities as pt_parse  # reuse if importable
    raw = call("get_cities") or ""
    cities = parse_cities(raw)
    print(f"=== 扩张规划: {len(cities)} 城 ===")
    for c in cities:
        growth = "停滞" if c["stagnant"] else ("正常" if c["growth"] else "?")
        prod = c["food_surplus"]
        extra = " [需建造者]" if c["needs_builder"] else ""
        extra += " [被掠夺]" if c["pillaged"] else ""
        extra += " [无墙]" if not c["walls"] else ""
        print(f"  {c['name']} pop{c['pop']} ({c['x']},{c['y']}) {growth}{extra}")

    # 建城顾问
    adv = call("get_global_settle_advisor") or ""
    print("\n建城顾问 Top5:")
    n = 0
    for line in adv.splitlines():
        m = re.match(r"\s*#(\d+) \((\d+),(\d+)\): Score (\d+)", line)
        if m:
            x, y, score = int(m.group(2)), int(m.group(3)), int(m.group(4))
            # 距离验证
            dists = [hex_dist(x, y, c["x"], c["y"]) for c in cities]
            legal = all(d > 3 for d in dists)
            n += 1
            if n <= 5:
                print(f"  ({x},{y}) score={score} {'LEGAL' if legal else 'ILLEGAL(距城过近)'} 距城={dists}")
    if n == 0:
        print("  (无数据)")

    # 未改良资源
    res = call("get_empire_resources") or ""
    unimp = re.findall(r"!! (\w+) \u2014 UNIMPROVED at \((\d+),(\d+)\)", res)
    if unimp:
        print("\n未改良资源:")
        for name, x, y in unimp:
            print(f"  {name}@({x},{y})")


def cmd_threats() -> None:
    raw = call("get_units") or ""
    craw = call("get_cities") or ""
    cities = parse_cities(craw)
    idx = raw.find("Nearby threats")
    if idx < 0:
        print("=== 威胁 ===\n(无 Nearby threats 段)")
        return
    threats_raw = raw[idx:]
    print("=== 威胁排序(距最近城市 + CS) ===")
    entries = []
    cur_faction = "?"
    for line in threats_raw.splitlines():
        mf = re.match(r"\s*(\S+?)\s*\((\d+) units?\):", line)
        if mf:
            cur_faction = mf.group(1)
            continue
        m = re.match(r"\s*UNIT_(\w+) at \((\d+),(\d+)\) \u2014 CS:(\d+)", line)
        if m:
            name, x, y, cs = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
            hp = re.search(r"HP:(\d+)/", line)
            hpv = int(hp.group(1)) if hp else 100
            mind = min((hex_dist(x, y, c["x"], c["y"]) for c in cities), default=99)
            entries.append((mind, -cs, f"[{cur_faction}] CS{cs}({hpv}hp) {name}@({x},{y}) 距城{mind}格"))
    entries.sort()
    for _, _, desc in entries[:12]:
        print(f"  {desc}")
    if not entries:
        print("  (无可见威胁)")


def cmd_cities() -> None:
    raw = call("get_cities") or ""
    cities = parse_cities(raw)
    print(f"=== 城市诊断 ({len(cities)}) ===")
    for c in cities:
        print(f"\n{c['name']} pop{c['pop']} ({c['x']},{c['y']})")
        if c["building"]:
            print(f"  生产: {c['building'].group(1).strip()}")
        else:
            print("  生产: 【空】")
        if c["growth"]:
            print(f"  增长: {c['growth'].group(1)} 回合")
        if c["food_surplus"]:
            f, p = c["food_surplus"].group(1), c["food_surplus"].group(2)
            print(f"  食物/产力: {f}/{p}")
        if c["stagnant"]:
            print("  !! 停滞/慢增长")
        if c["pillaged"]:
            print("  !! 有被掠夺地块")
        if c["needs_builder"]:
            print("  !! 需要建造者改良")
        if not c["walls"]:
            print("  !! 无城墙(边境城市风险)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "precheck"
    if cmd in ("precheck", "units", "promotions", "expansion", "threats", "cities"):
        if not game_online():
            print("游戏未连接(FireTuner 4318)。先启动游戏再运行。")
            sys.exit(1)
    if cmd == "precheck":
        cmd_precheck()
    elif cmd == "units":
        cmd_units()
    elif cmd == "promotions":
        cmd_promotions()
    elif cmd == "expansion":
        cmd_expansion()
    elif cmd == "threats":
        cmd_threats()
    elif cmd == "cities":
        cmd_cities()
    else:
        print(__doc__)
