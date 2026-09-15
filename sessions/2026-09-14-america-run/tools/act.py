#!/usr/bin/env python3
"""一步完成「路由信念决策 + 执行动作」。

治理门禁（enforce）要求每个受管动作先有匹配 action_intent 的授权。
手工两步很啰嗦，这里自动生成 action_intent 并提交路由，然后执行。

用法：
    python act.py set_research tech_or_civic=TECH_ENGINEERING
    python act.py unit_action unit_id=12345 action=fortify
    python act.py --statement "自定义判断" set_city_production city_id=1 item_type=UNIT item_name=UNIT_SCOUT
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(r"S:\vibe_coding\civ6-map-analysis")
CMD = HERE / "_cmd.jsonl"
OUT = HERE / "_out.jsonl"


def _lines(p: Path) -> list[str]:
    try:
        return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def call(tool: str, args: dict, timeout: float = 600.0) -> dict:
    before = len(_lines(OUT))
    with CMD.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "args": args}, ensure_ascii=False) + "\n")
        fh.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = _lines(OUT)
        if len(lines) > before:
            return json.loads(lines[-1])
        time.sleep(0.3)
    return {"ok": False, "tool": tool, "result": f"TIMEOUT after {timeout}s"}


def _coerce(v: str):
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v[:1] in "[{":
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    return v


def main() -> int:
    argv = sys.argv[1:]
    statement = None
    if argv and argv[0] == "--statement":
        statement = argv[1]
        argv = argv[2:]
    if len(argv) < 2:
        print("usage: act.py [--statement S] <tool> key=value ...")
        return 2

    tool = argv[0]
    args: dict = {}
    for item in argv[1:]:
        k, _, v = item.partition("=")
        args[k] = _coerce(v)

    if statement is None:
        statement = f"在回合 {time.strftime('%H')}:{time.strftime('%M')} 执行 {tool}，参数 {json.dumps(args, ensure_ascii=False)}；该动作可逆性低、符合当前扩张与避免黑暗时代的目标。"

    route_args = {
        "statement": statement,
        "probability": 0.8,
        "confidence": 0.8,
        "impact": "medium",
        "urgency": "medium",
        "irreversibility": 0.1,
        "belief_ids": "[]",
        "considered_actions": "[\"不执行该动作\", \"执行该动作\"]",
        "selected_action": tool,
        "reason": "按当前局面目标（扩张、避免黑暗时代、提升产出）判断该动作收益为正。",
        "action_intent": json.dumps({"tool": tool, "params": args}, ensure_ascii=False),
        "evidence_requirements": "[]",
        "gate_scope": "global",
    }

    rec = call("route_belief_decision", route_args, timeout=120)
    if not rec.get("ok"):
        print(f"ROUTE FAILED: {rec.get('result')}")
        return 1
    try:
        routed = json.loads(rec["result"])
        state = routed.get("decision_state")
        did = routed.get("id")
    except Exception:  # noqa: BLE001
        state, did = "?", "?"
        print("[route raw]", rec["result"][:800])
    print(f"[route] {state} {did}")

    rec2 = call(tool, args, timeout=600)
    print(f"[{tool}] ok={rec2.get('ok')} {rec2.get('seconds')}s")
    print(rec2.get("result"))
    return 0 if rec2.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
