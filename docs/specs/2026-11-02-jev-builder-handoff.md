# 建造者两阶段 Jev 建议实验

本实验实现文件式建议通信：主模型提出任务，Jev 选择候选，主模型批准有边界的下一步；到达后根据新原生事实另行判断建设。它不是自动对局功能，不改变 Runtime、游戏命令或未决门禁。

## 职责与阶段

| 环节 | 主模型 | Jev／辅助工具 |
| --- | --- | --- |
| 准备 | 用同一 CLI 读取局面、路径、城市需要；提出有限的竞争任务 | 不枚举地图，不生成策略 |
| task | 写目标、位置、次数、收益依据和未知项 | 在主模型给出的 move／wait／query 中选择一个 |
| 复核 | 接受、覆盖或升级建议，明确批准的单步 | 校验包、回复、局面声明的指纹绑定 |
| 移动 | 通过命名 CLI 预演、单次提交、回读 | 不连接游戏，不派发指令 |
| arrival | 确认实际到达，读取新原生动作候选 | 在新 act／wait／query 候选中选择一个 |
| 执行 | 复核具体代价，通过 CLI 执行一个动作并核对收益 | 无自动执行、重试、回合循环 |

`query` 是候选之一，Choice 不生成“缺什么信息”的解释。主模型需要判断是否确有值得补查的事实；也可以明确覆盖建议。不能为获得同意而反复调用 Jev。置信度与选项概率分别保留，不作为执行权限或固定正确率阈值。

task 中的“去某地建矿”只批准目的地移动，不批准未来建矿。实际未到达、行动力耗尽、预期动作不可用或情况变化时，由主模型等待、补查或重新分配任务，不能任取另一个可用动作替代。

## 文件契约

实验辅助工具为 [jev_builder_advice.py](../../scripts/jev_builder_advice.py)。它只读明确给定的文件、发送一次 TypeSafe 请求、写建议结果，不读取游戏、不持有 relay 地址、不生成 CLI 命令。

- `packet`：`version=1`、`stage`、`goal`、`snapshot`、`facts`、`observations`、`candidates`。局面声明包含轮次、文明、种子、玩家、回合、单位与坐标。原回执以路径及原始文件 SHA-256 关联；主模型区分观测、解释和未知。
- `candidates`：固定 ID 对应下一步、描述、未知项。task 只能 move／wait／query，move 必须给出目标；arrival 只能 act／wait／query。两者都保留 wait 或 query。
- `reply`：输入的规范化 SHA-256、实际 HTTP 请求 SHA-256、实际模型、完整候选概率与置信度、使用量和往返时间。回复必须精确覆盖本次候选。
- `review`：主模型填写输入／回复／局面声明指纹、所选候选、accept／override／escalate、批准的下一步及理由。accept 不能更换候选；escalate 不能批准动作；复核不能扩大原候选的单步范围。
- arrival 的 `parent`：关联 task 包、回复和复核的文件 SHA-256。必须来自已复核的 move，文明／种子／玩家／单位／轮次声明一致，实际位置声明等于批准的目的地，回合不倒退。

本次 act 只实现 **improve** 的原生证据契约：每个动作明确 `native_action.kind=improve`、`target` 和 `improvement`，并且只选择一份 `get_decision_context` 原始 CLI 资格回执。校验 ready、稳定世界／回合、完整 pending 全 false、身份匹配、精确建造者 ID／坐标，以及其 `Can build` 中明确列出的改良。其他事实仍可引用多份。砍伐、收割、修复等动作尚未接入此校验，不能借一个任意文件引用声称已验证资格。

这些校验只证明记录内对应关系。`game_run` 是父子包声明的轮次，不是原生 decision 返回的世界代际；文件指纹不是签名，也不能证明主模型的文字解释真实或此刻游戏未变化。**`valid` 不是游戏执行许可**。当前状态、原生资格、未决请求、单次提交及结果确认仍由主模型和现有 Runtime 在真实 CLI 调用时检查。

## 显式调用

在仓库根目录运行；实际包结构可参照 [本轮证据与结果](../../games/benchmark-sumeria-719665280/runs/2026-11-02-jev-chain/reports/results.md)。证据中的每份资格回执必须来自当次真实查询，不能把历史包直接用于当前游戏。

```sh
# 仅校验文件；无网络、无游戏操作。
uv run --locked python scripts/jev_builder_advice.py validate /path/to/task-packet.json

# 一次显式 Jev 请求，凭证仅取 TYPESAFE_API_KEY 环境变量。
uv run --locked python scripts/jev_builder_advice.py ask /path/to/task-packet.json --output /path/to/new-task-reply.json

# 主模型填写复核后，只检查文件绑定，不执行批准的动作。
uv run --locked python scripts/jev_builder_advice.py check-review /path/to/task-packet.json /path/to/new-task-reply.json /path/to/task-review.json
```

arrival 使用同样的三个命令，输入另行准备的新包。游戏动作始终由主模型在现有持续 debug 会话通过命名 CLI 执行。

输出文件在发送前独占创建。已有文件即拒绝，不覆盖；失败或中断也不自动重发。HTTP 禁止重定向，避免额外发送或转发凭证；不记录请求头、异常正文或无关响应字段。请求使用 `jev-latest`，每次保留实际返回版本，不宣称本机安装了新模型。

## 验证与限制

[离线测试](../../tests/test_jev_builder_advice.py) 覆盖阶段权限、错包／错回复、错误单位／目标／回合、非肯定或冲突资格、未知／不完整 pending、非法概率、单次发送、失败后不覆盖重发及实际本地 HTTP 重定向拒绝。只连接隔离本地 HTTP 夹具，不连接游戏或真实 Jev。

[T73 实机记录](../../games/benchmark-sumeria-719665280/runs/2026-11-02-jev-chain/reports/results.md) 证明链路经过真实移动、原生资格和改良回读。两次建议均被主模型覆盖，不能据此宣称 Jev 提高质量或速度。多建造者任务冲突、跨回合计划失效、其他动作证据和完整对照性能仍未验证。

接口形状依据 [TypeSafe System One API](https://docs.typesafe.ai/api/system-one)、[Choice](https://docs.typesafe.ai/primitives/choice) 和 [State](https://docs.typesafe.ai/concepts/state)，于 2026-11-02 核对；项目的阶段、复核和执行边界由本实验定义。
