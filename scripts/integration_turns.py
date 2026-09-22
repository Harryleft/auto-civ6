"""Integration driver: launch Civ6, load a save, play N turns autonomously.

Plumbing test, not a strategy run — resolves hard blockers with a fixed
simple policy and records every anomaly (timeouts, hangs, reconnects,
unresolved blockers). Exit code 0 = all turns completed; 1 = aborted.

Usage:
    uv run python scripts/integration_turns.py [--save 吉尔伽美什_turn_1] [--turns 10]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time

from civ_mcp import game_launcher, game_lifecycle
from civ_mcp.connection import GameConnection
from civ_mcp.end_turn import _get_turn_number, execute_end_turn
from civ_mcp.game_state import GameState

log = logging.getLogger("integration")

PRODUCTION_FALLBACKS = [
    ("UNIT", "UNIT_SCOUT"),
    ("UNIT", "UNIT_WARRIOR"),
    ("BUILDING", "BUILDING_MONUMENT"),
]


class Anomalies:
    """Everything that should NOT happen in a healthy run."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, what: str) -> None:
        self.items.append(what)
        print(f"  !! ANOMALY: {what}", flush=True)


async def _connect_with_retry(conn: GameConnection, attempts: int = 12) -> None:
    """Connect, tolerating the game's boot-time behaviour of accepting a TCP
    connection and immediately resetting it while FireTuner initializes."""
    for attempt in range(1, attempts + 1):
        try:
            await conn.connect()
            return
        except Exception as e:
            if attempt == attempts:
                raise
            print(f"  connect attempt {attempt} failed ({e}), retrying in 5s", flush=True)
            await asyncio.sleep(5)


async def ensure_game(save: str) -> GameConnection:
    conn = GameConnection()
    try:
        await asyncio.wait_for(conn.connect(), timeout=6)
        print("[setup] game already running — connected", flush=True)
    except Exception:
        print("[setup] game not reachable — launching via Steam...", flush=True)
        t0 = time.perf_counter()
        result = await game_launcher.launch_game()
        print(f"[setup] launch ({time.perf_counter() - t0:.0f}s): {result}", flush=True)
        if result.startswith("FAILED") or "not detected" in result:
            raise SystemExit(f"LAUNCH FAILED: {result}")
        await _connect_with_retry(conn)
    print(f"[setup] Lua states: {conn.lua_states}", flush=True)

    print(f"[setup] loading save '{save}' via FrontEnd API...", flush=True)
    t0 = time.perf_counter()
    result = await game_lifecycle.load_game_save(conn, save)
    dt = time.perf_counter() - t0
    print(f"[setup] load ({dt:.0f}s): {result[:300]}", flush=True)
    if (
        result.startswith("Error")
        or result.startswith("ERR:")
        or "not found" in result.lower()
    ):
        raise SystemExit(f"LOAD FAILED: {result}")
    # The game now reloads into the world (and may have dropped FireTuner) —
    # wait, then reconnect and re-discover states.
    await asyncio.sleep(5)
    try:
        await conn.disconnect()
    except Exception:
        pass
    await _connect_with_retry(conn)
    return conn


async def resolve_blockers(
    gs: GameState, report: str, anomalies: Anomalies
) -> list[str]:
    """Resolve hard blockers from a 'Cannot end turn' report.

    Returns the actions taken. Blockers we don't know how to resolve are
    recorded as anomalies (findings), never silently skipped.
    """
    actions: list[str] = []
    for line in report.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        kind = line[2:].split("(")[0].strip()

        if kind == "Units":
            r = await gs.skip_remaining_units()
            actions.append(f"units -> skip_remaining: {r[:60]}")

        elif kind == "Production":
            ids = [int(m) for m in re.findall(r"\(id:(\d+)\)", line)]
            for cid in ids:
                done = None
                for itype, iname in PRODUCTION_FALLBACKS:
                    r = await gs.set_city_production(cid, itype, iname)
                    if not r.startswith("Error"):
                        done = f"{iname}: {r[:50]}"
                        break
                actions.append(f"production city {cid} -> {done or 'ALL FAILED'}")
            if not ids:
                anomalies.add(f"production blocker without city id: {line[:80]}")

        elif kind == "Research":
            tc = await gs.get_tech_civics()
            if tc.available_techs:
                pick = min(tc.available_techs, key=lambda t: t.turns)
                r = await gs.set_research(pick.name)
                if r.startswith("Error"):
                    r = await gs.set_research(pick.tech_type)
                actions.append(f"research -> {pick.name}: {r[:50]}")

        elif kind == "Civic":
            tc = await gs.get_tech_civics()
            if tc.available_civics:
                pick = min(tc.available_civics, key=lambda c: c.turns)
                r = await gs.set_civic(pick.name)
                if r.startswith("Error"):
                    r = await gs.set_civic(pick.civic_type)
                actions.append(f"civic -> {pick.name}: {r[:50]}")

        elif kind == "City Capture":
            r = await gs.resolve_city_capture("keep")
            actions.append(f"city capture -> keep: {r[:60]}")

        elif kind == "Policy Slot":
            gov = await gs.get_policies()
            assigned: dict[int, str] = {}
            for slot in gov.slots:
                if slot.current_policy:
                    continue
                for pol in gov.available_policies:
                    if pol.slot_type == slot.slot_type:
                        assigned[slot.slot_index] = pol.policy_type
                        break
            if assigned:
                r = await gs.set_policies(assigned)
                actions.append(f"policies -> {assigned}: {r[:50]}")
            else:
                anomalies.add(f"policy blocker, nothing assignable: {line[:80]}")

        else:
            anomalies.add(f"unresolved blocker kind '{kind}': {line[:80]}")

    return actions


async def handle_diplomacy(gs: GameState, anomalies: Anomalies) -> None:
    """Answer pending diplomacy encounters and decline incoming trade deals."""
    for _ in range(6):  # sessions have 2-3 rounds
        sessions = await gs.get_diplomacy_sessions()
        if not sessions:
            break
        s = sessions[0]
        r = await gs.diplomacy_respond(s.other_player_id, "POSITIVE")
        print(f"  diplomacy({s.other_civ_name}) -> {r[:60]}", flush=True)
        await asyncio.sleep(0.5)
    else:
        anomalies.add("diplomacy session did not close after 6 rounds")

    deals = await gs.get_pending_deals()
    for d in deals:
        r = await gs.respond_to_deal(d.other_player_id, False)
        print(f"  trade deal({d.other_player_name}) declined -> {r[:60]}", flush=True)


async def play(save: str, n_turns: int) -> int:
    anomalies = Anomalies()
    conn = await ensure_game(save)
    gs = GameState(conn)
    recoveries = 0
    completed = 0

    turn0 = await _get_turn_number(gs)
    print(f"[run] starting at turn {turn0}, playing {n_turns} turns", flush=True)

    while completed < n_turns:
        turn_before = await _get_turn_number(gs)
        t0 = time.perf_counter()
        try:
            result = await execute_end_turn(gs)
        except TimeoutError as e:
            anomalies.add(f"turn {turn_before}: silent-socket TimeoutError: {e}")
            await conn.reconnect()
            continue
        except Exception as e:
            anomalies.add(f"turn {turn_before}: {type(e).__name__}: {e}")
            await conn.reconnect()
            continue
        dt = time.perf_counter() - t0

        if result.startswith("Cannot end turn: diplomacy encounter") or (
            "trade deal pending" in result
        ):
            print(f"[turn {turn_before}] diplomacy/trade pending — handling", flush=True)
            await handle_diplomacy(gs, anomalies)
            continue

        if result.startswith("Cannot end turn"):
            actions = await resolve_blockers(gs, result, anomalies)
            for a in actions:
                print(f"  resolved: {a}", flush=True)
            if not actions:
                print(f"[ABORT] unresolvable blocker report:\n{result}", flush=True)
                break
            continue

        if result.startswith("HANG:"):
            recoveries += 1
            anomalies.add(f"turn {turn_before}: game HANG ({recoveries})")
            if recoveries > 2:
                print(f"[ABORT] {recoveries} hangs — giving up", flush=True)
                break
            save_name = result.split("|")[0].split(":")[-1]
            print(f"[turn {turn_before}] HANG — restart_and_load({save_name})", flush=True)
            r = await game_launcher.restart_and_load(save_name)
            print(f"  recovery: {r[:120]}", flush=True)
            try:
                await conn.disconnect()
            except Exception:
                pass
            await _connect_with_retry(conn)
            continue

        turn_after = await _get_turn_number(gs)
        headline = result.splitlines()[0] if result else "(empty result)"
        print(
            f"[turn {turn_before} -> {turn_after}] {dt:.0f}s  {headline[:100]}",
            flush=True,
        )
        if turn_after is not None and turn_before is not None and turn_after <= turn_before:
            anomalies.add(
                f"turn did not advance ({turn_before} -> {turn_after}): {headline[:80]}"
            )
            break
        completed += 1

    print("\n========== SUMMARY ==========")
    print(f"turns completed : {completed}/{n_turns}")
    print(f"hang recoveries : {recoveries}")
    print(f"anomalies       : {len(anomalies.items)}")
    for a in anomalies.items:
        print(f"  - {a}")
    return 0 if completed == n_turns and not anomalies.items else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", default="吉尔伽美什_turn_1")
    ap.add_argument("--turns", type=int, default=10)
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("integration").setLevel(logging.INFO)

    try:
        code = asyncio.run(play(args.save, args.turns))
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
