# 可复用 Civ6 存档

保留原有评测场景的初始存档，供本机游玩和固定存档诊断使用。评测执行框架已移除；文件内容保持原样。

| 文件 | 文明 / 领袖 | 地图 | 难度 | 对手数 |
|---|---|---|---|---:|
| `0A_GROUND_CONTROL.Civ6Save` | Babylon / Hammurabi | Pangaea Standard | Prince | 7 |
| `0B_SNOWFLAKE.Civ6Save` | Korea / Seondeok | Six-Armed Snowflake Small | King | 5 |
| `0C_CRY_HAVOC.Civ6Save` | Sumeria / Gilgamesh | Pangaea Tiny | Immortal | 3 |

场景记录为 Ancient 起始、Quick 速度、Gathering Storm、Turn 1。Snowflake 仅启用统治胜利，其余场景启用全部胜利；具体状态以加载后的游戏查询为准。

使用 `scripts/install_saves.py` 复制到当前平台的单人存档目录，然后手动加载或通过原生主菜单读档工具加载。
