"""Evaluate captured K1/J2 stdout without connecting to Civilization VI.

This manual verifier does not run the smoke tests itself and does not change
the entry point.  It only evaluates stdout captured from already-completed
manual K1 and J2 runs.

Example:

    .venv/bin/python tests/manual/verify_runtime_cutover.py \\
      --k1-output /tmp/k1.stdout --j2-output /tmp/j2.stdout
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import NoReturn


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from civ_mcp.runtime.eval_harness import EvidenceFormatError, assess_cutover_evidence, load_json_documents


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate captured Runtime K1/J2 evidence without changing Civ6."
    )
    parser.add_argument("--k1-output", required=True, type=Path)
    parser.add_argument("--j2-output", required=True, type=Path)
    return parser.parse_args()


def main() -> NoReturn:
    args = _arguments()
    try:
        report = assess_cutover_evidence(
            load_json_documents(args.k1_output),
            load_json_documents(args.j2_output),
        )
    except (OSError, EvidenceFormatError) as exc:
        print(f"无法评估 Runtime cutover evidence：{exc}", file=sys.stderr)
        raise SystemExit(2)
    print(
        json.dumps(
            {
                "approved": report.approved,
                "blockers": report.blockers,
                "game_id": report.game_id,
                "k1_branch_id": report.k1_branch_id,
                "recovered_branch_id": report.recovered_branch_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    raise SystemExit(0 if report.approved else 2)


if __name__ == "__main__":
    main()
