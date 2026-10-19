# B01：支持用户授权的新神级基准

Status: resolved

## Problem Statement

用户要求从新建神级 T1 存档持续推进至终局，但当前加载器仅认识旧不朽基准的指纹，在游戏加载前拒绝新文件。

## Solution

把已经用户授权、经原生菜单和文件指纹核对的新存档登记为第二个受信基准；加载继续完整核对文明、种子、回合和就绪，保存检查点继续绑定独立轮次。保留旧基准及原有成果。

## User Stories

1. As a player, I want to load my explicitly authorized deity save, so that the new game can begin.
2. As a player, I want the exact file hash checked, so that a replaced save is rejected.
3. As a player, I want civilization, seed and turn verified, so that a successful load identifies the intended world.
4. As a player, I want a fresh run when loading either original baseline, so that checkpoints from earlier attempts cannot be mixed.
5. As a player, I want run ownership retained across an MCP restart, so that recovery can use this run's confirmed checkpoint.
6. As a player, I want both baseline files protected against overwriting, so that original evidence remains available.
7. As a maintainer, I want unknown files to remain refused without loading, so that expanding the trusted list does not remove load safeguards.
8. As a maintainer, I want the original baseline behavior preserved, so that prior verification remains reproducible.
9. As a player, I want the new game to advance from T1 to T2 after repair, so that validation includes real gameplay.
10. As a maintainer, I want this pre-load blocker and verification archived with the new run, so that later regression can locate it.

## Implementation Decisions

- Use one small named baseline registry, with exact archived digest and expected civilization, seed and turn for each authorized original.
- Keep the old baseline as the backwards-compatible default for existing callers.
- Begin ownership only after a baseline has passed the existing complete loader confirmation; persist the selected digest and unique run ID.
- Protect all registered original names before any native save submission, independently of current file digest.
- The new original's actual filename is benchmark_gold_tun_1. Do not rename or overwrite the source.
- Do not relax pending action, unknown load, checkpoint completion or file-change barriers.

## Testing Decisions

- Existing checkpoint and public GameState/lifecycle seams; temporary files and existing FakeConn transport. Test observable acceptance/refusal, run ownership, single load submission and save protection.
- Verify the new and old baselines, changed files, run changes and process-memory reload; do not access Steam or real saves in offline tests.
- Live: load the exact approved original once, read identity and configuration, execute normal opening actions, confirm T2, save a distinct checkpoint and verify recovery metadata.
- User confirmed these existing seams and live scope before implementation.

## Out of Scope

- Arbitrary file imports, untrusted baseline registration, automatic discovery of new originals, changes to gameplay rules or resources.
- New background observers, fixed-policy full-game driver, remote publication, push, or discarding existing saves.

## Further Notes

B01 occurred before loading (target T1). File SHA-256 is 0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf. The original menu metadata confirms deity Sumeria, quick tiny four-leaf-clover map. Header configuration tags give expected seed -1896272000; live loading must verify it before gameplay.
