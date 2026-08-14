# Civ Governance System

The governance layer turns the existing Civ VI harness into a controlled
national decision process. It does not create another state store, model
provider, or graph database. `GameState` remains the typed game boundary and the
Belief Engine JSONL/telemetry stream remains the audit source of truth.

The domain implementation is under `src/civ6_belief_engine/governance/`.
`civ_mcp` remains the MCP-facing game adapter and compatibility surface.

## Control loop

```text
typed GameState snapshot
  -> world entities / relations / metrics
  -> goals and ministerial proposals
  -> evidence-grounded critic review
  -> council arbitration
  -> exact ActionIntent routing
  -> single game writer
  -> Outcome + Observation + belief review
```

Only the common MCP action wrapper writes to Civ VI. A department or critic can
submit structured advice but cannot execute its own proposal.

## Six engineering constraints

1. Probability is the estimated likelihood of success; confidence is the
   quality of the supporting evidence. Both are strict finite values in `[0,1]`
   and are never collapsed into one score.
2. The critic verdict is `agree`, `agree_with_conditions`, or `object`.
   `object` requires a traceable Observation, or an invalidated assumption plus
   a concrete alternative. Disagreement is not a quota.
3. Governance snapshots consume `GameOverview`, `CityInfo`, `UnitInfo`,
   `CivInfo`, and other `GameState` dataclasses directly. Narrated MCP text is a
   presentation format, not the source of the governance graph.
4. The graph is stored as normal event-sourced entities and stable links. There
   is no graph-visualization dependency in this phase.
5. Council order is fixed: hard constraints, critic conditions, budget locks,
   strategic priority, Pareto dominance, then opportunity cost. There is no
   weighted national-strategy total.
6. Budget locks cover consumables and exclusive slots such as gold, faith,
   `city_production:city:<id>`, research, civic, and unit actions.

## MCP workflow

1. Call `get_governance_brief` once at the beginning of the turn. It captures a
   typed same-turn snapshot, retries once if the turn changes during collection,
   and returns ruleset capabilities, total capacity, active locks, and remaining
   budgets. Prior-turn locks are archived automatically.
2. Call `upsert_strategic_goal` for a durable national objective.
3. Each relevant department calls `submit_governance_proposal` with explicit
   constraints, locks, benefits, costs, opportunity cost and action intents.
4. Call `review_governance_proposal` only when the choice merits adversarial
   review. Agreement is a valid result. Counterevidence must name an existing
   Observation ID.
5. Call `resolve_governance_council` with policy budget ceilings and any
   explicitly accepted critic conditions. Live typed capacities are
   authoritative: supplied ceilings may reserve resources but cannot inflate
   current gold, faith, slots, cities, or units.
6. Call `route_belief_decision` using an action intent selected by that council
   decision. The action arguments are hash-bound.
7. If the route is `verify_then_fast`, execute the exact evidence query named by
   the intent after routing. Tool parameters and required facts/metrics must
   match; an unrelated query cannot unlock the action.
8. Execute the MCP action. Authorization moves through `authorized -> executing
   -> succeeded`, or `retryable` after a real failed attempt. Outcomes and
   factual observations are appended automatically.
9. If slow review invalidates the intent, or retrying is no longer rational,
   call `cancel_routed_action` with a concrete reason. The harness appends a
   cancelled, unexecuted Outcome; it never treats cancellation as success.
10. National, scarce-resource, high-impact, highly irreversible, founding,
    city-capture, and war-declaration intents require a council decision even
    when the caller omitted a proposal. This classification is server-derived.
11. If a client disconnects after authorization enters `executing`, reconcile
    the observed result with `record_action_verification`. The hash-bound tool
    must match; success closes the authorization and failure makes it retryable.

An `allowed_turn` intent is dormant before that turn and becomes a hard
obligation when due. Its budget locks remain reserved through the scheduled
turn. Missing the execution window fails closed and requires an explicit
re-proposal; the harness never silently executes it late.

### Evidence contracts (`evidence_requirements`)

`verify_then_fast` routes require an explicit evidence contract on the intent.
Each requirement **must** carry a non-empty `requirement_id`; a contract
without one is rejected by the server before the council meets. Only fresh
observations created *after* `route_belief_decision` (by `sequence`) can
satisfy the contract, so run the named query after routing, not before.

```json
"action_intents": [{
  "intent_id": "intent:pantheon:1",
  "tool": "choose_pantheon",
  "arguments": {"belief_type": "BELIEF_DIVINE_SPARK"},
  "proposal_id": "t75_pantheon",
  "evidence_requirements": [{
    "requirement_id": "req:pantheon:faith_check",
    "tool": "get_pantheon_beliefs",
    "params": {},
    "required_facts": ["summary"],
    "description": "Verify pantheon status and faith sufficiency before founding"
  }]
}]
```

The available `required_facts`/`required_metrics` keys are the ones the belief
engine's normalizer actually emits for that tool (e.g. `get_units` emits
`observed_unit_count` + `unit_ids`); inventing a key silently never matches.

### Budget locks lifecycle

A council-approved proposal's exclusive `budget_locks` stay reserved until
either (a) the next turn rolls over (`release_after_turn < current turn`), or
(b) the routed decision is cancelled — cancellation now archives the locks
belonging to the same `council_decision_id` automatically. A rejected proposal
becomes `resolved` and its ID cannot be resubmitted; re-propose under a new
`proposal_id`.

## Proposal example

```json
{
  "proposal_id": "production:east:walls",
  "department": "production",
  "summary": "Build Ancient Walls in the eastern city before expanding",
  "goal_ids": ["goal:survival"],
  "success": {"probability": 0.8, "confidence": 0.72},
  "priority": 90,
  "hard_constraints": {"city_can_build_walls": true},
  "budget_locks": [
    {
      "resource": "city_production",
      "scope": "city:0:4",
      "exclusive": true,
      "reason": "A city can run only one production order at a time"
    }
  ],
  "benefits": {"capital_survival": 0.8},
  "costs": {"expansion_delay_turns": 1},
  "opportunity_cost": 0.25,
  "action_intents": [
    {
      "intent_id": "intent:walls:4",
      "tool": "set_city_production",
      "arguments": {
        "city_id": 4,
        "item_type": "BUILDING",
        "item_name": "BUILDING_WALLS"
      },
      "proposal_id": "production:east:walls"
    }
  ]
}
```

Routine maintenance actions remain available without a council meeting. The
governance path is for resource conflicts, high-impact actions, or decisions
whose assumptions need explicit evidence and attribution.
