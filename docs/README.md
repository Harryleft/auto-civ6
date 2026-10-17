# 文档

- [安装与使用](../README.md)：本机 MCP 启动和客户端配置。
- [当前架构](architecture.md)：核心职责、执行与恢复边界。
- [动作确认与恢复时序修复规格](specs/2026-10-17-action-confirmation.md)：20 回合审查问题、实施约束和验收条件。
- [修复与 20 净回合复验](acceptance/2026-10-17-confirmation.md)：575 项离线测试、完整重新推进、最终保存恢复及证据。
- [第 30～50 回合调度修复规格](specs/2026-10-17-scheduling-repair.md)：响应归属、纯查询、真实完成、区域落位、移动与战斗估算；[工作项](specs/2026-10-17-scheduling-repair-tickets.md)。
- [第 21～30 回合调度问题](acceptance/2026-10-17-turn-30-scheduling.md)：当时的 3 次未知及恢复、完成事件与响应归属问题证据。
- [调度修复与第 50 回合验收](acceptance/2026-10-17-scheduling-repair-turn-50.md)：630 项离线测试、20 净推进、最终保存恢复及新增问题。
- [灾害弹窗、恢复读取与分数差异验收](acceptance/2026-10-17-reliability-repair.md)：两处灾害回放、分数原因、保存恢复及菜单换档分析。
- [菜单换档规格](specs/2026-10-17-menu-save-reload.md)与[耗时验收](acceptance/2026-10-17-menu-save-reload.md)：735项离线测试、进程保留、两组配对耗时、状态清理及新存档恢复。
- [模型操作规则](../AGENTS.md)：基于真实状态操作游戏。
- [存档格式](save-file-format.md)：`scripts/parse_save.py` 的逆向研究依据。
- [macOS 启动器设置](research/bypassing_aspyr_launcher.md)：Steam 启动配置参考。
- [随附存档与历史日志](../fixtures/README.md)：离线数据位置。
- [历史归档](archive/README.md)：旧架构、评测研究及游戏报告，不代表当前实现。
