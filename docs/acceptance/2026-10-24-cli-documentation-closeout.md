# CLI 命令手册与知识收尾

2026-10-24 将现有命令统一到 [CLI 命令手册](../cli.md)，并在 [AGENTS.md](../../AGENTS.md#调用入口)明确：常用诊断、知识、测试、日志和常规游戏操作统一走 CLI；连续操作保留同一个 debug／MCP 上下文，优先命名命令，未封装的公开工具经同一会话 tools／call 调用。43 个领域命令不再拆成阶段表和高频补丁表。

## 现役事实矩阵

| 事实面 | 状态 | 依据与处置 |
| --- | --- | --- |
| 代码 | changed-and-verified | 手册逐项对照实际解析器的 43 个命令、工具映射和变更标记；唯一代码改动是修正 verify 组将实机路径称为 offline 的旧 help 标签，未改变执行逻辑。 |
| 本机运行态 | verified-current | 实际 CLI doctor、verify knowledge 通过；debug --offline 验证 57 项帮助、两项知识工具及预演，游戏连接始终 false。此前实机验收仍按各报告的受测版本保留，本次没有操作真实游戏。 |
| 文档 | changed-and-verified | 独立命令手册为唯一命令目录；README、文档索引、安装、游戏流程、恢复、特殊操作、知识和既有验证技能均路由到该入口。底层 MCP 工具名继续作为协议说明。 |
| 规则 | changed-and-verified | 已读取实际全局／项目规则；CLAUDE.md 和 GEMINI.md 均仍软链到 AGENTS.md，无独立分叉；没有项目 override。 |
| 记忆 | out-of-scope | 当前是项目文档与规则收尾；Codex 生成记忆按 generated-read-only 处理，未写全局或生成记忆。 |
| 工作区 | pending | 仅 main 和主工作树，没有本次 worktree 或分支待整合；原有未跟踪对局资料及清理候选均保留，详见下表。 |

本次是本地知识收尾，不是发布任务；远端、PR、CI 和发布／部署均 out-of-scope，未 push。没有将历史实机通过升级成本次全功能验收。热座专用控制器、Single 存档回退范围和未实测写入路径仍按命令手册的限制执行。

## 已验证

- 按 neat-freak 完整路径机械盘点；起始清单共 1,034 个 Markdown 文件（含原样上游技能、独立嵌套仓库和本地运行依赖），按当前任务展开受影响入口，没有审改其他项目或上游 vendored 技能。
- 命令手册覆盖 43／43 个领域命令，MCP 映射及只读／变更标记与解析器一致；实际输出 57 项分层帮助，CLI 未连接游戏。公共参数、顶层帮助和会话控制也已核对。
- doctor 与 verify knowledge 返回 PASS。知识 README 修改后，从既有 sources/snapshot.json 在隔离目录构建，只发现 support_files.README.md 的已知指纹变化；快照、全部规则文件及其他支持文件均未变，清单按构建产物同步。
- 受影响文档的本地链接、锚点、规则软链、技能元数据及差异空白检查通过。仅 help 文案和文档／知识说明变更，未重复运行游戏动作或全量测试。
- 详细本地证据在 `.scratch/neat-cli-validation/`，原始前后盘点在 `.scratch/neat-cli-inventory-before.txt`、`.scratch/neat-cli-inventory-after.txt`；原始本机路径不提交。

## 保留现场与清理预览

| 对象 | 处置 | 原因 |
| --- | --- | --- |
| `.scratch/civ6-wheel/`、`.scratch/civ6-debug-wheel/`、`.scratch/civ6-cli-contract-wheel/`、`.scratch/civ6-domains-wheel/`、`.scratch/cli-frequency-wheel/` | pending：5 个 wheel 构建目录为删除候选，合计约 1.9 MiB；尚未删除 | 均为可从对应 Git 版本和锁文件重建的产物，待用户复核后确认是否清场。 |
| `games/2026-10-24-dual-ai-four-leaf/README.md`、`runs/20261024-083313/` | pending：保留原有未跟踪内容 | 属于任务开始前已有对局记录，不纳入本次提交，不自动提交或删除。后者位置相对于同一对局目录。 |
| `.local/hotseat-tuner/` | out-of-scope：保留 | 约 23G 的实验游戏副本，当前游戏进程使用它；不是缓存，没有停服、替换或删除。 |
| 对局存档／evidence、`.scratch/` 中其他验证证据及脚本、`.venv`、vendored pstack、独立嵌套仓库 | 保留 | 仍是运行、复核或其他任务材料，不能由目录名推断可删除；不在本轮清理候选内。 |

复核现场仍保留，等待用户确认后清场。neat-freak 的明确要求是「只有用户看完汇报后明确确认可以清场，才执行删除并补充汇报清场结果」；本次仅完成盘点和删除预览，没有执行删除。
