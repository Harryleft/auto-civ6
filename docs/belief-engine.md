# Civ Belief Engine

The Belief Engine is the persistent world model between game observation and
action.  It records what the agent observed separately from what the agent
believes, predicts, plans, and eventually verifies.

The implementation lives in `src/civ6_belief_engine/`; `civ_mcp` exposes the
MCP adapter. The legacy `civ_mcp.belief_engine` / `civ_mcp.governance`
compatibility shims were removed; import the product package directly.

## Runtime modes

Set `CIV_MCP_BELIEF_MODE` before starting the MCP server:

- `enforce` (default): preserves the existing governance snapshot, belief
  context, action preflight, and event recording behavior.
- `observe`: records observations, but removes governance snapshots, appended
  belief context, and belief-based action gates from the gameplay hot path.
- `off`: bypasses belief event recording as well as all `observe` bypasses.

Use `off` for a minimal A/B run against the legacy path. Core game validation,
end-turn safety, and autosave behavior remain independent of this setting.
`get_game_overview` reports the resolved mode and capabilities as `RUNTIME
POLICY`; Agent hosts should consume that contract instead of maintaining their
own mode tables.

## Runtime loop

```text
MCP query -> automatic Observation -> normalized metrics
          -> get_turn_brief / get_game_overview decision context
          -> Belief / Hypothesis revision
          -> Prediction and dynamic-plan review
          -> Fast / verify / slow routing
          -> game action -> automatic verification trace
          -> Surprise / Contradiction -> replan
```

Every entity supports current-state CRUD.  Deletes are tombstones: the entity
disappears from active state, but the append-only event remains available to
`get_belief_trace` for prediction scoring and post-game attribution.

Local state is stored per game at:

```text
~/.civ6-mcp/beliefs/belief_<civilization>_<seed>.jsonl
```

Telemetry mirrors the same events into each run's `beliefs.jsonl`, allowing
the Convex sync pipeline and game-detail dashboard to materialize current
entities without discarding the audit history.

Raw MCP results belong to the transcript/telemetry layer, not to strategic
state. New Belief Engine events keep only normalized facts/metrics, a
SHA-256 `result_ref`, and links between decisions, actions, outcomes, and
observations. They do not copy the raw result into each entity. Existing event
logs remain readable and are not rewritten in place.

## Entity model

- `observation`: directly observed fact, source, reliability, result
  fingerprint, and normalized metrics. Successful `get_*` MCP calls are
  captured automatically; raw text remains in the transcript/telemetry owner.
- `belief`: an interpretation with probability, confidence, impact, urgency,
  supporting evidence, counter-evidence, falsifiers, and expectations.
- `hypothesis`: one competing explanation in a topic pool. Use
  `rebalance_hypothesis_pool` to update the whole probability distribution.
- `prediction`: falsifiable statement, probability, deadline, and optional
  metric rule. False high-confidence predictions create Surprise entities.
- `plan`: 5/10/20-turn goal with assumptions, success criteria, exit criteria,
  and a scheduled review turn.
- `contradiction`: generated when an observed metric violates a belief's
  declared expectation.
- `world_entity`: typed `GameState` node with stable identity and graph links.
- `goal`, `proposal`, `critic_review`, `council_decision`, and `budget_lock`:
  national-governance state using the same event log and tombstone semantics.
- `decision`: Fast/Slow routing result plus a hash-bound structured action intent.
- `action`: automatic MCP action result linked to its consumed decision, or an
  explicit decision verification.
- `attribution`: candidate failure causes with evidence-weighted posteriors.

Probability describes the event; confidence describes the quality of the
estimate. The first implementation measures revision direction and latency—it
does not claim that model-generated percentages are statistically calibrated.

## Ruleset compatibility

The engine observes the active Civ VI ruleset rather than treating installed
database rows as enabled mechanics. Standard Rules omits Governors,
Ages/Dedications, Alliances, Diplomatic Favor, strategic-resource stockpiles,
and World Congress. Their MCP queries return explicit ruleset errors and do
not create successful observations. Basic diplomacy, owned/nearby resources,
combat estimates, and all other shared systems remain available.

## Declarative conditions

Predictions, belief expectations, and plan exit conditions use the same JSON
rule:

```json
{"metric":"science","operator":">=","value":60}
```

Supported operators are `>=`, `>`, `<=`, `<`, `==`, `!=`, `contains`, and
`not_contains`. `get_game_overview` currently normalizes `turn`, `score`,
`gold`, `gold_per_turn`, `science`, `culture`, `faith`, `favor`, `cities`,
`population`, `units`, `exploration_pct`, and `era_score`. Diplomacy metrics
use keys such as `diplomacy.player_3.at_war` and
`diplomacy.player_3.military`.

## MCP workflow

1. Start each turn once with `get_game_overview`. It returns the authoritative
   `RUNTIME POLICY`; in `enforce` mode the same call also captures and returns
   the typed governance snapshot. Do not immediately duplicate it with
   `get_governance_brief`.
2. Follow the policy capabilities. Use `get_governance_brief` only to refresh
   or recover an enforce-mode snapshot, and use `get_turn_brief` only after
   material evidence, an action outcome, or context recovery.
3. Call `get_belief_state` only when the full current world model is needed;
   the overview and targeted queries are the normal decision inputs.
4. Record important interpretations with `upsert_belief` and competing
   explanations with `upsert_hypothesis`.
5. Add falsifiable claims with `upsert_prediction` and explicit 5/10/20-turn
   commitments with `upsert_dynamic_plan`.
6. For governed strategic choices, use `upsert_strategic_goal`,
   `submit_governance_proposal`, an optional `review_governance_proposal`, and
   `resolve_governance_council`. Critics may agree; an objection is rejected
   unless it cites a stored Observation or identifies an invalid assumption and
   concrete alternative. The council uses hard constraints, budget locks,
   priority, Pareto dominance, and opportunity cost without reducing national
   strategy to one weighted score.
7. When the runtime policy reports enforced routing, call
   `route_belief_decision` for governed actions and pass the council-selected
   structured `action_intent`. The common harness wrapper consumes this
   authorization exactly once; calling a gated action without it is rejected
   before touching the game, and a `slow` route remains blocked.
   A `verify_then_fast` route requires the declared fresh query with matching
   parameters and required fact/metric keys. An unrelated `get_*` result cannot
   satisfy the gate.
8. Treat nearby hostile units as a verification trigger, not as evidence that
   a route is unsafe. Call `get_combat_estimate`, then pass its effective
   strengths, HP, modifiers, and expected damage to `assess_route_combat_risk`.
   Without that complete quantitative assessment, the route belief is not
   changed.
9. In a recording mode, call `get_turn_brief` after material new evidence or
   an action outcome.
   The harness already stores every successful action result as an
   Observation and runs review automatically; this call exposes the next
   decision gate. Overview and normal `get_*` queries include a compact gate
   context in their output.
10. Link the selected decision to its real outcome with
   `record_action_verification` when the normal MCP result is insufficient.
11. Read `get_belief_metrics` and `get_belief_trace` for calibration and
   post-game analysis.

Use `update_belief_entity` for corrections and `delete_belief_entity` for
current-state deletion. Do not encode interpretation into Observation text;
that destroys the fact/inference boundary the engine is intended to measure.
