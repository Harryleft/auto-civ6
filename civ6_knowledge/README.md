# Civ6 本机规则知识

九个主题的本机版本规则快照，通过 MCP 按需查询；目录可独立复制，备用 CLI 只需 Python 3 标准库。

## MCP 查询

规则、前置或机制不清楚时，先搜索名称或内部 ID，再按结果 ID 读取。查询无需游戏启动或客户端文件权限。

服务器默认定位源码仓库中的 `civ6_knowledge`；非源码安装需将完整目录放在服务器所在机器，并设置服务器环境变量 `CIV6_KNOWLEDGE_DIR` 指向该目录。客户端不传入文件路径。新增工具或更改配置后，重新启动 MCP 服务即可，无需重启游戏。

| 工具 | 参数 |
| --- | --- |
| `search_knowledge` | `query`：中英文名称或 ID 子串，忽略大小写；`topic`：可选，见下表；`limit`：默认 5，范围 1～20 |
| `get_knowledge` | `entry_id`：搜索返回的 ID；`level`：默认 `summary`，可选 `rule`、`evidence` |

```text
search_knowledge(query="写作", topic="technologies", limit=5)
get_knowledge(entry_id="TECH_WRITING", level="rule")
```

搜索不支持整句语义或全文检索。结果包含总数和截断标记；零命中可换用名称、英文或 ID，不能据此判定规则不存在。`summary` 主要用于确认身份；规划与条件判断读取 `rule`，规则冲突或来源不清时再读取 `evidence`。证据中的 `source_refs` 定位来源表与行。

回复附带版本快照与适用边界，不验证当前存档、活动模组或操作资格。规则明确后，用对应游戏工具核对本局候选、费用、地块和进度；无需每回合重复查阅。

## 读取路径

| 当前问题 | 主题 | `topic` |
| --- | --- | --- |
| 文化路线、政策与政体解锁 | [市政](topics/civics.md) | `civics` |
| 科研路线、尤里卡与科技解锁 | [科技](topics/technologies.md) | `technologies` |
| 区域容量、落位与相邻收益 | [区域建设](topics/districts.md) | `districts` |
| 议题、阶段与投票资格 | [世界议会](topics/congress.md) | `congress` |
| 产出、人口、住房、宜居度与生产 | [经济与成长](topics/economy.md) | `economy` |
| 资源供应、供电、灾害与海平面 | [资源、电力与气候](topics/resources.md) | `resources` |
| 路径、攻击、攻城、晋升与编队 | [战斗与移动](topics/combat.md) | `combat` |
| 关系、联盟、城邦、商路与交易 | [外交与贸易](topics/diplomacy.md) | `diplomacy` |
| 胜利条件、项目、信条与传播 | [胜利与宗教](topics/victory.md) | `victory` |

需要主题导览时读对应链接；规则足够支持当前判断时停止展开。`topics/` 保存指南与机制定义，`rules/` 保存规则卡，`sources/` 保存原始证据；避免整份加载 JSON。

## 资料边界

- 目标规则集为《风云变幻》。内部版本、包版本、Steam Build 和来源指纹以 [manifest.json](manifest.json) 为准。
- 来源为本机规则缓存，缺失文本补自安装文件；活动 DLC／模组／模式尚未核对。
- 外部关联未独立建卡。Modifier 与需求集合仅保留关联，不求值；脚本条件和活动随机树未完整解释。百科与规则表冲突时，数值以规则表为准，差异随相关卡保留。

## 查询

CLI 用于备用查询与维护。在知识目录执行；从项目根目录先进入 `civ6_knowledge`。

```sh
python3 query.py search 写作 --topic technologies --limit 5
python3 query.py show TECH_WRITING
python3 query.py show TECH_WRITING --level rule
python3 query.py show TECH_WRITING --level evidence
```

核对已知环境：`python3 query.py check-profile PROFILE_JSON`。输入字段取自清单的 `profile`；版本不匹配时拒绝兼容结论。

## 更新

按顺序执行：

```sh
python3 capture.py
python3 build.py --source sources/snapshot.json --output .
python3 query.py validate
```

`capture.py` 只读采集本机来源，其他安装位置用 `--help` 指定。来源变化、缺失或缓存早于真实启动时间时保留旧快照并报错。修改指南后从构建步骤开始，刷新文件指纹；独立重建时将 `--output` 指向新目录。

后续覆盖见 [整理优先级](NEXT.md)。
