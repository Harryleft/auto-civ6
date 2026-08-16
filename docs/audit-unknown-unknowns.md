# 信念引擎"未知的未知"审计与修复记录（2026-08）

> 审计基线：`8a9220a`；核验基线：`79a74a7`；修复提交：`ec3a62d`。
> 方法：读穿 belief_engine/derivation/graph 核心路径，用真实游戏日志
> （`beliefs_france_2126806272_tempered-emerald-spire-57.jsonl` 8.9MB、
> `beliefs_england_230461696_ivory-carmine-monument-88.jsonl` 3.8MB）量化验证。

## 一、已确认健全的"已知的未知"机制（审计时逐条核验）

| 机制 | 处理了什么未知 |
|---|---|
| coverage 四值语义（COMPLETE / CURRENTLY_VISIBLE / KNOWN_HISTORY / SUMMARY） | 未观察 ≠ 已删除；`graph/project.py` 中失去视野 → `observed=False` 而非删除 |
| 蛮族营地 5 回合未见才归档 + 迷雾往返 resurrect（`derivation.py:70-80`） | 防止"没看到"被当成"清除了"；`first_seen_turn` 保留 |
| `overdue_reason: metric_unavailable / no_evaluation_rule` | 知道"有些预测永远无法验证" |
| `OUTCOME_UNKNOWN` fail-closed + read-back 前置 | 动作结果未知时禁止重试 |
| ruleset 兼容显式报错 | 知道"此规则集下这些机制不存在" |
| 概率 vs 置信度分离 + "不声称统计校准"声明 | 知道自己的概率不可信 |

## 二、二次核验裁决（初判 → 核验结论）

| # | 初判 | 核验 | 证据 |
|---|---|---|---|
| 1 | known-gap 缺失（P0） | **确认，上调**：敌方城市既不在观测 facts、也不在图节点 | 真实日志 diplomacy facts keys 无 visible/unobserved；91 个 world_entity 中 `city` 仅 2 个（己方） |
| 2 | belief 无强制证据（P0） | **部分确认，方向修正**：机制漏洞确认，但真实数据揭示更尖锐的问题——信念层参与度归零（365 action vs 2 belief，FLEET 决策支持率 4.8%） | `belief_coverage.py` 基线：1036 决策仅 50 有 belief 支持 |
| 3 | stale knowledge 无告警（P1） | **确认**：review() 只查 prediction/expectation/plan；derivation 由观测触发，不查询=零信号 | 代码路径全链核验 |
| 4 | 归档=主动遗忘（P1） | **下调 P2**：resurrect 保护 + 歧义入 resolution 文本 + 真实日志零归档事件 | derivation.py:70-80；该局归档事件=0 |
| 5 | reliability 恒 1.0（P2） | **确认，范围扩大**：171/171 = 1.0，含 94 个快照/回合间接观测 | 真实日志分布 |
| — | 初判遗漏 | **Coverage 是四值不是三值**（SUMMARY 零消费方）；**旧投影 world_entity 的 coverage/observed 全部为 None**（覆盖语义只存在于影子图）；**55% 观测为纯 summary 零事实价值**（typed_snapshot 53 + end_turn 41 / 171） | `graph/model.py:24`；真实日志字段 |

## 三、修复内容（提交 `ec3a62d`）

1. **known-gap（P0）**：`normalize_tool_result` 的 get_diplomacy 分支（正则与
   信封双路径）产出 `visible_cities`/`unobserved_cities`（facts + metrics），
   "德国有 4 城只见 2 城"成为显式事实。敌方城市图占位节点留待图工程阶段四。
2. **belief 证据强制（P1）**：`_validate_entity` 拒绝无 `evidence_ids` 且无
   `unknown_basis=true` 的信念；`upsert_belief` 新增 `unknown_basis` 参数。
   旧日志 reduce 路径无校验——4530 条历史事件重放兼容验证通过。
3. **stale-knowledge（P1）**：`review()` 对超时未刷新的自动信念
   （camp ≥3 回合 / rival ≥5 回合，取归档阈值一半）输出 `knowledge_stale`，
   接入 turn_brief gate 与信念上下文/回合简报提示。**只提示不阻断**
   （default_route 不受影响）。
4. **reliability 分级（P2）**：信封=1.0 / 正则提取=0.9 / 纯摘要=0.8 /
   类型化快照=0.85，替换三处硬编码 1.0。
5. **归档 resolution_kind（P2）**：`ArchiveEntity` 增加 `kind`
   （unknown / resolved），5 个归档调用点全部标注；"cleared or lost to fog"
   类歧义不再被归档成"已解决"。

## 四、回归与验证

- 新增 `tests/test_audit_fixes.py` 15 项回归（known-gap 双路径、证据强制、
  stale 输出与不阻断、reliability 分级、resolution_kind）
- 更新受影响的 4 个测试夹具（belief payload 补 `unknown_basis: True`，
  rivals 精确断言补 known-gap 键）
- 全量离线 **632 passed**（含并发进程的 Lua golden 测试）
- 旧日志兼容：8.9MB 日志 4530 条事件 reduce 重放零异常

## 五、仍开放的问题（后续）

- 信念层参与度归零（FLEET 4.8%）：先观察 unknown_basis 强制后的使用变化，
  再决定引导使用或降级信念层。
- known-gap 的图侧占位节点（需要无坐标城市的身份方案）。
- SUMMARY coverage 已声明未落地（零消费方）。
- 观测信噪比：typed_snapshot/end_turn 观测占 55% 且为纯 summary，
  可考虑降低记录频率或合并。
