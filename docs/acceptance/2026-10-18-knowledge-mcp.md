# 世界知识 MCP 验收

两个只读工具已接入现有 MCP 服务；Agent 在规则、前置、解锁或机制不明确时查询，并用游戏工具核对本局候选、费用与进度。调用契约集中于[知识入口](../../civ6_knowledge/README.md#mcp-查询)，AGENTS 和策略文档只保留触发条件与链接。

## 实施与离线验证

| 范围 | 验证结果 |
| --- | --- |
| 搜索 | 中英名称／ID 包含匹配、九主题筛选、1～20 条限制、截断与零命中；不宣称语义搜索 |
| 分层读取 | summary 身份概览、rule 前置／解锁／条件／关联、evidence 来源；附 profile、snapshot 与静态适用边界 |
| MCP 协议 | 内存 ClientSession 发现 80 个工具；正式知识库完成搜索→三层读取；只读、幂等提示及输入 schema 正确 |
| 调度隔离 | 无游戏对象访问；action／save／load／end_turn 未决期间查询成功，未决不变，原动作门禁有效 |
| 失败边界 | 无资料、错误目录、不同 cwd、损坏来源／指纹／JSON、版本资料不一致、非法参数、坏返回和超时明确失败；超时进程回收 |
| 离线门槛 | 全量 1,472 passed（34.67 秒）；相关 197 passed；compileall、diff、九主题 validate 通过；依赖未变 |
| 文档 | 15 份文件、127 个链接／锚点、九主题与工具名核对；知识 README 保留独立目录能力和 CLI 备用入口 |
| 成果保护 | 979 个旧验收文件、54 个原生存档及其备份、10 个知识规则／来源文件 SHA-256 一致；manifest 仅同步 README 指纹 |

独立 Standards／Spec 审查均为 0 项实质问题；复核另验证九主题的 36 次 MCP 搜索与分层读取，以及取消查询后的子进程回收。实现合并 tip `5b7bfb4`；测试源码、测试与依赖指纹和集成树一致。此阶段在 `codex/architecture-deepening` 完成，后续合并见下节。

非源码安装须通过 `CIV6_KNOWLEDGE_DIR` 指定完整独立知识目录；工具不会下载、采集或重建资料。

规格与工作项：[规格](../specs/2026-10-18-knowledge-mcp.md)、[工作项](../specs/2026-10-18-knowledge-mcp-tickets.md)。离线原始证据：`~/.civ6-mcp/acceptance/20261018-knowledge-mcp/`，包括 `adapter/checks.json`、`adapter/tested-tree.json`、`adapter/public-mcp-validation.json`、`docs/documentation-validation.json`、`merger-k1.json`、`review-standards.md`、`review-spec.md` 与 `protection-final.json`。

## main 合并与当前会话验证

2026-10-18，经用户确认将已验证集成分支整体合并到 `main`，提交 `5ab7725`；合并文件树与此前 1,472 项离线测试通过的版本一致。客户端重新加载后，本聊天直接发现并调用知识工具，共 4 次成功。

| 当前会话验证 | 结果 |
| --- | --- |
| 工具发现 | 可见 80 个 Civ6 工具，包含 `search_knowledge` 和 `get_knowledge` |
| 搜索 | `query="写作"`、`topic="technologies"`、`limit=5`，返回 `found`、1 条结果 `TECH_WRITING` |
| `summary` | 返回 ID、中文／英文名称和主题，未展开规则或证据 |
| `rule` | 前置 `TECH_POTTERY`，解锁学院 `DISTRICT_CAMPUS` 和图书馆 `BUILDING_LIBRARY`，附 18 条来源引用 |
| `evidence` | 返回 18 条来源引用、18 行证据和 74 条来源文件记录 |

四次回复均指向同一 `1.0.12.54 (951533)` 版本快照，明确标记 `static_snapshot`、`current_session_verified=false`；未验证本局操作资格。独立 stdio 客户端此前也完成两次启动和重新连接，各发现 80 个工具、完成 4 次知识调用；上述表格记录的是本聊天的直接调用。

会话验证前后，54 个原生存档、1 个项目基准、1,081 个既有验收文件和 36 个知识文件指纹一致。此前工具未发现的复核与独立 stdio 结果全部保留。本次 MCP 实施与客户端验证均未连接真实游戏、FireTuner 或加载存档，未推进回合、未 push。

本轮原始证据：`~/.civ6-mcp/acceptance/20261018-knowledge-mcp-current-session/`。`direct-client-summary.json` 保存工具发现与四次调用校验，`raw-actions-*.jsonl` 保存原始回复，`result.json` 记录成果保护结果。前序证据分别保存在 `20261018-main-knowledge-mcp-merge/`、`20261018-knowledge-mcp-client/` 和 `20261018-knowledge-mcp-client-recheck/`，均位于同一验收根目录。
