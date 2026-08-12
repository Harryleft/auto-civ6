#!/usr/bin/env python3
"""Civ 6 helper toolkit — calculations to speed up decision-making.

Usage:
  civ6_tool.py dist X1 Y1 X2 Y2            # hex distance between tiles
  civ6_tool.py settle X Y                   # settle legality + score vs known cities
  civ6_tool.py combat CS1 HP1 CS2 HP2       # rough damage estimate (Civ6 formula)
  civ6_tool.py status                       # live empire digest (calls MCP get_cities/overview)
  civ6_tool.py plan                         # expansion plan tracker (cities vs target)

Reads live game data via the persistent MCP session (scripts/civ6).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CIV6 = REPO / "scripts" / "civ6"

PLAN_FILE = Path("/tmp/civ6_expansion_plan.json")
TARGET_CITIES = 10

# Rough Civ6 combat damage model: damage = 30 * (A/B)^1.8, clamped 5..cap
def combat(cs1: float, hp1: float, cs2: float, hp2: float) -> dict:
    """Estimate ranged/melee outcome. cs1 attacks cs2."""
    eff1 = cs1 * (hp1 / 100.0) ** 0.5
    eff2 = cs2 * (hp2 / 100.0) ** 0.5
    ratio = eff1 / eff2 if eff2 > 0 else 99
    dmg = 30 * (ratio ** 1.8)
    dmg = max(5, min(dmg, 100))
    ret = 30 * (eff2 / eff1) ** 1.8 if eff1 > 0 else 99
    ret = max(5, min(ret, 100))
    return {
        "attacker_damage": round(dmg, 1),
        "defender_damage": round(ret, 1),
        "attacker_survives": ret < hp1 - 10,
        "defender_survives": dmg < hp2 - 10,
        "kills": dmg >= hp2,
    }


def hex_dist(x1, y1, x2, y2) -> int:
    """Civ6 hex distance (validated: max(|dx|,|dy|) in this coordinate system)."""
    return max(abs(x1 - x2), abs(y1 - y2))


def call_tool(name: str, args: str = "{}") -> str | None:
    try:
        r = subprocess.run(
            [str(CIV6), name, args],
            capture_output=True, text=True, timeout=90,
        )
        for line in r.stdout.strip().splitlines():
            try:
                d = json.loads(line)
                if d.get("ok"):
                    return d.get("result")
            except Exception:
                continue
    except Exception as e:
        return f"ERR: {e}"
    return None


def parse_cities(raw: str) -> list[dict]:
    cities = []
    for line in raw.splitlines():
        # 城市名 (pop N) at (X,Y) — ...
        if "at (" not in line or "CITY_CENTER" in line:
            continue
        if "Cities" in line and ":" in line and "at" not in line:
            continue
        try:
            name = line.split("(")[0].strip()
            if not name:
                continue
            coord = line.split("at (")[1].split(")")[0]
            x, y = map(int, coord.split(","))
            pop = 0
            if "pop " in line:
                pop = int(line.split("pop ")[1].split(")")[0])
            cities.append({"name": name, "x": x, "y": y, "pop": pop})
        except Exception:
            continue
    return cities


def cmd_dist(args) -> None:
    x1, y1, x2, y2 = map(int, args[:4])
    d = hex_dist(x1, y1, x2, y2)
    print(f"hex distance ({x1},{y1}) -> ({x2},{y2}) = {d}")


def cmd_settle(args) -> None:
    x, y = map(int, args[:2])
    raw = call_tool("get_cities") or ""
    cities = parse_cities(raw)
    print(f"Candidate settle ({x},{y}):")
    min_d = 99
    nearest = None
    for c in cities:
        d = hex_dist(x, y, c["x"], c["y"])
        if d < min_d:
            min_d, nearest = d, c
        print(f"  {c['name']} ({c['x']},{c['y']}) dist {d} {'OK' if d > 3 else 'TOO CLOSE'}")
    if nearest:
        print(f"\nVerdict: nearest is {nearest['name']} at dist {min_d} "
              f"-> {'LEGAL' if min_d > 3 else 'ILLEGAL (need > 3)'}")


def cmd_combat(args) -> None:
    cs1, hp1, cs2, hp2 = map(float, args[:4])
    r = combat(cs1, hp1, cs2, hp2)
    print(f"Combat: CS{cs1}({hp1}hp) vs CS{cs2}({hp2}hp)")
    print(f"  dmg to defender: ~{r['attacker_damage']} | to attacker: ~{r['defender_damage']}")
    print(f"  kills defender: {r['kills']} | attacker survives: {r['attacker_survives']}")


def cmd_status(args) -> None:
    ov = call_tool("get_game_overview") or "n/a"
    print("=== GAME OVERVIEW ===")
    print(ov)
    raw = call_tool("get_cities") or ""
    cities = parse_cities(raw)
    print(f"\n=== CITIES ({len(cities)}/{TARGET_CITIES}) ===")
    for c in cities:
        print(f"  {c['name']} ({c['x']},{c['y']}) pop {c['pop']}")


def cmd_plan(args) -> None:
    plan = {}
    if PLAN_FILE.exists():
        plan = json.loads(PLAN_FILE.read_text())
    raw = call_tool("get_cities") or ""
    cities = parse_cities(raw)
    n = len(cities)
    print(f"Expansion: {n}/{TARGET_CITIES} cities")
    print(f"  need {TARGET_CITIES - n} more")
    for c in cities:
        print(f"    {c['name']} ({c['x']},{c['y']}) pop {c['pop']}")
    if plan.get("targets"):
        print("\nPlanned settle targets:")
        for t in plan["targets"]:
            print(f"  ({t['x']},{t['y']}) {'DONE' if t.get('done') else 'pending'}")


def cmd_save_plan(args) -> None:
    """civ6_tool.py plan-save '[[x,y],[x,y],...]'"""
    try:
        pts = json.loads(args[0])
    except Exception:
        print("usage: plan-save '[[x,y],...]'")
        return
    PLAN_FILE.write_text(json.dumps({"targets": [{"x": a, "y": b} for a, b in pts]}))
    print(f"saved {len(pts)} targets")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    rest = sys.argv[2:]
    if cmd == "dist" and len(rest) >= 4:
        cmd_dist(rest)
    elif cmd == "settle" and len(rest) >= 2:
        cmd_settle(rest)
    elif cmd == "combat" and len(rest) >= 4:
        cmd_combat(rest)
    elif cmd == "status":
        cmd_status(rest)
    elif cmd == "plan":
        cmd_plan(rest)
    elif cmd == "plan-save":
        cmd_save_plan(rest)
    else:
        print(__doc__)
