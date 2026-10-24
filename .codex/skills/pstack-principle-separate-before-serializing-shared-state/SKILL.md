---
name: pstack-principle-separate-before-serializing-shared-state
description: "pstack: Apply when concurrent actors might write to the same file, branch, key, or state object. Eliminate the sharing first; serialize structurally only when one shared writer is a real invariant."
---

Read [the Codex adapter](../../skill-sources/pstack/CODEX.md), then read and follow [the upstream principle-separate-before-serializing-shared-state skill](../../skill-sources/pstack/skills/principle-separate-before-serializing-shared-state/SKILL.md). Resolve upstream relative references from that upstream file. Use the adapter's mapping for other bundled skills and platform-specific tools.
