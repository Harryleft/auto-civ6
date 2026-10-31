# CLI 命令手册

本页只定位相关说明，不一次展开所有命令。连续操作仍使用同一个 `uv run --locked civ6 debug`；MCP 是内部通信实现。先查当前命名命令短帮助中的参数与边界，详细条件按下列小节展开。

```text
help                 # 选择命令组
help city            # 查看本组命令
help city list       # 只读当前命令的字段、边界及文档位置
city list --help     # 等价入口；不查询游戏
```

只读帮助不核验当前实体、资格或 pending；变更仍先 `--dry-run`，结果未知保留原请求。普通实体读取可能确认原动作，先核对已有回执与 pending，再决定是否需要原参数确认。需要什么再读什么，不为单个命令通读下列全部页面。

| 当前问题 | 先读短帮助 | 需要展开时 |
| --- | --- | --- |
| 单位、晋升、战斗、传教 | `help unit COMMAND` | [单位](cli/units.md) |
| 逐城宗教、工作地块、增长、购买 | `help city COMMAND` | [城市](cli/cities.md) |
| 回合与检查点 | `help turn COMMAND`、`help save COMMAND` | [研究、回合与存档](cli/turns-saves.md)、[保存与恢复](agent-recovery.md) |
| 地图收益、规则知识 | `help map area`、`help knowledge COMMAND` | [地图与知识](cli/map-knowledge.md) |
| 政府当前窗口、政策、总督 | `help government COMMAND`、`help policy COMMAND` | [政府与政策](cli/government.md) |
| 外交、交易、议会、弹窗 | 对应命令组的具体命令帮助 | [外交入口](cli/diplomacy.md)、[特殊操作](agent-special-operations.md) |
| 状态、未知结果、会话与预演 | `help state`、`help pending` | [状态查询](cli/state.md)、[会话](cli/debug.md)、[预演](cli/dry-run.md) |

以下保留既有小节锚点。每条链接仅展开原主题，不触发 CLI 或游戏调用。

## 安装与调用

[按需读取：安装与调用](cli/overview.md#安装与调用)

## 顶层命令

[按需读取：顶层命令](cli/overview.md#顶层命令)

### 顶层选项

[按需读取：顶层选项](cli/overview.md#顶层选项)

## 回合耗时统计

[按需读取：回合耗时统计](cli/timing.md#回合耗时统计)

## JSON 与退出码

[按需读取：JSON 与退出码](cli/discovery.md#json-与退出码)

## 组合与发现

[按需读取：组合与发现](cli/discovery.md#组合与发现)

## 决策事实查询

[按需读取：决策事实查询](cli/state.md#决策事实查询)

## 自动实机只读验收

[按需读取：自动实机只读验收](cli/state.md#自动实机只读验收)

## 会话内领域子命令

[按需读取：会话内领域子命令](cli/discovery.md#会话内领域子命令)

### 单位

[按需读取：单位](cli/units.md#单位)

### 城市

[按需读取：城市](cli/cities.md#城市)

### 研究、回合与存档

[按需读取：研究、回合与存档](cli/turns-saves.md#研究回合与存档)

### 地图、胜利与知识

[按需读取：地图、胜利与知识](cli/map-knowledge.md#地图胜利与知识)

### 政策、城邦与总督

[按需读取：政策、城邦与总督](cli/government.md#政策城邦与总督)

### 议会与通知

[按需读取：议会与通知](cli/congress.md#议会与通知)

### 科技、市政和新增领域命令

[按需读取：科技、市政和新增领域命令](cli/domains.md#科技市政和新增领域命令)

### 外交、生命周期与完整命名入口

[按需读取：外交、生命周期与完整命名入口](cli/diplomacy.md#外交生命周期与完整命名入口)

### 开发与热座模式

[按需读取：开发与热座模式](cli/modes.md#开发与热座模式)

### 最小调用顺序

[按需读取：最小调用顺序](cli/call-sequence.md#最小调用顺序)

## 副作用预演

[按需读取：副作用预演](cli/dry-run.md#副作用预演)

## 前台 debug 会话

[按需读取：前台 debug 会话](cli/debug.md#前台-debug-会话)

## 证据与清理

[按需读取：证据与清理](cli/evidence.md#证据与清理)
