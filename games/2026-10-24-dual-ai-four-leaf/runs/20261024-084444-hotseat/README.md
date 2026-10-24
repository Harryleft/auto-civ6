# 原生热座：首轮双 AI 控制与重载通过

原始基准 `benchmark_gold_tun_1`，神级、极小、四叶草、第一回合，游戏种子 `-1896272000`。仓库原件和实际加载的 Single 目录原件 SHA-256 均为 `0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`，结束后仍一致。上一轮失败现场保留。

## 实机结果

| 检查 | 结果 |
|---|---|
| 实际进程 | 独立 `Civ6-Hotseat.app` 内的 ARM64 引擎；应用包 1.4.6 / 376653.178，游戏 1.0.12.54 (951533) |
| 初始装载 | 通过原生 `Network.LoadGame` 的 HOTSEAT 服务类型载入同一原始地图；在准备室把苏美尔 0、瑞典 1 指定为人类 |
| Codex／苏美尔 | 开始原生交接，查询单位，驻防勇士 131073，跳过开拓者 65536；`HS_HANDOFF|from:0|to:1|turn:1|both_human:true` |
| 实际 Claude Code／瑞典 | 本机 Claude Code 2.1.296；独立会话 `5dd08216-246d-4885-a7be-a75f2b7057e3`，临时 MCP 配置绑定瑞典。查询、开始交接、驻防勇士 131073、跳过其余 5 个单位；`HS_HANDOFF|from:1|to:0|turn:2|both_human:true` |
| 内置 AI 隔离 | 交接前后及重载后均确认两文明为人类。双方城市数保持 0，单位坐标不变；苏美尔仍在 `(40,25)/(39,25)`，瑞典仍在原始 6 个位置。双方勇士驻防状态正常推进；没有上一轮苏美尔自动建城、移动的现象 |
| 独立 GameCore 回读 | `CORE 2 0 true true true false`：第二回合、本地苏美尔、双方人类、苏美尔活动、瑞典等待 |
| 错误席位保护 | 在苏美尔 T2，用瑞典绑定入口请求驻防，返回 `WAIT|Seat 1; native current seat 0, turn 2`，没有提交动作 |
| 原始游戏保全 | Steam 原始引擎 SHA-256 仍为 `17e6b8de23ccaf05d50d9071f3fa1c63392b51e1ec089469bee710127399c5d5`；补丁只在独立应用副本 |

## 保存、重载与当前状态

另存热座检查点 `dual_hotseat_20261024_084444_t002`，431,714 字节，SHA-256 为 `c50b732d9cada7e9ea72492c78e3bd049dba472fa3d7b0dfd8f04d710841e97a`。保存依据为原生类别 3、SaveComplete、实际新文件和状态回读。

本轮通过主菜单重载该检查点。在准备室，瑞典默认回到电脑槽位，因而先重新指定人类并验证两个人类后才启动。重新进入游戏后，回合、本地玩家、暂停、双方槽位与人类标志、城市数、全部单位 ID／坐标／行动力／驻防回合数，以及巴西和加拿大的记录行，与保存前全部一致。

当前停在实验副本的苏美尔第 2 回合原生交接界面，暂停中；Claude Code 会话已自然结束，无后台模型轮询。原始存档未覆盖。默认单人 MCP 的 `end_turn` 不用于此局，继续使用本轮固定席位入口。

## 实现和检查

[工具与机制说明](../../../../docs/specs/2026-10-24-native-hotseat.md)记录补丁、回滚和启动方式。工具测试 14 项通过；ARM64 原生指令仿真、函数起点及展开信息检查通过；实验副本签名校验通过。

初次连接交接遇到原生监听重新开放延迟，修复为只读预检的有界重连。初次 PlayerChange 调用误用了不存在的全局密码函数，错误发生在 OnOk 前；只读确认界面仍开启、仍暂停后，改用原生 PlayerConfiguration 方法，随后成功。本轮没有盲目重发可能已生效的动作。

这里只验证首轮控制及保存恢复，未开展长局、建城生产、外交议会或胜负比较。原地图的神级开局单位差异保留。

## 原始证据

原始证据均在本轮 `evidence/`，默认不提交 Git：

- `patch-manifest.json`、`offline-patch-verification.json`：版本、补丁指纹及真实指令仿真。
- `operation-*.json` 与对应 Lua：原生加载、准备室人类设置、启动、保存及重载的单次提交记录。
- `hotseat-t001-initial.json`、`before-claude-native-handoff.json`、`hotseat-t002-after-both-agents.json`：双人身份和单位状态。
- `codex-seat0-third.json`、`claude-native-hotseat.jsonl`、`agent-tools.jsonl`：两个实际入口的工具过程。
- `hotseat-checkpoint.json`、检查点文件副本、`reload-comparison.json`：保存完成、文件指纹和逐行重载比较。
- `wrong-seat-refused.json`、`gamecore-human-readback.json`、`process-vmmap.txt`：席位拒绝、GameCore 身份及 ARM64 进程证明。
