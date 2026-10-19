# 状态变更实施任务图

**Status:** claimed

[规格](spec.md)；[实施调研](research.md)；本地 Markdown tracker；基线 `d3450a3`。

| 票 | 交付 | Blocked by | 状态 |
| --- | --- | --- | --- |
| 01 | [地块购买与可复用的未决动作契约](issues/01-purchase-tile.md) | 无 | resolved |
| 02 | [存档取消与原生异常的未知保留](issues/02-save-cancellation.md) | 无 | resolved |
| 03 | [购买单位和建筑的结果确认](issues/03-purchase-item.md) | 01 | ready-for-agent |
| 04 | [单位升级、晋升与删除的身份连续性](issues/04-unit-transforms.md) | 01 | ready-for-agent |
| 05 | [总督任命、晋升与指派闭环](issues/05-governors.md) | 01 | ready-for-agent |
| 06 | [科研、市政、政体与时代承诺确认](issues/06-choices.md) | 01 | ready-for-agent |
| 07 | [政策付费解锁与换卡的部分完成](issues/07-policies.md) | 01 | ready-for-agent |
| 08 | [自动探索、治疗、警戒与休眠确认](issues/08-readiness.md) | 01 | ready-for-agent |
| 09 | [建造、维修、移除与道路确认](issues/09-tile-actions.md) | 01 | ready-for-agent |
| 10 | [商路与商人转移确认](issues/10-trade-routes.md) | 01 | ready-for-agent |
| 11 | [伟人招募、赞助和拒绝确认](issues/11-great-people-pool.md) | 01 | ready-for-agent |
| 12 | [伟人激活与传教结果范围](issues/12-unit-effects.md) | 01 | ready-for-agent |
| 13 | [占城决定绑定原待决城市](issues/13-city-decisions.md) | 01 | ready-for-agent |
| 14 | [间谍旅行与任务确认](issues/14-spy-assignments.md) | 01 | ready-for-agent |
| 15 | [万神殿与创建宗教的阶段确认](issues/15-religion.md) | 01 | ready-for-agent |
| 16 | [外交关系和宣战清理归属](issues/16-relationships.md) | 01 | ready-for-agent |
| 17 | [议会打开、投票与提交门禁](issues/17-congress.md) | 01 | ready-for-agent |
| 18 | [全入口收口、回归与双轴审查](issues/18-integrate-verify.md) | 02, 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13, 14, 15, 16, 17 | ready-for-agent |

01 与 02 已合入；03–17 可在各自领域独立推进。相同模块的工作可由同一实施者顺序领取，代码冲突由 merger 处理，不把资源调度伪装成业务阻塞。18 等全部领域工作完成后收口。

## Decisions-so-far

- 单一 MCP 控制游戏；不设计人工并发防御。
- 复用现有公开 MCP／GameState → 真实生成 Lua → 原生 adapter 的验证 seam。
- 每票独立工作树和分支，汇入一个集成分支；不 push。

- 用户已确认 18 票颗粒度、依赖关系和测试 seam；集成分支 `codex/mutation-lifecycle`，审查基线 `d3450a3`。
- 02 已由 `codex/mutation-02` 快进合入（实现 `c4e68c4`，闭票 `31fa2e0`）；集成后保存、原生终局边界、回合与 GameState 四个相关测试文件共 421 项通过。详见本票 Answer。
- 01 已由 `codex/mutation-01` 合入 `15d999d`（实现 `ab45699`，闭票 `fe35624`）；地块购买、使者、保存、原生终局边界、回合与 GameState 六个测试文件联合验证共 589 项通过。后续票复用 `_ActionEvidence`，接入说明位于 `/tmp/civ6-mutation-action-interface.md`。
