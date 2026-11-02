"""Experimental, file-based builder advice. No game access or action dispatch.

The main model authors every candidate, observation and review. Hash checks bind
artifacts; they cannot prove that the game has not changed since an observation.
Runtime remains the only authority for pending, eligibility and single submission.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"),
    ).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_ref(reference, base):
    path = base / reference["path"]
    require(hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"],
            f"Evidence changed: {path}")
    return read(path)


def validate_native_improvement(snapshot, candidate, receipts):
    """Bind this experiment's improvement choice to an actual CLI decision receipt.

    This deliberately supports only improve. Other actions need their own native
    evidence contract, rather than treating every named receipt as eligibility.
    It checks the recorded observation, never current game freshness.
    """
    action = candidate["native_action"]
    require(action["kind"] == "improve", "Only native improvement evidence is supported")
    require(action["target"] == snapshot["position"], "Action target differs from arrival")
    eligible = False
    for receipt in receipts:
        if receipt.get("tool") != "get_decision_context" or receipt.get("is_error") is not False:
            continue
        context = json.loads(receipt["result"]["content"][0]["text"])
        require(context["status"] == "ready" and context["checks"]["stable_world_turn"] is True,
                "Native decision receipt is not ready/stable")
        pending = context["pending"]
        required = {"action", "turn", "diplomacy", "congress", "save", "replacement", "popup"}
        require(required <= pending.keys() and all(value is False for value in pending.values()),
                "Receipt has pending or incomplete pending facts")
        for actual, declared in (("civilization", "civilization"), ("seed", "seed"),
                                 ("player_id", "player"), ("turn", "turn")):
            require(context["identity"][actual] == snapshot[declared], f"Native identity mismatch: {actual}")
        units = context["sections"]["units"]
        require(units["status"] == "ready", "Native units observation is unknown")
        blocks = re.split(r"(?m)^  (?=\S)", units["data"])
        matches = [block for block in blocks if re.search(
            rf"\[id:{snapshot['unit_id']},", block)]
        require(len(matches) == 1, "Native unit is absent/ambiguous")
        block = matches[0]
        x, y = snapshot["position"]
        require(f"(UNIT_BUILDER) at ({x},{y})" in block, "Native builder position mismatch")
        choices = re.search(r"(?m)^    >> Can build: (.+)$", block)
        eligible |= choices is not None and action["improvement"] in choices[1].split(", ")
    require(eligible, "Native receipt does not affirm this improvement")


def validate_packet(packet, base):
    require(packet["version"] == 1, "Unsupported packet version")
    stage = packet["stage"]
    require(stage in {"task", "arrival"}, "stage must be task or arrival")
    require(bool(packet["goal"]), "A main-model goal is required")
    snapshot = packet["snapshot"]
    for field in ("game_run", "civilization", "seed", "player", "turn", "unit_id", "position"):
        require(field in snapshot, f"Missing snapshot.{field}")
    require(type(snapshot["unit_id"]) is int, "unit_id must be an integer")
    require(len(snapshot["position"]) == 2, "Expected unit position [x,y]")
    require(all(type(v) is int for v in snapshot["position"]), "Position must contain integers")
    facts = packet["facts"]
    require(bool(facts), "Current CLI receipts are required")
    receipts = {key: file_ref(reference, base) for key, reference in facts.items()}
    candidates = packet["candidates"]
    require(len(candidates) >= 2, "Include alternatives, including wait or query")
    allowed = {"move", "wait", "query"} if stage == "task" else {"act", "wait", "query"}
    require(any(c["next_step"] in {"wait", "query"} for c in candidates.values()),
            "A wait/query escape must remain available")
    for key, candidate in candidates.items():
        require(candidate["next_step"] in allowed, f"Invalid step at {stage}: {key}")
        require(bool(candidate["description"]), f"Missing description: {key}")
        require("unknowns" in candidate, f"Separate unknowns from observations: {key}")
        if candidate["next_step"] == "move":
            require(len(candidate["target"]) == 2, f"Missing movement target: {key}")
        if candidate["next_step"] == "act":
            evidence = candidate.get("native_evidence", [])
            require(len(evidence) == 1 and evidence[0] in facts,
                    f"Exactly one selected native eligibility receipt required: {key}")
            validate_native_improvement(snapshot, candidate, [receipts[e] for e in evidence])
    if stage == "arrival":
        parent = packet["parent"]
        task = file_ref(parent["packet"], base)
        reply = file_ref(parent["reply"], base)
        review = file_ref(parent["review"], base)
        validate_packet(task, (base / parent["packet"]["path"]).parent)
        require(task["stage"] == "task", "Arrival parent must be a task packet")
        chosen = validate_review(task, reply, review)
        require(chosen["next_step"] == "move", "Arrival requires a reviewed move")
        for field in ("game_run", "civilization", "seed", "player", "unit_id"):
            require(snapshot[field] == task["snapshot"][field], f"Arrival mismatch: {field}")
        require(snapshot["turn"] >= task["snapshot"]["turn"], "Arrival predates task")
        require(snapshot["position"] == chosen["target"], "Not at reviewed destination")
    return packet


def request_for(packet):
    return {
        "model": "jev-latest",
        "state": {k: packet[k] for k in ("stage", "goal", "snapshot", "observations")},
        "questions": {"next_step": {
            "type": "choice",
            "instructions": (
                "Select one bounded next step for this builder under the main-model goal. "
                "Compare travel time, worked-city benefits, finite charges, opportunity "
                "costs and stated uncertainties. Task choices authorize no construction. "
                "Arrival choices use fresh native eligibility; availability alone does "
                "not imply strategic value. Choose query or wait if appropriate. "
                "No choice authorizes execution; the main model reviews every result."
            ),
            "criteria": packet["candidates"],
        }},
    }


def validate_answer(packet, response):
    require(isinstance(response.get("model"), str) and bool(response["model"]), "Missing actual model")
    require(set(response["answers"]) == {"next_step"}, "Unexpected answer coverage")
    answer = response["answers"]["next_step"]
    require(answer["type"] == "choice", "Expected Choice answer")
    require(answer["choice"] in packet["candidates"], "Unknown chosen candidate")
    probabilities = answer["probabilities"]
    require(set(probabilities) == set(packet["candidates"]), "Incomplete distribution")
    values = [*probabilities.values(), answer["confidence"]]
    require(all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in values),
            "Invalid probability/confidence")
    require(abs(sum(probabilities.values()) - 1) < .01, "Distribution does not sum to one")
    return answer


def validate_review(packet, reply, review):
    require(reply["status"] == "valid", "Reply was not validated")
    require(reply["packet_sha256"] == digest(packet), "Reply belongs to another packet")
    answer = validate_answer(packet, reply["response"])
    require(review["packet_sha256"] == digest(packet), "Review belongs to another packet")
    require(review["reply_sha256"] == digest(reply), "Review belongs to another reply")
    require(review["snapshot_sha256"] == digest(packet["snapshot"]), "Review snapshot mismatch")
    require(bool(review["reason"]), "Main-model review reason required")
    choice = review["selected_candidate"]
    require(choice in packet["candidates"], "Review selected an unknown candidate")
    require(review["disposition"] in {"accept", "override", "escalate"}, "Invalid review disposition")
    if review["disposition"] == "accept":
        require(choice == answer["choice"], "Acceptance changed the suggested choice")
    chosen = packet["candidates"][choice]
    if review["disposition"] == "escalate":
        require(chosen["next_step"] in {"query", "wait"}, "Escalation cannot approve an action")
    require(review["approved_step"] == chosen["next_step"], "Review widened the approved step")
    return chosen


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, None)


def ask(packet_path, output):
    packet_path = Path(packet_path)
    packet = validate_packet(read(packet_path), packet_path.parent)
    payload = request_for(packet)
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    request = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone", data=raw,
        headers={"Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"],
                 "Content-Type": "application/json"}, method="POST",
    )
    # Reserve before sending. Reusing an output path never repeats even a failed
    # or interrupted request; a new call requires an explicitly different path.
    record = {"status": "started", "advisory_only": True, "packet_sha256": digest(packet),
              "request_sha256": hashlib.sha256(raw).hexdigest(), "started_unix": time.time()}
    with Path(output).open("x", encoding="utf-8") as stream:
        json.dump(record, stream)
        stream.flush()
        start = time.perf_counter()
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
                result = json.load(response)
            validate_answer(packet, result)
            answer = result["answers"]["next_step"]
            normalized = {"model": result["model"], "answers": {"next_step": {
                k: answer[k] for k in ("type", "choice", "probabilities", "confidence")}}}
            normalized["usage"] = {k: v for k, v in result.get("usage", {}).items()
                                   if k in {"input_tokens", "output_tokens"} and type(v) is int}
            record.update(status="valid", response=normalized)
        except Exception as error:
            # Never serialize request headers, exception text or error bodies.
            record.update(status="error", error_type=type(error).__name__,
                          http_status=getattr(error, "code", None))
        record.update(latency_s=time.perf_counter() - start, finished_unix=time.time())
        stream.seek(0)
        stream.truncate()
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate", help="Offline validation; no network/game calls")
    check.add_argument("packet", type=Path)
    send = commands.add_parser("ask", help="One Jev request; never executes a game action")
    send.add_argument("packet", type=Path)
    send.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("check-review", help="Offline artifact binding check; not execution permission")
    review.add_argument("packet", type=Path)
    review.add_argument("reply", type=Path)
    review.add_argument("review", type=Path)
    args = parser.parse_args()
    if args.command == "ask":
        result = ask(args.packet, args.output)
    else:
        packet = validate_packet(read(args.packet), args.packet.parent)
        if args.command == "check-review":
            validate_review(packet, read(args.reply), read(args.review))
        result = {"status": "valid", "advisory_only": True, "packet_sha256": digest(packet)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
