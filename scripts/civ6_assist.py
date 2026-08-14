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


def print_belief_turn_brief(raw: str | None) -> None:
    """Put the reviewed belief state at the top of every precheck."""
    print("=== BELIEF ENGINE 回合简报 ===")
    if not raw:
        print("  !! 无法读取信念引擎；本回合不得把威胁扫描当作战力结论。")
        return
    if raw.startswith(("TOOL_ERR:", "BRIDGE_ERR:")):
        print(f"  !! {raw}")
        return
    try:
        brief = json.loads(raw)
    except json.JSONDecodeError:
        print("  !! 信念简报格式异常，先调用 get_turn_brief 重试。")
        return

    gate = brief.get("decision_gate") or {}
    print(f"  默认路由: {gate.get('default_route', 'fast')}")
    beliefs = brief.get("beliefs") or []
    if beliefs:
        for item in beliefs:
            review = " [REVIEW]" if item.get("review_required") else ""
            print(
                f"  信念 {item.get('id', '?')}: p={float(item.get('probability', 0)):.2f} "
                f"conf={float(item.get('confidence', 0)):.2f}{review} — "
                f"{item.get('statement', '')}"
            )
    else:
        print("  当前没有活动信念。")

    predictions = brief.get("predictions") or []
    if predictions:
        print("  预测: " + "; ".join(
            f"{item.get('id', '?')}@T{item.get('deadline_turn', '?')}"
            for item in predictions
        ))
    plans = brief.get("plans") or []
    if plans:
        print("  计划: " + "; ".join(
            f"{item.get('id', '?')}[{item.get('status', 'active')}]"
            for item in plans
        ))
    if gate.get("active_surprises"):
        print("  !! Surprise: " + ", ".join(gate["active_surprises"]))
    if gate.get("active_contradictions"):
        print("  !! Contradiction: " + ", ".join(gate["active_contradictions"]))
    print("  约束: 附近敌对单位只触发验证；路线风险需用 get_combat_estimate 的真实数值更新。")
    print("  约束: 高影响/不可逆行动前调用 route_belief_decision。")


def _brief_raw() -> str | None:
    """Get governance brief with fallback to turn brief on older servers."""
    raw = call("get_governance_brief")
    if raw and "Unknown tool" not in raw and "TOOL_ERR" not in raw and "BRIDGE_ERR" not in raw:
        return raw
    return call("get_turn_brief", '{"limit": 12}')


def print_governance_brief(raw: str | None) -> bool:
    """Print the typed governance agenda and its nested belief brief."""

    print("=== GOVERNANCE 治理简报 ===")
    if not raw:
        print("  !! 无法读取治理快照；不得进入关键行动或结束回合。")
        return False
    if raw.startswith(("TOOL_ERR:", "BRIDGE_ERR:", "Error:")):
        print(f"  !! {raw}")
        return False
    try:
        governance = json.loads(raw)
    except json.JSONDecodeError:
        print("  !! 治理简报格式异常，请直接调用 get_governance_brief 重试。")
        return False
    # Older server without get_governance_brief returns the turn brief format.
    if governance.get("decision_gate") is not None:
        print("  (服务器未提供治理快照，使用信念回合简报)")
        print_belief_turn_brief(raw)
        return True

    snapshot = governance.get("snapshot") or {}
    capabilities = governance.get("capabilities") or {}
    agenda = governance.get("governance") or {}
    print(
        f"  回合 {governance.get('turn', '?')} | "
        f"snapshot={snapshot.get('snapshot_id', '?')} | "
        f"ruleset={snapshot.get('ruleset', '?')}"
    )
    unavailable = sorted(
        name for name, enabled in capabilities.items() if enabled is False
    )
    if unavailable:
        print("  规则集禁用: " + ", ".join(unavailable))
    print(
        "  议程: goals={goals} proposals={proposals} reviews={reviews} "
        "decisions={decisions} locks={locks}".format(
            goals=len(agenda.get("goals") or []),
            proposals=len(agenda.get("proposals") or []),
            reviews=len(agenda.get("critic_reviews") or []),
            decisions=len(agenda.get("council_decisions") or []),
            locks=len(agenda.get("budget_locks") or []),
        )
    )
    confidence_gaps = governance.get("confidence_gaps") or []
    if confidence_gaps:
        print(
            "  !! 低置信度: "
            + ", ".join(str(item.get("id", "?")) for item in confidence_gaps)
        )
    print_belief_turn_brief(
        json.dumps(governance.get("belief_brief") or {}, ensure_ascii=False)
    )
    return bool(snapshot.get("snapshot_id"))


# ----------------------------- Commands -----------------------------

def cmd_precheck(governance_raw: str | None = None) -> None:
    print("=== END_TURN 预检 ===\n")
    issues = []
    opps = []
    # This is the mandatory control-plane entry. It captures typed GameState,
    # updates the governance graph, and embeds the reviewed belief brief.
    if governance_raw is None:
        governance_raw = _brief_raw()
    if not print_governance_brief(governance_raw):
        issues.append("[治理] 当前回合类型化治理快照缺失 -> get_governance_brief")

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
    governance_raw = None
    if cmd == "precheck":
        governance_raw = _brief_raw()
        if not governance_raw or "Cannot connect" in str(governance_raw):
            print("游戏未连接(FireTuner 4318)。先启动游戏再运行。")
            sys.exit(1)
    elif cmd in ("units", "promotions", "expansion", "threats", "cities"):
        if not game_online():
            print("游戏未连接(FireTuner 4318)。先启动游戏再运行。")
            sys.exit(1)
    if cmd == "precheck":
        cmd_precheck(governance_raw)
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
