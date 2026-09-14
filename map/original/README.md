# map/original/ — 对局「ai-civ6-map-01」的原始文件

这里放的是**你在建局时保存的原始文件本体**（不是导出的 JSON 快照；导出快照在上级目录 `map/`）。

| 文件 | 说明 |
|---|---|
| `ai-civ6-map-01.Civ6Cfg` | 你保存并命名的对局/地图配置（37,656 字节，2026-09-13 16:42）。Civ6 把它识别为 “Sid Meier's Civilization VI saved game”。 |
| `AutoConfigGame_01.Civ6Cfg` | 同一局开始时游戏自动写出的配置（29,946 字节，同一分钟）。 |
| `SHA256SUMS.txt` | 两份文件的 sha256，用于核对上传后与本地一致。 |

## 为什么"地图数据"是配置文件

Civ6 的地图是**开局时按配置里的参数与随机种子程序化生成**的，没有独立的 `.Civ6Map` 文件。
因此这套配置就是这张地图的"原始定义"：

- 需要**参数层面**的原件 → 就是本目录的两个 `.Civ6Cfg`；
- 需要**已经生成出来的地形/归属** → 上级目录的导出快照更直接：
  `map/mapstatic_turn141.json`（60×38=2280 格地形、归属、道路、37 座城市、11 名玩家）
  与 `map/mapturns_all.jsonl`（第 2–141 回合共 138 回合的城市/归属增量）。
- 需要**可继续游玩的存档** → 完整的对局状态在本地（未上传，1 MB 二进制）：
  `~/Library/Application Support/Sid Meier's Civilization VI/Sid Meier's Civilization VI/Saves/Single/0_MCP_0141.Civ6Save`
  （需要的话可以一并加进来。）

## 恢复到游戏里

把 `.Civ6Cfg` 放回游戏的存档目录：

```
~/Library/Application Support/Sid Meier's Civilization VI/Sid Meier's Civilization VI/Saves/Single/
```

然后在游戏内从设置/载入入口选用该配置（文件名即 `ai-civ6-map-01`）。
`.Civ6Cfg` 是二进制（内部压缩），不要用文本编辑器改写。

## 校验

```bash
cd map/original && shasum -a 256 -c SHA256SUMS.txt
```

## 出处

- 本局对局 id：`sumeria_-1162067840`（我方苏美尔，不朽，快速，60×38 地图）
- 导出时间：2026-09-14
- 对局在第 141 回合（世界议会回合）遇到本机引擎级卡死，故快照停在该回合；
  原因与恢复记录见 `docs/agent-recovery.md`。
