# 12: 伟人激活与传教结果范围

**What to build:** 激活伟人或传教只报告实际能核对的次数、单位与目标效果。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

**Owner:** implement_01 — codex/mutation-12

- [x] 覆盖现有 RequestOperation 和 RequestCommand 激活分支。
- [x] 剩余次数、实体移除及精确目标证据足以确认已声明的完成范围。
- [x] 缺少无法核实的奖励证据不虚报奖励，重入不再次消耗。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。

## Answer

- `activate_great_person` 与 `spread_religion` 通过现有 ActionLifecycle／`_ActionEvidence` 保存原 full unit ID、世界／回合／玩家及原生次数和目标；MCP 分派保留完整 ID。同意图重入只确认，其他动作不能覆盖原未决请求。
- 普通伟人使用原生 RequestCommand，核对 `UnitGreatPersonActivated(owner, fullID, class, individual)` 与实际减少一次的 action charges。最后一次激活要求精确移除事件与原单位不存在；只保存标量身份，原生销毁后不再访问旧 userdata。输出只报告激活和次数，不声明未测量奖励。
- 大预言家使用 FOUND_RELIGION RequestOperation，独立核对精确操作启动、原 individual／实体状态、`GetNumBeliefsEarned` 增长、`HasReligiousFoundingUnit` 变为 false、仍未创建宗教。返回 `RELIGION_FOUNDING_READY`，创建宗教／选择信条属于独立票 15。
- 传教绑定原地城市或原生 `GetOperationTargets` 的唯一城市目标，以精确坐标提交；没有可靠／唯一目标时零提交拒绝。确认同一宗教、同一 owner／city ID 的当前 `GetTotalPressureOnCity` 增长，以及一次 spread charge 消耗／最后一次移除。多数宗教不变也可确认压力增长；只有多数改变而没有压力证据不能确认。
- 原生观察器在提交前安装；安装失败零提交并清理，未知结果保留，确认后清理。取消、延迟、丢回应、原生进入后抛错、读回错误、原世界变化均不导致重发。

## Native evidence

- 本机 `Base/Assets/UI/Popups/GreatPeoplePopup.lua:962`：伟人激活事件完整身份参数。
- `Base/Assets/Gameplay/Data/GreatPeople_Prophets.xml:23–38`：所有预言家 `ActionCharges="0"`，不能套普通伟人 charges>0 规则。
- `Base/Assets/UI/ReligionScreen.lua:115–137,305–333`：可选信仰数、创教单位状态、已创建宗教与下一步选择界面。
- `Base/Assets/UI/WorldInput.lua:975–984`：原生目标 PLOTS／MODIFIERS／MODIFIER_IS_TARGET 解释；`Panels/UnitPanel.lua:2493–2535`：普通命令和操作提交入口。
- `Base/Assets/UI/WorldView/CityBannerManager.lua:1230–1246`：当前宗教压力使用 `GetTotalPressureOnCity`，区别于 lifetime pressure。

## Verification

- TDD 实际 red→green：丢回应激活原先只报 Error；预言家零次数需要独立确认；延迟传教原先在次数尚未减少时直接宣称成功。
- 新增 **98** 个公开 MCP／GameState＋真实 Lua 5.1／原生 adapter 场景，覆盖三条正常路径、延迟／取消／丢回应、错误 full ID／individual／事件、次数、最后一次销毁 userdata、传教压力与城市身份、预言家准备状态、观察器失败／回收、世界变化与共享门禁。
- 合入最新集成基线 `f60a7ec` 后，以下命令 **880 passed**：

```sh
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest -q tests/test_unit_effect_confirmation.py tests/test_game_state.py tests/test_server.py tests/test_action_confirmation.py tests/test_tile_purchase_confirmation.py tests/test_envoy_confirmation.py tests/test_great_person_pool_confirmation.py tests/test_unit_tasks.py tests/test_policy_lua_execution.py
```

- 7 个改动 Python 文件语法、`git diff --check`、规格相对链接检查通过。未实机、未 push。
- 实现提交：`5b374fb`；最新集成合并：`2d83e79`。未改变公共生命周期接口。
