# 11: 伟人招募、赞助和拒绝确认

**What to build:** 伟人池操作绑定原 individual，核对资源和招募／跳过结果。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

**Owner:** implement_01 — codex/mutation-11

- [x] 付费赞助不会重复消费；旧伟人单位不能冒充新招募。
- [x] 拒绝重入不会误拒绝下一位候选。
- [x] 原生资格、未知、取消和世界改变均保留正确状态。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。

## Answer

- `recruit_great_person`、`patronize_great_person`、`reject_great_person` 复用 ActionLifecycle 和 `_ActionEvidence`，保留原 individual、货币、世界／回合／玩家和原生费用。MCP 与直接 GameState 同意图重入只确认；未决期间其他消费、拒绝或推进不覆盖原请求。
- 招募核对原 `timeline.Cost` 与 class 点数扣除；赞助核对原指定金币／信仰费用。两者都要求完整单位 ID 不在提交前集合内，且 `GetGreatPerson():GetIndividual()` 精确匹配原候选。
- 拒绝消耗该类别伟人点数，修正旧 `faith_cost` 和“自动换下一位”的文案。确认要求原生请求进入并返回、核心空闲、原点数费用扣除、原 individual 的招募／拒绝资格均消失；不要求原候选从其他玩家的全局池中移除。
- 原生资格必须明确为 true。缺失费用／枚举、已领取或未知候选在进入原生操作前拒绝；取消、丢回应、空回应、原生进入后抛错、读回错误及世界变化保留未决。
- 原生依据：本机 `Base/Assets/UI/Popups/GreatPeoplePopup.lua:690–779` 的 timeline／费用／资格，以及 `Base/Assets/Text/en_US/InGameText.xml:4746–4748` 的拒绝点数说明。未虚构 rejected getter 或事件。

## Verification

- TDD 实际 red→green：丢回应赞助原先仅报 Error 丢失未决；延迟招募原先直接报成功；延迟拒绝原先报 `faith_cost`。新增公开 MCP／GameState＋真实 Lua 5.1／原生 adapter 场景替代 3 个仅验证 ACK 文本的旧测试。
- 本票 76 个场景覆盖正常、延迟、取消、执行后丢回应、空回应、读取失败、原生拒绝／抛错、旧单位／错误 individual、精确费用、池变更重入、世界绑定和跨入口门禁；故障注入发现并修复缺失金币枚举时误选信仰的 Lua 空值分支。
- 合入最新 `codex/mutation-lifecycle`（`21bef79`）后，以下命令 **822 passed**：

```sh
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest -q tests/test_great_person_pool_confirmation.py tests/test_game_state.py tests/test_server.py tests/test_tile_purchase_confirmation.py tests/test_action_confirmation.py tests/test_item_purchase_confirmation.py tests/test_trader_confirmation.py tests/test_player_choices.py tests/test_unit_tasks.py tests/test_research_eligibility.py
```

- 7 个改动 Python 文件语法检查、`git diff --check` 和规格相对链接检查通过。未连接实机，未 push。
- 代码提交：`c83ca0b`；集成合并提交：`6574a62`。未改变公共生命周期接口。
