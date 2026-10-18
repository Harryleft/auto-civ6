# 间谍撤离身份与确认验收

实现 `3ab5a3b`、补充修复 `3627d48`，最终集成 `4c6876a`；分支 `codex/native-pillage`，未 push。[规格](../specs/2026-10-19-spy-escape-identity.md)与[工作项](../specs/2026-10-19-spy-escape-identity-tickets.md)。

T148 原生待撤离 ID 为 2949124，旧 MCP 截为索引 4，导致合法撤离被拒。修复完整身份后，首次实机选择使间谍立即阵亡，旧确认又因单位不再存在而永久 UNKNOWN。最终确认要求本次返回回执、原世界与完整实体，以及真实门禁解除；单位消失时，还必须有提交前安装的精确 `UnitRemovedFromMap` 见证。离图不等于成功逃生，缺失无证据仍保留未决。

| 验证 | 结果 |
| --- | --- |
| 官方原始接口 | EspionageEscape 直接将 GetNextEscapingSpyID 返回值交给 FindID；UnitFlagManager／UnitPanel 定义 UnitRemovedFromMap(playerID, unitID)。来源与指纹另存 |
| 查询安全 | 仅有效、未撤销的撤离通知允许调用危险 getter；get_spies 展示真实 escaping 及原生合法路线，不在普通任务状态猜测撤离 |
| 最终相关离线 | 12 个模块 696 项通过，独立集成 13.52 秒；编译、差分、源码指纹通过。覆盖错误 owner／代 ID／世界、过早事件、未返回请求、迟到、取消及缺失监听器零提交 |
| 双轴审查 | 两轮 Standards／Spec 均无遗留问题 |
| 首次实机 | 629 识别完整 ID，仅有市中心路线；630 请求后 UNKNOWN；631–633 单位消失、原生通知阵亡且撤离待办解除；634 只确认未重发 |
| 显式故障恢复 | 636 使用已确认的本轮 T148 检查点，63.922 秒；未决动作随受保护替换退役后才正常关闭旧客户端 |
| 同路线复验 | 642 再核对唯一合法路线；643 同样选择市中心，0.133 秒返回 ESCAPE_ROUTE_CONFIRMED／entity:removed_event；644 原生通知同样阵亡，未以回档改选路线 |
| 保存 | 646：benchmark_terminal_20261019_t148_escape_verified，1,095,454 字节；原生完成、文件及身份回合确认 |
| 菜单恢复及续行 | 660 菜单恢复撤离后新档 30.076 秒；664 无外交会话；671 确认 T149，686 确认 T150，704 确认 T151 |

撤离后还出现瑞典引擎会话开放、界面对话和报价均不可见的状态。关闭间谍结果提示、打开及退出外交 Overview 都未解除该会话；官方 UI 的 Close／UninitializeView 与 CloseSession 分属不同路径，原始诱因尚未确定。菜单恢复撤离后检查点成功清理，未新增默认回应或强制关闭工具。

证据根目录：`~/.civ6-mcp/acceptance/20261018-turn-terminal121/`。实现及验证见 `spy-escape-impl/`、`spy-escape-fix/`、`spy-escape-merger/`；首次现场及恢复见 `spy-escape-live-first-attempt.md`、`spy-escape-post-fix.md`、`live/618–704`；隐藏会话原始 API 研究见 `hidden-session-research/`。专用工作树已清理，分支与提交保留。旧存档、旧验收及新结果另行归档；整轮终局验证继续。
