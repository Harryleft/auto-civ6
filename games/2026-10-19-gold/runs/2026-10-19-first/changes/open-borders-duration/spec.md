# B11：开放边境的原生期限绑定

Status: resolved

## Problem Statement

T55模型准备以10外交支持换取巴西收入并互通边境，以重建商路和继续探索。工具只给OPEN_BORDERS设置subtype，未设置期限；#953实际进入的双方条款duration=0，原生四参GetPossibleDealItems同一参与者当前给出的期限为25、IsValid=true。零期限没有准确表达普通玩家的原生开放边境交易。预览#951/952给出5gpt反报价，#953提交该表面相同条件却拒绝；期限错误已实证，但拒绝是否由此造成须修复后验证。

## Solution

让明确选择的开放边境条款使用同一原生working deal、同一方与交易对象的可用协议摘要，绑定该原生条款的期限及必要值字段，提交前后核对完整条款。没有可读唯一原生选项时明确拒绝，不猜0/25/速度换算。保留原预览、反报价、明确接受和关闭协议。

## User Stories

1. As a player, I want offered open borders bound to the current native duration, so that my deal has the intended term.
2. As a player, I want requested open borders bound independently for the counterpart, so that both directions remain accurate.
3. As a maintainer, I want exact native participants and working deal used for eligibility, so that unrelated options cannot authorize this deal.
4. As a player, I want the native eligible summary selected by the explicit agreement subtype, so that no policy is chosen for me.
5. As a maintainer, I want unavailable, unreadable or ambiguous native summaries refused before evaluation, so that no duration is guessed.
6. As a player, I want full native subtype/duration/value readback and fingerprint retained, so that changed terms remain a counteroffer.
7. As a player, I want failed preparation safely closed through the existing owned no-send path, so that missing options do not deadlock play.
8. As a player, I want lost replies confirmed without resubmission, so that agreements remain single-submit.
9. As a maintainer, I want ordinary gold/favor/resource trade and war agreements retained, so that the repair stays scoped.
10. As a player, I want a preview to stay nonbinding and counters require explicit decisions, so that a repair cannot conclude another deal.
11. As a player, I want the original T55 deal reevaluated after the duration correction, so that any remaining rejection has accurate terms.
12. As a player, I want funding/trade route or subsequent game actions verified in the same run, so that economic play can continue.

## Implementation Decisions

- Bind OPEN_BORDERS from the shipped native four-argument GetPossibleDealItems(from,to,AGREEMENTS,workingDeal) summary; subtype chosen by model intent, duration and required ForType/value from the actual matching native entry. Mirror native agreement preparation, with explicit validity and readable numeric facts, not hardcoded25.
- Preserve current war agreement logic and exact target/WarType; share an existing narrow native-summary helper where it improves clarity, without introducing a generic agreement planner.
- Both offered and requested directions must use their own actual options. Missing/changed/ambiguous/unreadable options fail before SendWorkingDeal, using existing no-send cleanup; no fallback to legacy zero duration.
- Fingerprint already includes duration/value; ensure the original encoded agreement is checked and AI-modified terms cannot be called as-is or autoaccepted.
- Parent closed a fully known rejected trade explicitly (#955), savedT55 (#956), and cleanly closed MCP for pure read-only inspection. No unresolved owner was discarded; a fresh MCP after merge suffices without world restart.

## Testing Decisions

Reuse the approved public MCP/GameState/native-Lua fixtures and existing trade-progress seam. Red→green both directions using real nativeDuration25 with a non25 fixture proving no constant, full subtype/value/duration readback, invalid/missing/ambiguous/unreadable or changed entry refusal/no evaluation, no-replay after lost reply and owned no-send cleanup. Ordinary economic/war agreement regression retained. Parent verifies current T55 actual encoded durations and reevaluates the same displayed economic terms, recording acceptance/rejection honestly, then continues actions/turn. No unrelated test deal is committed.

## Out of Scope

AI pricing policy, autoaccepting counters, guaranteeing AI acceptance, changing existing deals, changing game rules, arbitrary agreement planner or fixing an unproven explanation of all preview/send disagreement.

## Further Notes

t055-trade-check.txt: receiptoriginal has bothowner0/2 OPEN_BORDERS duration0, offerfavor10, requestGPT5 duration30. Bothworkingdeals retain these terms after decline. Same read-only native summary offers owner0/2 OPEN_BORDERS duration25 IsValidtrue ValidationResult1. #951/952 previews counter10favor+ourOB for5gpt+theirOB; #953 INSPECT rejects, #955 explicitdecline/cleanup, #956gold_20261019_t055_trade_check saved. Same run e8b4d6a62dca49bbb256e8264beb18f2. No proposed deal has concluded. ThirdcityNippur founded37,28 T55; originalwarendedT54 and losses retained.
