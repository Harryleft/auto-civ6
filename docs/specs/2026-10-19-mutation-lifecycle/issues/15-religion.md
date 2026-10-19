# 15: 万神殿与创建宗教的阶段确认

**What to build:** 宗教选择及创建宗教、添加两个信条保留原目标和每步进度。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

**Owner:** implement_01 — codex/mutation-15

- [x] 每个已提交阶段在未知或重入后不重复执行。
- [x] 原宗教与精确信条回读约束完成，部分完成明确可见。
- [x] 无合法资格或原生能力时不绕过规则；取消和丢回应保留状态。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。

## Answer

- `choose_pantheon` 与 `found_religion` 使用现有 ActionLifecycle／`_ActionEvidence`，绑定原世界、玩家、回合及原宗教／两个信条意图。已满足的精确目标返回 unchanged；其他既有宗教／不完整信条组合没有原请求记录时不擅自补写。
- 万神殿核对 `CanCreatePantheon`、精确 Pantheon belief class、原生占用／数量限制与实际 `GetPantheon`。采用原生 `VALUE_EXCLUSIVE`；取消、丢回应或原生进入后抛错不会再次消耗信仰。不假定固定费用或余额保持不变。
- 创建宗教使用一个小型领域 receipt，分别记录 FOUND_RELIGION、首个 ADD_BELIEF、第二个 ADD_BELIEF 的 entered／returned 和已确认进度。每阶段进入前记录；已进入但结果未知的阶段绝不重发。
- 原方法的同意图显式调用可继续已确认前序后的未进入阶段；`confirm_pending_action`、查询和其他动作门禁只读。每次继续都在原生层重新核对世界、原宗教／信条集合、剩余资格和已赚取信条数。
- 回读核对原 pantheon、`GetReligionTypeCreated`、`GetReligions` 的精确 Religion／Founder／Beliefs 集合。中途保留 `confirmed:n/3` 与每阶段 entered／returned；缺失／额外／错误信条、原生资格变化和世界失配保留未决。通知未清不会阻止已有精确信条事实确认。
- 原生依据是本机 `Base/Assets/UI/ReligionScreen.lua:115–137,305–333,445–468,596–598,644,773–779,899–920,1480–1524`。没有虚构 CanFoundReligion；保留票 12 的 Prophet 创教准备路径。

## Verification

- 实际 red→green：万神殿执行后丢回应原先丢失原意图；第一信条丢回应原先整块提交三个步骤；已满足的精确目标原先只报拒绝。测试运行公开 MCP／GameState、真实 Lua 5.1，仅模拟原生引擎与传输。
- 新增 **83** 个场景，覆盖正常三步、每阶段延迟／取消／丢回应／原生抛错、空 ACK、只读门禁不续写、不同意图、原生资格、宗教及信条集合、世界变化、继续前世界改变、读取失败、旧通知和 no-op。
- 合入集成基线 `56c9b09` 后，以下命令 **925 passed**：

```sh
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest -q tests/test_religion_plan_confirmation.py tests/test_unit_effect_confirmation.py tests/test_great_person_pool_confirmation.py tests/test_policy_lua_execution.py tests/test_game_state.py tests/test_server.py tests/test_tile_purchase_confirmation.py tests/test_action_confirmation.py tests/test_city_decisions.py
```

- 6 个改动 Python 文件语法、`git diff --check` 和规格相对链接检查通过；未实机，未 push。
- 实现提交：`82d7ee2`；集成合并：`193254a`。无公共生命周期接口扩展。
