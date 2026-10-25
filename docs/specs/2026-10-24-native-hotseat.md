# 原生热座双 AI 实验

2026-10-24 实机通过四叶草神级存档的首轮双人操作、原生交接、保存和重载。Codex 控制苏美尔，实际 Claude Code 进程控制瑞典。双方在 InGame 与 GameCore 中均保持人类身份，工具未调用 `SetLocalPlayerAndObserver`。详细证据见[本轮记录](../../games/2026-10-24-dual-ai-four-leaf/runs/20261024-084444-hotseat/README.md)。

这是独立实验入口。默认单人 `civ6 debug` 生命周期不变，普通 `turn end` 不用于热座交接。

## 版本与补丁

仅支持本次核验的 macOS Steam 应用包 `1.4.6 / 376653.178`，游戏显示版本 `1.0.12.54 (951533)`，ARM64。原始 `Civ6_Exe_Child` SHA-256 为 `17e6b8de23ccaf05d50d9071f3fa1c63392b51e1ec089469bee710127399c5d5`。

补丁把 Tuner 自身的关闭条件从 `IsAnyMultiplayer()` 改为 `IsAnyMultiplayer() && !IsHotseat()`。只重定向一个调用点，增加 48 字节函数，并同步调整代码 section、函数起点及 compact-unwind 表；重新签名会产生额外文件差异。公共关闭函数、玩家类型判断、x86_64 指令及游戏规则不改。

[补丁工具](../../scripts/hotseat_tuner_patch.py) 只写独立应用副本。原件、未知版本、已修补输入、占用的填充区及与原件共享可签名 inode 的目标均拒绝修改。APFS clone 后对子可执行文件和应用包重新签名并校验，原始文件再次核对指纹。

```sh
uv run --with unicorn --with capstone python scripts/verify_hotseat_tuner_patch.py \
  --binary "/path/to/Civ6.app/Contents/MacOS/Civ6_Exe_Child"

python scripts/hotseat_tuner_patch.py \
  --source "/path/to/Civ6.app" \
  --destination .local/hotseat-tuner/Civ6-Hotseat.app \
  --manifest "$RUN/evidence/patch-manifest.json"
```

先保全并显式退出正在运行的对局，再启动副本。实测直接启动副本的 `Contents/MacOS/Civ6_Exe_Child`，首个参数为副本应用包绝对路径，工作目录为副本 `Contents/MacOS`，环境变量为 `SteamAppId=289070`、`SteamGameId=289070`；Steam 客户端保持登录。`open` 启动外层 stub 的尝试没有进入游戏，本轮成功路径未依赖该 stub。

回滚是退出实验副本并启动未修改的 Steam 原件；存档按对应测试轮次恢复，不能覆盖原始基准。副本位于 `.local/`，不提交游戏文件。

## 原始存档转入热座

使用原生 FrontEnd 查询取得目标文件，核对其实际路径和已登记 SHA-256。原始基准来自 `SaveTypes.SINGLE_PLAYER`，热座检查点来自本轮原生保存返回的类别 3。

在 `MainMenu` 中指定热座模式，再调用：

```lua
GameConfiguration.SetGameMode(GameModeTypes.HOTSEAT)
Network.LoadGame(nativeSaveFile, ServerType.SERVER_TYPE_HOTSEAT)
```

这会载入原有地图和状态并进入原生准备室。在本次版本的 `StagingRoom` 中，`OnSlotType(1, 4)` 把瑞典设为人类，并执行官方的玩家信息广播和界面更新。检查苏美尔、瑞典的文明身份、两者槽位均为 `SS_TAKEN`、人类数量为 2、开始按钮可用，再调用一次 `OnReadyButton()`。实际运行后还必须核对两名玩家的 `IsHuman()`，不能只凭准备室配置判断成功。

**本轮检查点重载时，准备室将瑞典默认列为电脑。** 重载流程必须重新指定瑞典人类槽位，再通过上述人数与身份检查后启动。运行入口在缺少任一人类席位时拒绝操作。本次重新指定后，保存前后的全部记录行一致；这不是存档自动记住第二个人类席位的证明。

操作源文件和单次提交记录保存在本轮 `evidence/`。开发用[原生控制台](../../scripts/hotseat_probe.py) 精确发现上下文、逐次释放连接，并拒绝复用已经登记的变更操作 ID；传输返回本身不代表游戏结果确认，仍须原生回执和后续回读。

## 两个模型的入口

两个模型都只通过 CLI 进入，各自启动一个绑定席位的会话：

```sh
uv run --locked civ6 debug --hotseat-seat 0 --hotseat-run "$RUN"
uv run --locked civ6 debug --hotseat-seat 1 --hotseat-run "$RUN"
```

它复用原[固定席位控制器](../../scripts/hotseat_agent.py)（由 CLI 拉起，内部仍是 stdio 服务），并沿用现有 `server.py`、GameState、GameConnection 和领域动作确认，不另造单位操作实现。`--hotseat-seat` 与 `--hotseat-run` 必须成对，且不能与 `--offline`／`--developer` 合用；该轮必须含已核验补丁清单，发现共享 pending 时拒绝新建会话。会话内用 `hotseat begin`、`hotseat end`、`hotseat actions` 完成本席位操作，普通 `turn end`、Single 存档操作和任意 Lua 在该控制器不可用。

> 2026-10-25 复核：`scripts/hotseat_endurance.py` 是上述实验的旧协调器，仍以直接 MCP 注入方式驱动两个模型，未迁移到 CLI 入口；它现在要求 `--acknowledge-legacy-mcp` 才允许运行，仅用于复现历史实验。

两个入口使用同一运行目录，席位 0 为苏美尔、1 为瑞典。每个工具持有跨进程控制锁，验证原生热座、种子、文明、双方人类身份及当前席位；结束后断开 FireTuner。监听重新开放有短暂延迟，只允许只读预检和确认在限定时间内重连。变更不自动重放。

`hotseat begin`（控制器内部 `begin_hotseat_turn`）调用原生 PlayerChange 的 `OnOk` 一次，分别确认暂停解除和手番可用。`hotseat end`（内部 `end_hotseat_turn`）提交原生 ENDTURN 一次，以控制交给下一名人类玩家为完成条件；同轮交接不要求全局回合号增加。未确认请求写入共享门禁，其他变更和新服务实例不能越过，普通查询不清除门禁。

该入口只暴露本次测试需要的查询、合法单位动作、研究与生产，以及热座开始／结束工具；不暴露任意 Lua、玩家切换、游戏退出或重启。单位动作限制为移动、取消移动、驻防、跳过和建城。结果未知时保留门禁，需核对原生事实或显式从本轮已确认检查点恢复。

两个模型都执行 `civ6 debug --hotseat-seat N --hotseat-run "$RUN"`；CLI 内部由固定席位控制器暴露本次实验允许的游戏命令。模型不注册 MCP、不直接连接内部服务；该控制器不暴露任意 Lua、玩家切换、游戏退出或重启。

## 验证范围

- 离线：14 项工具测试通过；真实 ARM64 指令仿真覆盖单人、热座、LAN、互联网、跨平台、Cloud、未知模式及无效值；栈／寄存器恢复和 74,150 个原函数的展开编码通过检查。
- 实机：苏美尔和瑞典均驻防一名勇士、跳过其余单位，并完成 `苏美尔 T1 → 瑞典 T1 → 苏美尔 T2`。GameCore 与 InGame 均回读双方人类身份；两文明城市数保持 0、单位位置保持原始位置。巴西和加拿大仍按原存档作为内置 AI 正常执行。
- 保存重载：原生 SaveComplete、新鲜非空热座文件、重载后的逐行状态比较通过；错误席位提交动作被拒绝。
- 未验证：长期稳定性、完整建城／生产／外交／议会流程、胜负表现，以及真实联网对局回归。原神级瑞典的额外开局单位保留，不能据首轮控制测试判断策略强弱。
