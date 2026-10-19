# 06: 科研、市政、政体与时代承诺确认

**What to build:** 选择类操作等待原生目标状态，不因旧读数换通道重发。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 科研／市政删除推断式 GameCore fallback，保留真实资格检查。
- [x] 政体和承诺绑定当前世界／时代和目标选择；已有目标返回 no-op。
- [x] 延迟和未知保留原意图；所有入口门禁一致。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

- 实施提交 `39012f2`；完成前合入集成分支 `30685ac`，合并提交 `62e39ac`。仅以已有 ActionLifecycle 保存四类选择的原意图、范围和领域回执。
- 科研／市政用原生 Hash 与 `VALUE_EXCLUSIVE` 单次提交，目标 Index 实际匹配后确认；删除 GameCore fallback 和旧直接 setter。资格、已完成、已选与未知读数仍由真实原生事实判断。
- 政体提交与解锁查询改用 Hash。实际当前政体匹配才报告 `GOVERNMENT_CHANGED`；原生 accepted=true、原先未处于无政府期且新结束回合与原目标预计时长匹配时报告 `GOVERNMENT_CHANGE_STARTED`，释放本次命令门禁，允许后续动作推进。未伪造未来最终政体。
- 时代承诺绑定原时代与指定 Index，核对当前候选、可选数量和实际 active 目标。重复已选不消费下一项；时代变更后锁存未知。
- 新增 `test_player_choices.py` 35 项 MCP／GameState＋真实 Lua 测试；更新科研原生 adapter，故意区分 Hash／Index 并计数 UI、GameCore 与进度写入。覆盖延迟、取消、执行后丢回应、原生抛错、读回异常、no-op、跨世界／时代、重复请求和跨入口门禁；无政府期启动后验证下一普通动作可真实提交。
- 合并后相关 10 个测试文件 **720 passed**；8 个改动 Python 文件 AST 解析通过，`git diff --check` 通过。测试命令为 `PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest tests/test_player_choices.py tests/test_research_eligibility.py tests/test_civic_progress.py tests/test_game_state.py tests/test_server.py tests/test_pending_dispatch.py tests/test_tile_purchase_confirmation.py tests/test_governor_confirmation.py tests/test_unit_transforms.py tests/test_trader_confirmation.py -q`。
- 离线验证；未连接实机、未 push。原生调用抛错或无政府期受理／区间证据不全时保留 unknown，不重发；多回合最终效果由既有查询读取。
