# 18: 全入口收口、回归与双轴审查

**What to build:** 使全量审计中受支持的业务操作都有明确所有者、证据和未知行为，并交付完整集成分支。

**Blocked by:** 02 存档取消与原生异常的未知保留, 03 购买单位和建筑的结果确认, 04 单位升级、晋升与删除的身份连续性, 05 总督任命、晋升与指派闭环, 06 科研、市政、政体与时代承诺确认, 07 政策付费解锁与换卡的部分完成, 08 自动探索、治疗、警戒与休眠确认, 09 建造、维修、移除与道路确认, 10 商路与商人转移确认, 11 伟人招募、赞助和拒绝确认, 12 伟人激活与传教结果范围, 13 占城决定绑定原待决城市, 14 间谍旅行与任务确认, 15 万神殿与创建宗教的阶段确认, 16 外交关系和宣战清理归属, 17 议会打开、投票与提交门禁

**Status:** claimed

- [x] 对照 82 工具／43 变更工具和隐藏分支清单，无遗漏直接提交业务路径；任意 Lua 边界单列。
- [x] 直接 GameState 与 MCP 门禁一致；查询、显式恢复和回合阻塞解除保留正确许可。
- [ ] 运行全量离线回归、语法与文档检查；完成 Standards／Spec 双轴审查并修复发现。
- [ ] 每票有提交与验证记录并标记 resolved；清理实施工作树，不 push、不替换真实对局。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

初始实施提交：`3fa68cd`，基于集成 `b219479`。本票保持 **claimed**：离线收口与初审发现修复已完成，Standards／Spec 修复差异复核、最终闭票与全部工作树清理由集成负责人执行。

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

未连接 FireTuner，未运行 scripts 实机测试，未启动、关闭、换档或修改真实对局，未 push。工作树保留供最终审查和合并，预存目录未触碰。

### 初审修复

基于冻结集成 `7e3a2a9`：Standards 初审 **1 个 P2、0 个独立 smell**，Spec 初审 **No material issues found（0）**。两轴原始结论及待复核状态分别保留于验收文档，未将实现者的测试通过计作独立复核通过。

Standards 的单一根因是未知身份／时代／政策布局被误当成有效变化永久锁存，初审涉及 13 个新增领域文件；修复排查另覆盖 envoys／contribution 两条同类旧路径，合计 15 个领域文件。共享片段只集中有效事实、未知和实际不匹配的判断；领域费用／效果／原生事件证据仍在本域。证据完整且读数恢复后可只读确认；真的身份、时代或布局变化继续锁存，缺少原生事件不能被恢复读数补成成功。

新增 152 项公开 GS/MCP＋真实 Lua 回归，原生 getter 故障由 adapter 注入。红灯分别为地块 3 例、其余守卫 36 例、时代／布局 4 例；恢复后原提交仍为 1 次，已付费政策只读确认不换卡，同意图续行只换卡一次且不再付款。相关回归 **1231 passed in 15.15s**；最终全量 **3715 passed in 125.17s**，日志 `/tmp/civ6-ticket18-review-fix-full.log`。`compileall -q src tests` 与 `git diff --check` 通过；26 个修改的 Markdown 文件／159 个本地链接及锚点，0 unresolved。
