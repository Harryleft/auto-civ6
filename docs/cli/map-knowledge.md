# 地图、胜利与知识

[返回 CLI 索引](../cli.md)

| 会话命令 | MCP 工具 | 模式 |
| --- | --- | --- |
| `map area X Y [--radius N]` | `get_map_area` | 只读 |
| `victory show` | `get_victory_progress` | 只读 |
| `knowledge search QUERY [--topic TOPIC] [--limit N] [--view brief\|evidence]` | `search_knowledge` | 静态只读 |
| `knowledge show ID [--level LEVEL]` | `get_knowledge` | 静态只读 |

地图半径默认 2、范围 1～4，越界直接拒绝。知识搜索 limit 默认 5、范围 1～20；LEVEL 为 summary（默认）、brief、rule 或 evidence。brief 保留完整规则，合并来源、父级 ID 和公共字段；公共字段作用于组内每行，格式见[知识说明](../../civ6_knowledge/README.md#cli-查询与内部协议)。静态规则不证明本局资格；迷雾、未知和不存在仍须区分。知识查询也可在 `debug --offline` 内执行。

`map area` 的收益来自每次 GameCore_Tuner 查询中的 `Plot:GetYield`，保留原生小数；报告一次性标注来源。该上下文的修正完整性与原生数据年龄未验证，不能直接等同 InGame 城市工作地块收益，也不能把两者差异自动解释为缓存、基础值或缺少某项政策。收益块始终保留 F／P 的零值，省略的 G／S／C／Fa 明确为零；没有收益块表示未取得收益，不表示全零。迷雾与归属范围不变。

`knowledge search` 默认 `--view brief`，搜索结果、计数、截断、覆盖和适用范围均完整保留。已知重复的嵌套环境资料通过 `profile_reference` 引用，包含完整 profile 的 canonical SHA-256、`omitted_fields` 和同一 query／topic／limit 的 `view=evidence` 展开参数；零命中也可展开。profile 的标量、警告及尚未识别的新字段保留。`--view evidence` 返回完整 profile；内部 search API 默认 evidence，以兼容原调用。

