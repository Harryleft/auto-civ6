#!/usr/bin/env python3
"""自动执政官：无人值守地把回合推进下去，直到分出胜负或到达上限。

设计取舍：
  - 这是「保守型总督」策略，不追求最优，目的是把对局推到终点。
  - 每回合只做低风险、可逆的决策：补研究/市政、给停产的城市排产、
    处理待回应外交、跳过其余单位、结束回合。
  - 遇到无法处理的错误或明显的游戏结束信号就停止并报告。

用法：
    python play.py --max-turns 20
    python play.py --max-turns 200 --log _play.log
"""

from __future__ import annotations

import argparse
import json
import re
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


def call(tool: str, args: dict | None = None, timeout: float = 600.0) -> tuple[bool, str]:
    before = len(_lines(OUT))
    with CMD.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "args": args or {}}, ensure_ascii=False) + "\n")
        fh.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = _lines(OUT)
        if len(lines) > before:
            rec = json.loads(lines[-1])
            return bool(rec.get("ok")), rec.get("result") or ""
        time.sleep(0.3)
    return False, f"TIMEOUT after {timeout}s"


def jcall(tool: str, args: dict | None = None, timeout: float = 600.0):
    """调用并尽力把结果解析成 JSON（结果里可能带中文说明包装）。"""
    ok, text = call(tool, args, timeout)
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return ok, json.loads(m.group(0)), text
        except json.JSONDecodeError:
            pass
    return ok, None, text


# ── 排产优先级 ────────────────────────────────────────────────────────────
PROD_PRIORITY = [
    "DISTRICT_CAMPUS",
    "DISTRICT_COMMERCIAL_HUB",
    "DISTRICT_HARBOR",
    "DISTRICT_THEATER",
    "DISTRICT_HOLY_SITE",
    "DISTRICT_ENCAMPMENT",
    "BUILDING_MONUMENT",
    "BUILDING_GRANARY",
    "BUILDING_WATER_MILL",
    "BUILDING_WALLS",
    "UNIT_BUILDER",
    "UNIT_TRADER",
    "UNIT_ARCHER",
    "UNIT_SPEARMAN",
    "UNIT_WARRIOR",
]

# 处于战争时改为「先防守再谈发展」
WAR_PRIORITY = [
    "BUILDING_WALLS",
    "UNIT_ARCHER",
    "UNIT_SPEARMAN",
    "UNIT_WARRIOR",
    "UNIT_BUILDER",
    "BUILDING_MONUMENT",
    "BUILDING_GRANARY",
]

AT_WAR = False


def choose_production(city_id: int) -> tuple[str, str] | None:
    """给一个停产的城市挑一个可造项，返回 (item_type, item_name)。"""
    ok, data, _ = jcall("get_city_production", {"city_id": city_id}, timeout=120)
    if not ok or not data:
        return None
    opts = (data.get("facts") or {}).get("options") or []
    by_name = {o["item_name"]: o for o in opts}

    order = WAR_PRIORITY if AT_WAR else PROD_PRIORITY

    # 战争状态下优先防守与兵力
    if AT_WAR:
        for name in order:
            o = by_name.get(name)
            if o and o["turns"] <= 30:
                return o["category"], name
    # 和平状态下先补建造者，再按优先级挑选
    if not AT_WAR and "UNIT_BUILDER" in by_name:
        if by_name["UNIT_BUILDER"]["turns"] <= 20:
            return "UNIT", "UNIT_BUILDER"
    for name in order:
        o = by_name.get(name)
        if o and o["turns"] <= 40:
            return o["category"], name
    # 3) 兜底：最快的项目
    usable = [o for o in opts if o.get("turns", 999) > 0]
    if usable:
        o = min(usable, key=lambda x: x["turns"])
        return o["category"], o["item_name"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--stall-limit", type=int, default=3,
                    help="连续多少个回合回合号没有前进就停止")
    args = ap.parse_args()

    log_lines: list[str] = []

    def log(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log_lines.append(line)
        (HERE / "_play.log").write_text("\n".join(log_lines), encoding="utf-8")

    last_turn = None
    stalled = 0

    for i in range(args.max_turns):
        ok, data, raw = jcall("get_game_overview", timeout=180)
        if not ok or not data:
            log(f"ABORT: get_game_overview 失败: {raw[:300]}")
            return 1

        m = re.search(r"回合\s*(\d+)", raw)
        turn = int(m.group(1)) if m else -1
        m2 = re.search(r"得分：\s*(\d+)", raw)
        score = m2.group(1) if m2 else "?"
        log(f"--- 回合 {turn} 得分 {score} (第 {i + 1}/{args.max_turns} 次推进)")

        if "GAME OVER" in raw.upper() or "胜利" in raw and "游戏结束" in raw:
            log("检测到游戏结束信号，停止。")
            break

        if turn == last_turn:
            stalled += 1
            if stalled >= args.stall_limit:
                log(f"ABORT: 回合号连续 {stalled} 次没有前进，可能卡住。")
                return 2
        else:
            stalled = 0
        last_turn = turn

        # 研究/市政是否断档，以 get_tech_civics 的 current_* 为准。
        # get_game_overview 返回的是文本（解析不出指标），get_turn_brief 的
        # turns_remaining 又滞后，两者都会漏判，导致回合停在「选择研究」。
        ok2, tc, _ = jcall("get_tech_civics", timeout=180)
        facts = (tc or {}).get("facts") or {}
        cur_tech = str(facts.get("current_research") or "").strip()
        cur_civic = str(facts.get("current_civic") or "").strip()
        need_tech = cur_tech in ("", "None", "nil")
        need_civic = cur_civic in ("", "None", "nil")

        if need_tech or need_civic:
            if need_tech:
                techs = facts.get("available_techs") or []
                if techs:
                    pick = min(techs, key=lambda t: t.get("turns", 999))
                    log(f"  研究 -> {pick['name']} ({pick.get('turns')} 回合)")
                    call("set_research", {"tech_or_civic": pick["tech_type"], "category": "tech"})
                else:
                    log("  无可选科技")
            if need_civic:
                civics = facts.get("available_civics") or []
                if civics:
                    pick = min(civics, key=lambda c: c.get("turns", 999))
                    log(f"  市政 -> {pick['name']} ({pick.get('turns')} 回合)")
                    call("set_research", {"tech_or_civic": pick["civic_type"], "category": "civic"})
                else:
                    log("  无可选市政")

        # ── 城市排产 ──
        ok3, cities, _ = jcall("get_cities", timeout=180)
        for c in ((cities or {}).get("facts") or {}).get("cities", []):
            cur = c.get("currently_building") or ""
            left = c.get("production_turns_left") or 0
            if cur and left and left > 0:
                continue
            choice = choose_production(c["city_id"])
            if choice:
                itype, iname = choice
                log(f"  {c['name']} 排产 -> {iname}")
                ok4, txt = call("set_city_production", {
                    "city_id": c["city_id"], "item_type": itype, "item_name": iname,
                }, timeout=180)
                if not ok4 or "失败" in txt:
                    # 区域需要坐标，跳过这次排产
                    log(f"    (排产未成功: {txt.strip().splitlines()[-1][:90] if txt else ''})")

        # ── 外交：一律拒绝交易、拒绝对话、尝试求和 ──
        okt, tr, _ = jcall("get_pending_trades", timeout=120)
        for d in ((tr or {}).get("facts") or {}).get("deals", []) or []:
            pid = d.get("other_player_id")
            log(f"  拒绝 {d.get('other_player_name')}(pid={pid}) 的交易提议")
            call("respond_to_trade", {"other_player_id": pid, "accept": False}, timeout=120)

        okp, dip, _ = jcall("get_pending_diplomacy", timeout=120)
        for s in ((dip or {}).get("facts") or {}).get("sessions", []) or []:
            pid = s.get("other_player_id") or s.get("player_id")
            log(f"  外交对话 pid={pid} -> NEGATIVE")
            call("respond_to_diplomacy", {"other_player_id": pid, "response": "NEGATIVE"},
                 timeout=120)

        okdi, dip2, _ = jcall("get_diplomacy", timeout=180)
        global AT_WAR
        AT_WAR = False
        for c in ((dip2 or {}).get("facts") or {}).get("civs", []) or []:
            if c.get("is_at_war"):
                AT_WAR = True
                log(f"  尝试与 {c.get('civ_name')} 求和")
                oki, txt = call("propose_peace", {"other_player_id": c["player_id"]}, timeout=120)
                log(f"    ok={oki} {(txt or '').strip().splitlines()[-1][:90]}")
        if AT_WAR:
            log("  [战时] 城市转为防守排产")

        # ── 时代着力点 ──
        okd, ded, _ = jcall("get_dedications", timeout=120)
        dfacts = (ded or {}).get("facts") or {}
        if dfacts.get("selections_allowed", 0) and not dfacts.get("active"):
            # 雄伟壮丽（每建 1 个新区域 +1 时代得分）有利于爬出黑暗时代
            idx = 2 if any(c.get("index") == 2 for c in dfacts.get("choices", [])) else 0
            log(f"  选择着力点 index={idx}")
            okc, txt = call("choose_dedication", {"dedication_index": idx}, timeout=120)
            log(f"    ok={okc} {txt.strip().splitlines()[-1][:80] if txt else ''}")

        # ── 城邦使者 ──
        okcs, cs, _ = jcall("get_city_states", timeout=120)
        cfacts = (cs or {}).get("facts") or {}
        if cfacts.get("tokens_available", 0) > 0:
            cands = [c for c in cfacts.get("city_states", [])
                     if c.get("can_send_envoy") and c.get("city_state_type") == "Scientific"]
            if not cands:
                cands = [c for c in cfacts.get("city_states", []) if c.get("can_send_envoy")]
            if cands:
                tgt = min(cands, key=lambda c: c.get("envoys_sent", 0))
                log(f"  派使者 -> {tgt['name']} (现有 {tgt.get('envoys_sent')})")
                call("send_envoy", {"player_id": tgt["player_id"]}, timeout=120)

        # ── 世界议会：每回合都注册投票器 ──
        # 议会是在 end_turn 执行过程中才打开的，事前拿不到当次议案哈希、
        # 甚至看不到议案列表。所以这里用 resolution_type 作持久键
        # （注册时看不到、开会时按类型匹配），没有已知议案时传空列表，
        # 让处理器退回默认策略（均分外交支持、投 A 案）。
        # 不注册的话 end_turn 会停在议会等人工投票，整条流水线就卡死了。
        okw, wc, _ = jcall("get_world_congress", timeout=120)
        wfacts = (wc or {}).get("facts") or {}
        votes = []
        for r in wfacts.get("resolutions", []) or []:
            targets = r.get("possible_targets") or []
            tgt = 0
            if targets:
                try:
                    tgt = int(str(targets[0]).split(":")[0])
                except ValueError:
                    tgt = 0
            votes.append({
                "hash": r.get("resolution_hash"),
                "type": r.get("resolution_type"),
                "option": 1,
                "target": tgt,
                "votes": 3,
            })
        okq, txt = call("queue_wc_votes", {"votes": json.dumps(votes)}, timeout=120)
        log(f"  注册议会投票器 ({len(votes)} 条策略) ok={okq} "
            f"{(txt or '').strip().splitlines()[-1][:70]}")

        call("skip_remaining_units", timeout=180)
        # 模态弹窗会阻塞引擎，先清掉再结束回合
        call("dismiss_popup", timeout=120)
        ok6, txt = call("end_turn", {
            "tactical": "自动执政官回合：跳过其余单位并结束回合。",
            "strategic": "维持研究/市政不断档，城市持续排产。",
            "planning": "由 play.py 自动推进，人工在关键节点介入。",
            "tooling": "常驻 MCP 守护进程，调用前自动聚焦游戏窗口。",
            "hypothesis": "持续排产与不断档研究能稳定提升得分。",
        }, timeout=1200)
        log(f"  end_turn ok={ok6}")
        if not ok6:
            log(f"ABORT: end_turn 失败: {txt[:400]}")
            return 3
        m3 = re.search(r"回合\s*(\d+)\s*→\s*(\d+)", txt)
        if m3:
            log(f"  -> 进入回合 {m3.group(2)}")

    log("完成本轮自动推进。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
