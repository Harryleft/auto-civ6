# 实机结果：未通过双 AI 独立控制

原件为 `benchmark_gold_tun_1`：神级、极小、四叶草、第一回合，游戏种子 `-1896272000`。原始文件 SHA-256 为 `0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`，测试结束重新计算仍一致。

## 实际结果

| 检查 | 实机证据 |
|---|---|
| 加载原件 | 苏美尔 ID 0：人类、活动手番、0 城市、开拓者与勇士各一；瑞典 ID 1：电脑、未活动、1 开拓者和 5 勇士 |
| 将瑞典槽位从电脑改成人类 | InGame 配置槽位从 1 变为 3，但运行中 `Players[1]:IsHuman()` 仍为 false；配置修改没有形成运行中的双人席位 |
| 调用原生玩家切换 | GameCore `PlayerManager.SetLocalPlayerAndObserver(1)` 后，瑞典成为活动人类，苏美尔变成电脑；InGame 随原生处理更新到相同状态 |
| 内置 AI 干扰 | Codex 没有发出苏美尔建城或移动命令，但苏美尔城市数从 0 变为 1，勇士从原始 `(39,25)` 移到 `(37,25)` 并耗尽行动力；全局仍为第一回合 |
| Claude Code 入口 | 实际执行本机 `/opt/homebrew/bin/claude`，版本 2.1.296，会话 `10aa0e28-b4ef-4d92-93f9-6da7cc61760d`，通过独立 stdio civ6 MCP 接入 |
| Claude Code 真实操作 | Claude 调用 `unit_action(unit_id=131073, action="fortify")`，得到 `FORTIFIED|unit:131073|ready:false`；其后只读回读及 Codex 独立回读均确认该瑞典勇士位于 `(17,25)`、`GetFortifyTurns()=1` |
| 恢复 | 通过正式 `reload_game_save` 加载测试前检查点，回读本地玩家 0、第一回合、苏美尔人类且活动、双方 0 城市；苏美尔单位回到 `(40,25)` 与 `(39,25)`、行动力均为 2；瑞典回到电脑槽位 1，勇士驻防回合数恢复 0 |

本次验证的是现有单人存档的直接席位修改和原生玩家切换路径，没有制作或应用热座 Tuner 二进制补丁，没有验证双人热座。运行时同时保留两个独立人类文明的目标未达到，不能据此比较两个 AI 的策略效果。原神级存档还保留瑞典的电脑开局单位优势，后续比较需明确是否保留这一配置。

## 连接与执行边界

Claude 首次接入时 Codex 持久连接尚未释放，三次查询连接失败。协调者随后用无游戏动作的受控 Lua 错误使正式连接层关闭自身连接；Claude 再接入后查询和驻防成功。因此两个客户端没有并发发出游戏操作，也没有重发结果未知的变更。

Claude 的只读探测阶段在所有请求均已返回后被 SIGINT 停止，以更正动作工具名称；最终驻防和回读完成后再次结束客户端。Claude 还曾请求 `kill_game`，被工具许可拒绝，游戏没有因此退出。证据保留原始调用及拒绝记录。本轮未修改 Claude 的全局设置。CLI 模型字段与模型自述不一致，未核实实际后端模型；确认的是实际 Claude Code 进程及其 MCP 工具调用。

## 存档与证据

- 测试前：`dual_ai_fourleaf_20261024_083313_t001_before`，396,893 字节，原生保存完成并登记为本轮检查点。
- 失败现场：`dual_ai_fourleaf_20261024_083313_failed_takeover_t001`，408,406 字节，保留苏美尔被内置 AI 接管、瑞典勇士驻防后的状态。
- 两份过程存档及原件副本均另存于 `evidence/`；文件指纹见 `evidence/save-evidence.json`。
- `evidence/codex-native-probes.json`：槽位修改、玩家切换及内置 AI 干扰回读。
- `evidence/claude-start.json`、`claude-sweden-probe.jsonl`、`claude-sweden-handoff.jsonl`、`claude-sweden-action.jsonl`：真实 Claude Code 启动和工具过程，均位于 `evidence/`。
- `evidence/final-readbacks.json`：驻防独立回读、失败现场保存和最终恢复确认。

未修改项目游戏控制代码、游戏二进制或游戏配置文件；变更只在本轮游戏内测试状态及独立记录、检查点中发生。未执行完整轮转或公平对抗验收。
