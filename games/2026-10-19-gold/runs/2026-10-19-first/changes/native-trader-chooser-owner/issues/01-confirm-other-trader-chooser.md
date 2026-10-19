# B25-01 Confirm completed route with another trader's chooser

Status: ready-for-agent
Dependencies: none
Spec: ../spec.md

Worker owns necessary trader code/tests/ticket in isolated worktree. Parent owns live/progress. Read implement-spec/tdd; public MCP/GameState/FakeConn/nativeLua approved. Strict original route/scope and verified current other trader UI ownership; no automatic economic action or confirm-time UI mutation. Preserve same original chooser and unknown guards, lost reply/cancel/pending gates. Parent holds real T131 receipt/other idletrader for pure native proof after explicit UI workaround confirmed the original pending owner. Final native proof + public new trader route + nextturn/save required. Merge integration before report, no live game by worker.
