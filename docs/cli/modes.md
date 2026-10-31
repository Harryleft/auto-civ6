# 开发与热座模式

[返回 CLI 索引](../cli.md)

普通会话不在 `tools` 中公开 `run_lua`，通过 `call` 或 `developer lua` 也会在发送前拒绝。用户明确授权诊断后，可在无未决状态下正常关闭原会话，再启动 `civ6 debug --developer`；此时 `developer lua "print(...)" --context gamecore --dry-run` 预演任意 Lua，正式执行仍受 Runtime 门禁保护。开发模式不自动清理未知请求，也不允许从另一个进程接管它。

既有双人热座实验可使用 `civ6 debug --hotseat-seat 0 --hotseat-run DIR`，其中 DIR 是已有、已核验补丁清单的授权轮次。该模式启动原专用控制器，`hotseat begin` 确认本席位 PlayerChange，`hotseat end` 确认原生交接，`hotseat actions 'JSON_ARRAY'` 执行最多 20 个已明确的独立动作。每条仍支持 `--dry-run`；普通 `turn end`、Single 存档操作及任意 Lua 在该控制器不可用。

热座不能与 `--offline`／`--developer` 合用；两个热座选项必须成对。现有共享 pending 会阻止新建该轮会话；活动会话的 `pending` 同时展示纯内存事实与独立的 `shared_pending`。正常退出会同时检查两者，保留原文件，不把空内存当作可以接管。此入口不创建补丁、不启动对局，也没有扩展到任意地图或更多席位；其真实交接验收仍按专用实验范围执行。

`scripts/hotseat_endurance.py` 是 2026-10-24 历史实验的复现器，仍向模型注入直接 MCP，必须显式提供 `--acknowledge-legacy-mcp`。它不属于当前支持的操作入口，不得用于新对局或替代上述 CLI 热座流程。

