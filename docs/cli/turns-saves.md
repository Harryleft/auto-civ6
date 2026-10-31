# 研究、回合与存档

[返回 CLI 索引](../cli.md)

复验开始前独立保存原现场；复验结束后先另存过程与结果，再恢复原检查点。恢复完成后，重新读取 `state overview`、`unit list`、`city list`，再用 `pending` 核对所有原请求已确认且 `transport_unknown=null`，才允许后续写入或正常退出；不为清理而额外推进回合。

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `research show` | `get_tech_civics` | 只读 |
| `research set ID [--category CATEGORY]` | `set_research` | 变更 |
| `turn status` | `get_turn_status` | 只读 |
| `turn end` | `end_turn` | 变更 |
| `save list` | `list_saves` | 只读 |
| `save create NAME` | `save_game` | 变更 |
| `save reload NAME` | `reload_game_save` | 变更 |
| `save load NAME [--source-run RUN --sha256 SHA]` | `load_game_save` | 变更 |

`CATEGORY` 为 tech（默认）或 civic，研究 `ID` 为本次候选的原生类型。`save create` 可能覆盖同名普通存档，须使用独立名称；`save reload` 会丢失保存后现场，`save load` 仅从主菜单加载。文件系统存档回退当前只扫描 Single，不能据此声明 Hotseat 恢复可用。`turn end` 不替代双 AI 热座专用席位交接；热座沿用任务指定控制器及轮次记录，不并行另开 CLI 控制它。

`save reload`、`save load` 和 `game recover` 只接受本 CLI 登记过的检查点：`save create` 完成原生确认后登记身份与指纹，原生 `AutoSave_*` 不在登记表中。名字未登记时命令拒绝执行并说明原因，同时列出本轮可用的已登记检查点名称。FireTuner 一次只接受一个客户端连接；连接被拒时先结束其他 `civ6 debug` 会话或残留的 `civ-mcp` 进程再重试。

用户授权从历史阶段另开测试时，`save load` 可成对提供 `--source-run` 与 `--sha256`，在完整加载确认后新建独立轮次。原历史检查点保留；默认加载和恢复仍要求当前轮次。来源校验与另存起点要求见[保存与恢复](../agent-recovery.md#检查点与测试轮次)。

