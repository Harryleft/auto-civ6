# civ6-belief-engine 产品边界

`civ6-belief-engine` 是产品名和 Python 发行包名；MCP 只是它对外连接
Civilization VI 的一个适配模块。

## 稳定的 MCP 兼容面

以下名称保持不变，避免破坏 DSH、现有 MCP 客户端和用户脚本：

- Python 适配包：`civ_mcp`
- CLI 入口：`civ-mcp`
- MCP server key：`civ6`
- 工具前缀：`mcp__civ6__*`
- FireTuner 握手标识：`APP:civ6-mcp`
- 既有运行数据目录：`~/.civ6-mcp`

## 目录分层

```text
src/
├── civ6_belief_engine/       # 产品领域层：信念、治理、图、策略状态
│   ├── governance/
│   └── graph/
└── civ_mcp/                  # MCP 适配层 + 游戏运行时
    ├── server/               # MCP 入口包：assembly 装配、pipeline 运行管道、tools/ 按域工具
    ├── lua/                  # Civ 6 Lua 查询/动作工具
    ├── connection.py         # FireTuner 连接
    └── ...                   # 生命周期、存档、遥测等运行时能力
```

实现位于 `civ6_belief_engine/`，`civ_mcp` 只保留适配层职责。旧的
兼容转发 shim（`civ_mcp/belief_engine.py`、`civ_mcp/governance/`）
已移除，测试与代码直接导入产品域包；`civ_mcp/belief_mode.py` 仍是
MCP 侧真实的运行模式逻辑。

域包对 `civ_mcp` 已零运行时导入：`TypedTurnSnapshot` 是 domain 侧边界
类型，具体 Lua DTO 只由 `GameState` 采集端转换为该类型；Department 通过
窄 `GraphSnapshotView` 消费治理快照，不再读取旧快照字段。

## 产品名与技术名

安装和发行包使用：

```bash
uv sync
uv pip install 'civ6-belief-engine[launcher-macos]'
```

启动 MCP 仍使用：

```bash
uv run civ-mcp
```

仓库 URL、仓库目录和 MCP 技术标识暂不因产品改名而自动改变；只有在远
端仓库也正式改名后，才应同步更新 clone URL 与 DSH 工作目录说明。
