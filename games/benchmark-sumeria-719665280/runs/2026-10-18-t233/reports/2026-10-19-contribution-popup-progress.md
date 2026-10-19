# 贡献未决与信息弹窗验收

[规格](../changes/2026-10-19-contribution-popup-progress.md)与[工作项](../changes/2026-10-19-contribution-popup-progress-tickets.md)。实施6cb488b，独立合入55f692b，codex/native-pillage；未合main、未push。

T231 ProjectBuiltPopup引用当前引擎事件，关闭时才释放。贡献2179只提交一次，已有返回回执但没有命令／劳动力事件；2183显式dismiss_popup又被贡献的全局依赖门禁拦截。原版PopupManager的ReferenceCurrentEvent／ReleaseEventID及现场证据说明事件处理暂停。没有证据支持旧弹窗清空新生产队列。

修复只让显式dismiss_popup到达已有GameState／原生关闭路径；贡献的所有权、身份、令牌、单次提交和后续确认均不变。关闭本身不释放门禁，不自动重试、不自动关闭、不强制推进。

| 验证 | 结果 |
| --- | --- |
| 离线红绿 | 旧调度关闭调用次数为0，回归变红；修复后真实内存MCP允许显式关闭。新7项覆盖延迟事件、关闭后仍未知、失败／无弹窗、外交保护、其他写入及原请求单次确认 |
| 相关模块 | contribution_popup_progress、royal_contribution、diplomacy_cleanup，157项通过；独立合入2.83秒，编译、差分及受保护dirty文件检查通过 |
| 独立审查 | Standards／Spec各0材料性问题 |
| T231原请求 | 旧版本下标准UI Escape关闭已核实的项目弹窗；2187information:none，2188原贡献0.029秒确认6次劳动力、生产0→110；无重发、无重载 |
| 新版原生关闭 | 原请求已确认后才更新MCP；2209仍同世界T232，2210显式dismiss_popup关闭ProjectBuiltPopup与摄影机，2212读到下一市政提示、2214再次明确关闭，2215科研选择回读正常 |

新版“贡献仍未决时关闭”的完整调度链由内存MCP回归验证；实机未人为制造第二次未知。T232后继续同轮终局，结果见[整轮验收](2026-10-19-turn-terminal.md)。

证据：本轮验收根目录下project-popup-diagnosis.md、contribution-popup-impl／merge／review及live/2176–2215。原生游戏进程未因本修复重启，专用实施worktree已清理。
