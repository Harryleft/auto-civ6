"""Offline contract checks for the experimental advisory-only transport."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest


spec = importlib.util.spec_from_file_location(
    "jev_builder_advice", Path(__file__).parents[1] / "scripts/jev_builder_advice.py")
advice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advice)


def reference(path, data):
    path.write_text(json.dumps(data))
    import hashlib
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def packet(tmp_path):
    return {
        "version": 1, "stage": "task", "goal": "Support current university construction",
        "snapshot": {"game_run": "isolated", "civilization": "sumeria", "seed": 1,
                     "player": 0, "turn": 73, "unit_id": 7, "position": [2, 3]},
        "facts": {"units": reference(tmp_path / "units.json", {"position": [2, 3]})},
        "observations": {"worked_tile": "Nearby hill is worked; mine eligibility unknown"},
        "candidates": {
            "a": {"next_step": "move", "target": [3, 3], "description": "Inspect hill",
                  "unknowns": ["native mine eligibility"]},
            "b": {"next_step": "wait", "description": "Keep charge", "unknowns": []},
        },
    }


def response():
    return {"model": "jev-test", "answers": {"next_step": {
        "type": "choice", "choice": "a", "probabilities": {"a": .6, "b": .4},
        "confidence": .2}}}


def reply_review(packet):
    reply = {"status": "valid", "packet_sha256": advice.digest(packet), "response": response()}
    review = {"packet_sha256": advice.digest(packet), "reply_sha256": advice.digest(reply),
              "snapshot_sha256": advice.digest(packet["snapshot"]), "selected_candidate": "a",
              "disposition": "accept", "reason": "Main model approved inspection only",
              "approved_step": "move"}
    return reply, review


def test_changed_observation_file_is_rejected(packet, tmp_path):
    (tmp_path / "units.json").write_text('{"position":[9,9]}')
    with pytest.raises(ValueError, match="Evidence changed"):
        advice.validate_packet(packet, tmp_path)


def test_task_cannot_authorize_construction(packet, tmp_path):
    packet["candidates"]["a"]["next_step"] = "act"
    with pytest.raises(ValueError, match="Invalid step"):
        advice.validate_packet(packet, tmp_path)


def test_unit_identity_cannot_be_a_regex_pattern(packet, tmp_path):
    packet["snapshot"]["unit_id"] = ".*"
    with pytest.raises(ValueError, match="unit_id must be an integer"):
        advice.validate_packet(packet, tmp_path)


@pytest.mark.parametrize("changed", ["packet", "reply", "snapshot", "scope", "escalate"])
def test_review_cannot_cross_bindings_or_widen_permission(packet, changed):
    reply, review = reply_review(packet)
    if changed == "packet":
        packet["goal"] = "Different strategic goal"
    elif changed == "reply":
        reply["response"]["model"] = "another-model"
    elif changed == "snapshot":
        review["snapshot_sha256"] = "stale"
    elif changed == "scope":
        review["approved_step"] = "act"
    else:
        review["disposition"] = "escalate"
    with pytest.raises(ValueError):
        advice.validate_review(packet, reply, review)


@pytest.mark.parametrize("mismatch", [None, "unit_id", "game_run", "position", "turn", "native", "native_position", "native_turn", "native_action", "native_pending", "incomplete_pending", "old_receipt", "conflicting_receipts"])
def test_arrival_requires_reviewed_destination_and_native_receipt(packet, tmp_path, mismatch):
    reply, review = reply_review(packet)
    parent = {key: reference(tmp_path / f"{key}.json", data)
              for key, data in (("packet", packet), ("reply", reply), ("review", review))}
    arrival = copy.deepcopy(packet)
    arrival.update(stage="arrival", parent=parent)
    arrival["snapshot"]["position"] = [3, 3]
    identity = {"civilization": "sumeria", "seed": 1, "player_id": 0, "turn": 73}
    units = "1 units:\n  Builder (UNIT_BUILDER) at (3,3) — moves 1/4 charges:1 [id:7, idx:0]\n    >> Can build: IMPROVEMENT_MINE"
    if mismatch == "native_position":
        units = units.replace("at (3,3)", "at (2,3)")
    elif mismatch == "native_turn":
        identity["turn"] = 72
    elif mismatch == "native_action":
        units = units.replace("IMPROVEMENT_MINE", "IMPROVEMENT_FARM")
    context = {"status": "ready", "checks": {"stable_world_turn": True},
               "identity": identity, "pending": {k: False for k in (
                   "action", "turn", "diplomacy", "congress", "save", "replacement", "popup")},
               "sections": {"units": {"status": "ready", "data": units}}}
    if mismatch == "native_pending":
        context["pending"]["action"] = True
    elif mismatch == "incomplete_pending":
        context["pending"] = {}
    receipt = {"tool": "get_decision_context", "is_error": False,
               "result": {"content": [{"text": json.dumps(context)}]}}
    arrival["facts"]["arrival"] = reference(tmp_path / "arrival.json", receipt)
    if mismatch == "old_receipt":
        arrival["facts"]["arrival"] = arrival["facts"]["units"]
    arrival["candidates"]["a"] = {
        "next_step": "act", "description": "Build native available mine", "unknowns": [],
        "native_evidence": ["arrival"],
        "native_action": {"kind": "improve", "target": [3, 3], "improvement": "IMPROVEMENT_MINE"},
    }
    if mismatch == "conflicting_receipts":
        context["sections"]["units"]["data"] = units.replace("IMPROVEMENT_MINE", "IMPROVEMENT_FARM")
        receipt["result"]["content"][0]["text"] = json.dumps(context)
        arrival["facts"]["later"] = reference(tmp_path / "later.json", receipt)
        arrival["candidates"]["a"]["native_evidence"].append("later")
    if mismatch in {"unit_id", "game_run"}:
        arrival["snapshot"][mismatch] = "other"
    elif mismatch == "position":
        arrival["snapshot"]["position"] = [4, 4]
    elif mismatch == "turn":
        arrival["snapshot"]["turn"] = 72
    elif mismatch == "native":
        arrival["candidates"]["a"]["native_evidence"] = []
    if mismatch:
        with pytest.raises(ValueError):
            advice.validate_packet(arrival, tmp_path)
    else:
        assert advice.validate_packet(arrival, tmp_path) == arrival


@pytest.mark.parametrize("bad", ["choice", "coverage", "nan", "sum", "boolean"])
def test_malformed_remote_answers_are_not_validated(packet, bad):
    result = response()
    answer = result["answers"]["next_step"]
    if bad == "choice":
        answer["choice"] = "unproposed"
    elif bad == "coverage":
        answer["probabilities"].pop("b")
    elif bad == "nan":
        answer["confidence"] = float("nan")
    elif bad == "sum":
        answer["probabilities"]["a"] = .1
    else:
        answer["confidence"] = True
    with pytest.raises(ValueError):
        advice.validate_answer(packet, result)


@pytest.mark.parametrize("failure", [False, True])
def test_one_post_and_no_replay_or_overwrite(packet, tmp_path, monkeypatch, failure):
    packet_path = tmp_path / "task.json"
    packet_path.write_text(json.dumps(packet))
    output = tmp_path / "reply.json"
    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture-secret")
    with patch.object(advice.urllib.request, "build_opener") as opener:
        send = opener.return_value.open
        if failure:
            send.side_effect = TimeoutError("fixture-secret")
        else:
            send.return_value.__enter__.return_value = io.BytesIO(json.dumps(response()).encode())
        result = advice.ask(packet_path, output)
        assert result["status"] == ("error" if failure else "valid")
        assert send.call_count == 1
        original = output.read_bytes()
        with pytest.raises(FileExistsError):
            advice.ask(packet_path, output)
        assert send.call_count == 1
        assert output.read_bytes() == original
        assert b"fixture-secret" not in original


def test_redirect_does_not_send_a_second_request_or_forward_credentials():
    received = []

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/credential-destination")
            self.end_headers()

        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = advice.urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/one-post", data=b"{}",
            headers={"Authorization": "Bearer fixture-secret"})
        opener = advice.urllib.request.build_opener(advice.NoRedirect())
        with pytest.raises(advice.urllib.error.HTTPError) as error:
            opener.open(request, timeout=2)
        assert error.value.code == 302
        assert received == ["/one-post"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
