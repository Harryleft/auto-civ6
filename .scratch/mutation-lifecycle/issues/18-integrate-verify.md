# 18: 全入口收口、回归与双轴审查

**What to build:** 使全量审计中受支持的业务操作都有明确所有者、证据和未知行为，并交付完整集成分支。

**Blocked by:** 02 存档取消与原生异常的未知保留, 03 购买单位和建筑的结果确认, 04 单位升级、晋升与删除的身份连续性, 05 总督任命、晋升与指派闭环, 06 科研、市政、政体与时代承诺确认, 07 政策付费解锁与换卡的部分完成, 08 自动探索、治疗、警戒与休眠确认, 09 建造、维修、移除与道路确认, 10 商路与商人转移确认, 11 伟人招募、赞助和拒绝确认, 12 伟人激活与传教结果范围, 13 占城决定绑定原待决城市, 14 间谍旅行与任务确认, 15 万神殿与创建宗教的阶段确认, 16 外交关系和宣战清理归属, 17 议会打开、投票与提交门禁

**Status:** resolved

- [x] 对照 82 工具／43 变更工具和隐藏分支清单，无遗漏直接提交业务路径；任意 Lua 边界单列。
- [x] 直接 GameState 与 MCP 门禁一致；查询、显式恢复和回合阻塞解除保留正确许可。
- [x] 运行全量离线回归、语法与文档检查；完成 Standards／Spec 双轴审查并修复发现。
- [x] 每票有提交与验证记录并标记 resolved；清理实施工作树，不 push、不替换真实对局。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

初始实施提交 `3fa68cd`，最终受测代码提交 `31ae3f5`；冻结集成 `c64201e` 的 Standards／Spec 独立复核均为 **No material issues found**。最终全量 **3856 passed in 127.55s**，18 个实施工作树已安全清理，本票及规格已 resolved。以下保留各轮实施与审查历史；claimed／待复核均为当时状态。

- 已核对源码注册、公开 MCP 目录和完整覆盖表：**82 工具／43 变更／39 纯查询，遗漏 0**。补齐三项纯查询的 readOnlyHint；[完整覆盖与离线验收](../../../docs/acceptance/2026-10-19-mutation-lifecycle.md)逐项列出组合分支、未注册 GS 入口、原生 UI callbacks、任意 Lua 与显式生命周期边界，原审计保留历史基线。
- 直接 GS 的 save/load/turn/diplomacy 门禁补齐；普通 kill/launch/任意 Lua/弹窗关闭不能绕过未知保存或替换。原动作与迁移动作同样先由原 owner 匹配意图；MCP 旧 skip/fortify 等重入不会先确认旧请求后再进入新操作。OPEN_BORDERS 别名继续交原交易 owner 匹配，错目标拒绝，其他关系动作仍受门禁。
- MCP move/attack/fortify/skip/found_city 传递完整 ID；单位、城市查找及移动／攻击／生产回读核对原身份。兼容直接低 index 移动时仍从提交前原生记录核对实例，替代单位到达目的地不能确认旧请求。
- 所有 scoped ActionLifecycle 请求在观察到世界／回合／玩家失配后锁存失效，恢复原世界不能追认。保留议会原生阶段所有者，仅 end_turn 可依据最新可信 REGISTERED 放行；spy_escape 没有证据需要新增 pending-turn 例外，活动通知与危险 getter guard 保持。
- 保存未知继续由原 save_pending 与显式检查点恢复处理，没有发明自动补记、通用保存事务或跨重启意图持久化。

验证：新增 **63 项**公开 GS/MCP＋真实 Lua 回归；仅引擎、连接、临时保存与进程 adapter 模拟，推进组合门禁复用既有 GameStateConn 原生协议 adapter。完整身份／scope 首轮 11 例失败，跨入口门禁及旧重入 21 例失败；低 index 替代实例、OPEN_BORDERS 别名和查询目录另有先失败后修复的证据。最终全量命令：

```sh
PYTHONPATH=src ~/Documents/ChatGPT/civ6/civ6-mcp/.venv/bin/python -B -m pytest tests/ -q
```

初审冻结前结果 **3563 passed in 124.08s**；日志 `/tmp/civ6-ticket18-full-reviewed.log`。此前 249／497／340 项针对回归通过；首轮全量唯一失败是旧 server 测试仍要求截断 composite ID，已按新完整身份契约更新。`compileall -q src tests`、`git diff d3450a3 HEAD --check` 通过；提交后 Markdown 检查覆盖 26 文件／156 本地链接及锚点，0 unresolved。

未连接 FireTuner，未运行 scripts 实机测试，未启动、关闭、换档或修改真实对局，未 push。当时工作树保留供最终审查和合并，现已完成清理；预存目录未触碰。

### 初审修复

基于冻结集成 `7e3a2a9`：Standards 初审 **1 个 P2、0 个独立 smell**，Spec 初审 **No material issues found（0）**。两轴原始结论及待复核状态分别保留于验收文档，未将实现者的测试通过计作独立复核通过。

Standards 的单一根因是未知身份／时代／政策布局被误当成有效变化永久锁存，初审涉及 13 个新增领域文件；修复排查另覆盖 envoys／contribution 两条同类旧路径，合计 15 个领域文件。共享片段只集中有效事实、未知和实际不匹配的判断；领域费用／效果／原生事件证据仍在本域。证据完整且读数恢复后可只读确认；真的身份、时代或布局变化继续锁存，缺少原生事件不能被恢复读数补成成功。

新增 152 项公开 GS/MCP＋真实 Lua 回归，原生 getter 故障由 adapter 注入。红灯分别为地块 3 例、其余守卫 36 例、时代／布局 4 例；恢复后原提交仍为 1 次，已付费政策只读确认不换卡，同意图续行只换卡一次且不再付款。相关回归 **1231 passed in 15.15s**；最终全量 **3715 passed in 125.17s**，日志 `/tmp/civ6-ticket18-review-fix-full.log`。`compileall -q src tests` 与 `git diff --check` 通过；26 个修改的 Markdown 文件／159 个本地链接及锚点，0 unresolved。

### 首次复核后的修复

首轮修复提交 `22b5653` 合入冻结集成 `ba9cdd8` 后，Standards 关闭原 P2，0 个剩余问题；Spec 独立复核又确认 2 个 P2：外层身份首次读取的无效事实被锁存，以及负槽数／不存在槽位类型被当作有效布局变化。Spec 初审 0 与首次复核 2 个 P2 分别保留，未改写为同一轮结论。

修复统一 prepare_scope／确认／普通身份刷新的原生事实解析；序列化前检查数值类型，外层和 Lua 玩家范围均为 0–63，保留负 seed。政策槽数须非负，槽位类型／政体／时代须存在于原生目录；政体 -1 仍是有效“无政体”哨兵，确实变化仍锁存。新故障恢复保留原领域结果证据与单次提交规则。

本轮新增 85 项公开 GS/MCP＋真实 Lua 回归（范围恢复文件累计 237 项），红灯为外层首读 2 例、身份刷新／类型 4 例、政策／时代值域 6 例、玩家64准备／确认 2 例。针对回归 **604 passed in 13.13s**。当时修复差异待两轴独立复核，18 保持 claimed。

全量首轮 3799 通过／1 失败，原因是旧 Royal Society adapter 使用已变化的 Lua 源码前缀注入丢帧，故没有实际截断回应。现改为识别返回的 ACTION_IDENTITY 帧，保留原断言；相关 367 项通过。

本轮最终全量 **3800 passed in 125.59s**，日志 `/tmp/civ6-ticket18-second-review-full.log`。`compileall -q src tests`、`git diff --check` 通过；26 个修改的 Markdown 文件／159 个本地链接及锚点，0 unresolved。未 push、未操作实机；工作树交回集成负责人复核与清理。

### 第二次复核后的修复

第二轮修复 `21ff615` 合入冻结集成 `2643c1d` 后，Spec 独立关闭前轮 2 个 P2，新增 0；Standards 原 P2 保持关闭，本轮新增 1 个 P2：外层身份探针要求四字段同时有效，一个未知字段会掩盖另一个有效的世界／回合／玩家变化。两轴历史分别保留，当时 Standards 修复待独立复核，18 保持 claimed。

公共探针逐字段隔离 getter 异常并验证原生类型，保留每个有效读数；确认按原绑定玩家读取文明，任何有效失配先锁存，部分未知仍不能确认成功。准备提交继续要求完整有效身份；普通身份刷新复用同一探针，完整 GAME_SCOPE 交当前动作比较，GAMESEED 与二元身份返回接口保留；世界身份不完整时不更新缓存。玩家 0–63、signed seed、政体 -1 与原生目录校验语义保持。

新增 **56 项**公开 GameState／MCP＋真实 Lua 回归，采用没有内层身份守卫的时代承诺入口：四字段混合失配 24 项、单字段未知后恢复 20 项、普通身份刷新 12 项。红灯分别为 seed nil／throw 掩盖回合变化 2 例、普通身份刷新文明／seed 组合 6 例、玩家未知掩盖原玩家文明变化 2 例；收尾核对再补普通身份刷新玩家／回合组合 6 例红灯。有效变化后恢复原值仍保持未知；只有未知读数的请求可在恢复后只读确认，提交数始终为 1。

针对回归 **574 passed in 13.48s**，日志 `/tmp/civ6-review-third-targeted-final.log`；最终全量 **3856 passed in 127.55s**，日志 `/tmp/civ6-ticket18-third-review-final.log`。`compileall -q src tests`、`git diff d3450a3 HEAD --check` 与工作区差异检查通过；当时 26 个修改的 Markdown 文件／159 个本地链接及锚点，0 unresolved。未 push、未操作实机；工作树当时保留供最终合入、独立双轴复核与清理。

### 最终闭票

最终审查冻结 `c64201e`，修复差异 `2643c1d...c64201e`。Standards 本轮关闭最后一个 P2，遗留 0、新增 0，累计发现的两个 P2 均已关闭；独立 8 组 ChoiceConn 故障验证中，读数恢复后正确确认或继续失效，原生提交均为 1 次。Spec 前轮两个 P2 已关闭，本轮新增 0，独立 56 项公开入口真实 Lua 测试通过（1.26s）。两轴均报告 No material issues found，完整结论分别保留在验收文档。

18 个实施工作树已不使用 force 安全移除，分支保留；`git worktree list` 仅主检出，清理日志 `/tmp/civ6-mutation-worktree-cleanup.json`。最终 `src`／`tests` 与受测 `31ae3f5` 的 Git tree 完全相同，本次仅文档闭票，不重复全量。全部 18 票与规格 resolved，无剩余验收项；原 `draft_40be61cf_folder/` 保留。
