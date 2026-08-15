#!/usr/bin/env -S .venv/bin/python
"""Belief coverage audit CLI — per-game and fleet support ratios.

Answers the audit question behind the "belief-driven" claim: how many
recorded decisions are actually backed by recorded beliefs? Reads
BeliefEngine JSONL journals (default ~/.civ6-mcp/beliefs/) without a live
game, so it is safe to run at any time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from civ6_belief_engine.coverage import load_journal, summarize_fleet, summarize_game

DEFAULT_JOURNAL_DIR = Path.home() / ".civ6-mcp" / "beliefs"


def _iter_journals(paths: list[str]) -> list[Path]:
    journals: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            journals.extend(sorted(path.glob("belief_*.jsonl")))
        else:
            journals.append(path)
    return journals


def _fmt_ratio(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "—"


def _print_table(summaries: list[dict]) -> None:
    header = (
        f"{'game':32} {'turns':>5} {'decis':>6} {'supp':>5} {'ratio':>7} "
        f"{'beliefs':>7} {'b_auto':>6} {'preds':>6} {'p_auto':>6} {'resolv':>6} {'actions':>8}"
    )
    print(header)
    print("-" * len(header))
    for s in summaries:
        print(
            f"{str(s['game_id']):32} {s['turns_played']:>5} "
            f"{s['decisions_total']:>6} {s['decisions_with_belief_support']:>5} "
            f"{_fmt_ratio(s['belief_supported_decision_ratio']):>7} "
            f"{s['beliefs_total']:>7} {s['beliefs_derived']:>6} "
            f"{s['predictions_total']:>6} {s['predictions_derived']:>6} "
            f"{s['predictions_resolved']:>6} {s['actions_total']:>8}"
        )
    print("-" * len(header))
    fleet = summarize_fleet(summaries)
    print(
        f"{'FLEET (' + str(fleet['games']) + ' games)':32} "
        f"{'':>5} {fleet['decisions_total']:>6} "
        f"{fleet['decisions_with_belief_support']:>5} "
        f"{_fmt_ratio(fleet['belief_supported_decision_ratio']):>7} "
        f"{fleet['beliefs_total']:>7} {fleet['beliefs_derived']:>6} "
        f"{fleet['predictions_total']:>6} {'':>6} "
        f"{fleet['predictions_resolved']:>6} {fleet['actions_total']:>8}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(DEFAULT_JOURNAL_DIR)],
        help=f"Journal files or directories (default: {DEFAULT_JOURNAL_DIR})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable summaries instead of a table",
    )
    args = parser.parse_args()

    journals = _iter_journals(args.paths)
    if not journals:
        print(f"No journals found under: {args.paths}", file=sys.stderr)
        return 1

    summaries = []
    for journal in journals:
        events, skipped = load_journal(journal)
        if not events:
            continue
        summary = summarize_game(events)
        summary["journal"] = str(journal)
        if skipped:
            summary["skipped_lines"] = skipped
        summaries.append(summary)

    if args.json:
        print(json.dumps({"games": summaries, "fleet": summarize_fleet(summaries)}, ensure_ascii=False, indent=2))
    else:
        _print_table(summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
