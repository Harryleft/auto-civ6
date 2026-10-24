# pstack in Codex

Read this adapter before following an upstream pstack skill. The upstream files remain unchanged. This project installs pstack as project-local Codex skills, rather than registering a Cursor plugin.

## Local skills and paths

`<pstack>` is the directory containing this file. Read bundled `/name` skills from `<pstack>/skills/<name>/SKILL.md`; resolve their relative references from the upstream file's directory. The discoverable Codex entry is `$pstack-<name>`. Keep pstack's `tdd` and `teach` separate from the project's existing skills with those names. Replace upstream GitHub pstack file links with the local copy when reading resources.

## Tools and delegation

Map Read to the available file-reading tool, Grep/Glob to `rg`, and shell commands to the available execution tool. Map AskQuestion to the available user-input tool. Use a plan tool if provided; otherwise keep a short checklist for multi-step tasks. Use only fields supported by the current tool schema.

When a pstack workflow calls for subagents and delegation is authorized, use the current collaboration tools. For `poteto-agent`, read `<pstack>/agents/poteto-agent.md` and include its bootstrap instructions, this adapter, and the absolute upstream poteto-mode path in the delegate's brief. For `comment-sicko`, use a read-only reviewer brief from `<pstack>/agents/comment-sicko.md`. Respect the session's concurrency limit and assign disjoint files to editing workers. A worktree or another user-owned chat is not a substitute for a subagent; create either only under the applicable project and session rules.

## Models and setup

By default omit model and reasoning overrides so delegates inherit the parent chat. Upstream Claude and Grok slugs are Cursor defaults, not evidence that those models are available here. For comparisons using one available model, report that the review is single-model.

When `<pstack>/codex-models.md` exists, read its role choices before choosing models and include it in delegate briefs. If the user invokes setup-pstack, use its role-selection workflow with the model identifiers and reasoning levels actually allowed by the current session. Read/write optional role choices in that file instead of Cursor rules. Validate model/effort combinations before saving. With no such file, inherit the parent model. Do not change global Codex settings as part of project-local setup.

## Platform dependencies and project authority

Cursor custom modes, Bugbot execution, Cursor Automations, cloud-agent environments, and Cursor transcript paths do not become available by installing these files. Use an existing equivalent only when its capabilities and the task's authorization match; otherwise name the unavailable step and its effect on completion. Report a required unmet verification as a blocker. Never claim multi-model, live, UI, or game verification from static inspection.

For a skill from another plugin, such as cursor-team-kit/deslop or control-ui, first look for an installed equivalent. If absent, state that dependency and perform an authorized equivalent check when feasible; do not install another plugin or silently mark the step passed.

Follow the current user instructions and project AGENTS.md for Git, real-game access, external actions, memory edits, autonomy, and completion. Installing or invoking a workflow does not authorize push, merge, deployment, messages, game operations, recurring automations, or global policy changes. Run only checks appropriate to the requested task.
