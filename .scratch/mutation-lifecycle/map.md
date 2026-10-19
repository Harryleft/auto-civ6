# 状态变更实施任务图

**Status:** claimed

[规格](spec.md)；[实施调研](research.md)；本地 Markdown tracker；基线 `d3450a3`。

| 票 | 交付 | Blocked by | 状态 |
| --- | --- | --- | --- |
| 01 | [地块购买与可复用的未决动作契约](issues/01-purchase-tile.md) | 无 | resolved |
| 02 | [存档取消与原生异常的未知保留](issues/02-save-cancellation.md) | 无 | resolved |
| 03 | [购买单位和建筑的结果确认](issues/03-purchase-item.md) | 01 | resolved |
| 04 | [单位升级、晋升与删除的身份连续性](issues/04-unit-transforms.md) | 01 | resolved |
| 05 | [总督任命、晋升与指派闭环](issues/05-governors.md) | 01 | resolved |
| 06 | [科研、市政、政体与时代承诺确认](issues/06-choices.md) | 01 | resolved |
| 07 | [政策付费解锁与换卡的部分完成](issues/07-policies.md) | 01 | resolved |
| 08 | [自动探索、治疗、警戒与休眠确认](issues/08-readiness.md) | 01 | resolved |
| 09 | [建造、维修、移除与道路确认](issues/09-tile-actions.md) | 01 | resolved |
| 10 | [商路与商人转移确认](issues/10-trade-routes.md) | 01 | resolved |
| 11 | [伟人招募、赞助和拒绝确认](issues/11-great-people-pool.md) | 01 | resolved |
| 12 | [伟人激活与传教结果范围](issues/12-unit-effects.md) | 01 | resolved |
| 13 | [占城决定绑定原待决城市](issues/13-city-decisions.md) | 01 | resolved |
| 14 | [间谍旅行与任务确认](issues/14-spy-assignments.md) | 01 | resolved |
| 15 | [万神殿与创建宗教的阶段确认](issues/15-religion.md) | 01 | resolved |
| 16 | [外交关系和宣战清理归属](issues/16-relationships.md) | 01 | resolved |
| 17 | [议会打开、投票与提交门禁](issues/17-congress.md) | 01 | resolved |
| 18 | [全入口收口、回归与双轴审查](issues/18-integrate-verify.md) | 02, 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13, 14, 15, 16, 17 | claimed（待双轴审查） |

01–17 已合入；18 的全入口复核、离线回归与初审发现修复已合入，保持 claimed，待 Standards／Spec 修复差异复核完成后闭票。

## Decisions-so-far

- 单一 MCP 控制游戏；不设计人工并发防御。
- 复用现有公开 MCP／GameState → 真实生成 Lua → 原生 adapter 的验证 seam。
- 每票独立工作树和分支，汇入一个集成分支；不 push。

- 用户已确认 18 票颗粒度、依赖关系和测试 seam；集成分支 `codex/mutation-lifecycle`，审查基线 `d3450a3`。
- 02 已由 `codex/mutation-02` 快进合入（实现 `c4e68c4`，闭票 `31fa2e0`）；集成后保存、原生终局边界、回合与 GameState 四个相关测试文件共 421 项通过。详见本票 Answer。
- 01 已由 `codex/mutation-01` 合入 `15d999d`（实现 `ab45699`，闭票 `fe35624`）；地块购买、使者、保存、原生终局边界、回合与 GameState 六个测试文件联合验证共 589 项通过。后续票复用 `_ActionEvidence`，接入说明位于 `/tmp/civ6-mutation-action-interface.md`。
- 03 已由 `codex/mutation-03` 快进合入（实现 `c26dc16`，闭票 `a1b465c`）；集成后购买、地块、使者与 GameState 四个相关测试文件共 372 项通过。详见本票 Resolution。
- 05 已由 `codex/mutation-05` 快进合入（实现 `70cfdfe`，闭票 `a8598e0`）；集成后总督、两类购买与 GameState 四个相关测试文件共 341 项通过。详见本票 Answer。
- 04 已由 `codex/mutation-04` 快进合入（实现 `efc1ef3`，闭票 `c48d527`）；集成后单位转换、未决分派、作用域、GameState、生命周期和 server 六个相关测试文件共 549 项通过。详见本票 Answer。
- 10 已由 `codex/mutation-10` 快进合入（实现 `20c114c`，闭票 `ffd62b9`）；集成后商路、单位转换、未决分派、GameState 和 server 五个相关测试文件共 459 项通过。详见本票 Resolution。
- 08 已由 `codex/mutation-08` 快进合入（实现 `d82944f`，闭票 `44751ce`）；集成后单位任务、readiness、单位转换、商路、未决分派和 server 六个相关测试文件共 374 项通过。详见本票 Answer。
- 06 已由 `codex/mutation-06` 合入 `7c06b94`（实现 `39012f2`，闭票 `ee3fa05`）；集成后选择、科研、市政、单位任务、readiness、GameState、未决分派和 server 八个相关测试文件共 534 项通过。详见本票 Answer。
- 11 已由 `codex/mutation-11` 快进合入（实现 `c83ca0b`，闭票 `e236280`）；集成后伟人池、GameState、server、动作确认和购买五个相关测试文件共 522 项通过。详见本票 Answer。
- 07 已由 `codex/mutation-07` 快进合入（实现 `1dcacb2`，闭票 `81a604e`）；集成后政策、选择、总督、GameState、未决分派和 server 六个相关测试文件共 503 项通过。详见本票 Answer。
- 09 已由 `codex/mutation-09` 快进合入（实现 `48a159d`，闭票 `f6d4c89`），12 已由 `codex/mutation-12` 合入 `5a67fd2`（实现 `5b374fb`，闭票 `d59aac3`）；合并保留两组 full ID 分派与准确结果说明。地块、候选、单位效果、任务、GameState、未决分派和 server 七个相关测试文件共 554 项通过。详见两票 Answer。
- 13 已由 `codex/mutation-13` 快进合入（实现 `2349c5e`，闭票 `9ebffde`）；集成后占城决定、地块购买、GameState、未决分派和 server 五个相关测试文件共 515 项通过。详见本票 Answer。
- 15 已由 `codex/mutation-15` 快进合入（实现 `82d7ee2`，闭票 `a537ba5`）；集成后宗教阶段、单位效果、政策阶段、GameState、未决分派和 server 六个相关测试文件共 617 项通过。详见本票 Answer。
- 14 已由 `codex/mutation-14` 合入 `010952a`（实现 `98d0885`，闭票 `e67b8c0`）；集成后间谍指派、escape、回合、GameState、未决分派和 server 六个相关测试文件共 552 项通过。详见本票 Answer。
- 17 已由 `codex/mutation-17` 快进合入（实现 `cc36207`、`bedb8f8`，闭票 `179d60f`）；集成后议会确认、票单、打开、回合、原生推进、GameState、未决分派和 server 八个相关测试文件共 563 项通过。详见本票 Answer。
- 16 已由 `codex/mutation-16` 合入 `805d8b6`（实现 `0c3b86a`，闭票 `316005a`）；保留议会门禁和精确零提交拒绝修复。关系、来约最终回应、交易、来约协商、议会确认、回合、GameState、未决分派、server 与作用域十个相关测试文件共 719 项通过。详见本票 Answer。
- 18 已由 `codex/mutation-18` 快进合入 `920ae31`（实现 `3fa68cd`，记录 `716190a`、`920ae31`）。全量离线回归 **3563 passed in 124.08s**，日志 `/tmp/civ6-ticket18-full-reviewed.log`；`compileall -q src tests` 通过，26 个 Markdown 文件／156 个本地链接及锚点检查无未解析项。集成没有冲突或代码修改，源代码与该受测版本相同；仅追加本任务图审查状态，不重复全量。规格与 18 均保持 claimed，全部工作树保留供双轴审查。
- 初审固定 `7e3a2a9`：Standards 发现 1 个 P2（未知事实被误锁存），Spec 无实质问题。唯一实施者的修复 `22b5653` 已快进合入，覆盖 15 个领域中的同根因判断，新增 152 项真实 Lua 故障回归。最终全量 **3715 passed in 125.17s**（`/tmp/civ6-ticket18-review-fix-full.log`），相关回归 1231 项通过；语法检查与 26 个 Markdown 文件／159 个本地链接及锚点检查通过。集成源代码与受测修复提交完全一致，无冲突或代码改动，复用全量结果；规格和 18 保持 claimed，双轴修复复核尚未完成。
- 首次复核固定 `ba9cdd8`：Standards 关闭原 P2，Spec 新发现 2 个 P2（外层无效身份读数、无效政策布局事实）。修复 `21ff615` 已快进合入，新增 85 项真实 Lua 回归；最终全量 **3800 passed in 125.59s**（`/tmp/civ6-ticket18-second-review-full.log`），针对回归 604 项、丢帧 adapter 调整后相关回归 367 项通过。集成无冲突、无代码改动，`src`／`tests` 与受测 `21ff615` 完全一致；复用全量证据，语法、diff、26 个 Markdown 文件／159 个链接及锚点检查通过。最新修复仍待双轴独立复核，规格与 18 继续保持 claimed，工作树保留。
