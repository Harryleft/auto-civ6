# 实验基线清单

本页说明如何在任何对照运行之前记录一份可复现基线，以保证“精简路径 vs 原路径”的
比较不会被环境差异污染。工具是 `scripts/experiment_baseline.py`。

## 为什么需要基线

如果两轮运行的 Git 提交、实际模型、游戏规则或起始存档不同，那么观察到的差异无法
归因给精简设计。基线清单把这些字段固化下来，并且只使用**可以重新读取的来源**：
Git 提交、组合后的 DSH 配置、MCP 工具注册表、以及存档文件自身的头部。

客户端标签不能代替模型版本。`CIV_MCP_AGENT_MODEL` 的值是 `deepseek-harness`，
它标识的是客户端而非模型；清单里的模型标识来自组合后的 DSH 配置
（`agent-default-model` 行的 `provider`/`model`）。

## 硬边界

- **离线。** 不连接 FireTuner、不启动文明 VI、不调用模型。
- **不破坏。** 只**复制**存档，从不移动、改名、截断或删除游戏存档目录下的任何文件；
  若目标位置已有同名但字节不同的备份，脚本拒绝覆盖并报错。
- **独立目录。** 清单、日志和存档副本都放在仓库的 `experiments/`（已忽略），
  不写入用户正式存档目录。

## 用法

```bash
cd civ6-mcp

# 只列出本机可用的存档（含离线可读的速度/地图/难度/规则集）
uv run python scripts/experiment_baseline.py --list-saves

# 记录一份基线并把选中的中盘存档复制进实验目录
uv run python scripts/experiment_baseline.py \
  --run-id <运行编号> \
  --save 0_MCP_0141 \
  --backup-save \
  --play-profile legacy

# 只输出 JSON（便于后续脚本消费）
uv run python scripts/experiment_baseline.py --run-id <运行编号> --save 0_MCP_0141 --json
```

输出目录默认为 `experiments/<运行编号>/`，包含 `manifest.json` 与 `saves/`。
`--out` 可指定其它目录；测试与实验必须使用独立目录，不要复用用户存档目录。

退出码：`0` 正常；`1` 本机没有可用存档；`2` 指定的存档未找到，或备份因字节冲突被拒绝。

## 清单字段

| 字段 | 来源 | 说明 |
|---|---|---|
| `repo.commit` / `repo.branch` | `git rev-parse` | 运行对应的固定提交 |
| `repo.dirty` / `repo.dirty_files` | `git status --porcelain` | 有未提交修改时无法归因到提交，因此逐项记录 |
| `dsh.version` | `<checkout>/package.json` | DSH 版本，与 MCP 子进程无关 |
| `dsh.model` / `dsh.provider` | `--dump-config` 的 `agent-default-model` | 实际模型标识，不是客户端标签 |
| `dsh.sampling` | 同上 | 覆盖层中显式给出的采样参数；为空表示沿用 provider 默认值 |
| `dsh.config_sha256` | `--dump-config` 全文 | 组合后（而非 YAML 表面）配置的指纹 |
| `dsh.prompt_sha256` | `system-prompt` 行的 `persona` | 精简角色的指纹；`agent-instructions` 默认关闭 |
| `dsh.tool_call_timeout_ms` | `civ6.cordis.yml` | 宿主单次工具调用期限，必须大于 end_turn 总预算 |
| `tools.count` / `tools.names` / `tools.surface_sha256` | `civ_mcp.server.mcp` | 模型**实际**可见的工具面 |
| `save.*` | 存档头部 | 规则集、速度、地图尺寸、难度、启用的模组、SHA-256 |
| `save_backup` | 复制结果 | 备份路径与哈希；字节冲突时给出 `error` |
| `live_only_unverified` | 固定文本 | 只能由现场只读会话确认的项目 |

规则集、速度、地图与难度直接从存档头部读取（`RULESET_*`、`GAMESPEED_*`、
`MAPSIZE_*`、`DIFFICULTY_*_NAME`），与 `scripts/parse_save.py` 读取的区域相同。
这样即使没有运行中的游戏，也能确认两轮对照使用的是同一套规则与启用内容。

`map/` 目录下的导出**不是**可继续运行的 `Civ6Save`，也不是合法的游戏内情报，
不能用作可复现起始点。

## 与超时预算的关系

`dsh.tool_call_timeout_ms` 不是随手取的数字：它必须大于 `civ_mcp.end_turn`
按等待循环常量推导出的最坏单次 `end_turn` 总预算。
`tests/test_end_turn_budget.py` 会解析覆盖层并与该预算比较，因此调低期限或加长轮询
都会让测试失败，而不是静默产生“回合推进中途被杀”的不可判定结果。

## 仍需现场只读确认

以下项目无法离线判定，必须在获授权的单客户端现场会话中确认，不要凭记忆填写：

- 当前回合号与对局内实际启用内容。
- 游戏内加载的分支是否仍与备份的存档一致（用 `get_game_overview` 的回合号核对）。
- FireTuner 是否只有一个客户端。
