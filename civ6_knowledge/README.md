# Civ6 本机规则知识

十个主题的本机版本资料。项目日常查询使用[CLI 命令手册](../docs/cli.md)中的 `knowledge search/show`，底层通过 MCP 按需读取；目录可独立复制，独立维护 CLI 只需 Python 3 标准库。

## 查询

<a id="mcp-查询"></a>

### CLI 查询与内部协议

在同一个 `civ6 debug` 或 `debug --offline` 会话中执行 `knowledge search`，再用 `knowledge show` 读取；下表保留底层工具合同。规则、前置或机制不清楚时，先搜索名称或内部 ID，再按结果 ID 读取。查询无需游戏启动或客户端文件权限。

服务器默认定位源码仓库中的 `civ6_knowledge`；非源码安装需将完整目录放在服务器所在机器，并设置服务器环境变量 `CIV6_KNOWLEDGE_DIR` 指向该目录。客户端不传入文件路径。新增工具或更改配置后，在无未决状态下正常结束并重新启动 CLI 会话，无需重启游戏。

| 工具 | 参数 |
| --- | --- |
| `search_knowledge` | `query`：中英文名称或 ID 子串，忽略大小写；`topic`：可选，见下表；`limit`：默认 5，范围 1～20 |
| `get_knowledge` | `entry_id`：搜索返回的 ID；`level`：默认 `summary`，可选 `brief`、`rule`、`evidence` |

```text
knowledge search 写作 --topic technologies --limit 5
knowledge show TECH_WRITING --level brief
```

搜索不支持整句语义或全文检索。结果包含总数和截断标记；零命中可换用名称、英文或 ID，不能据此判定规则不存在。`summary` 主要用于确认身份；规划与条件判断先读 `brief`，需要逐行展开时读 `rule`，来源冲突时读 `evidence`。

`brief` 保留完整规则卡，通过来源表分组、父级 ID 和公共字段减少重复。`common_facts`、`common_record` 适用于组内每一行，`by` 字段由分组键提供；行格式为 `[原序号, 来源行, 覆盖字段, 可选记录元数据]`。条件、负值、反向需求、重复记录及未应用边界均保留，不求值、不排序候选、不截断；`Table#row` 对应完整视图的来源表与行。`encoding.card_sha256` 标识完整卡，`profile.sha256` 标识完整资料环境；`evidence` 可展开完整 profile。静态适用边界在精简视图中仍为未验证。

回复附带版本快照与适用边界，不验证当前存档、活动模组或操作资格。规则明确后，用对应游戏工具核对本局候选、费用、地块和进度；无需每回合重复查阅。

### CLI 备用

下列独立 query.py CLI 用于脱离项目的分发查询和知识包维护；项目内常用查询仍走上面的 civ6 会话。在知识目录执行；从项目根目录先进入 `civ6_knowledge`。

```sh
python3 query.py search 写作 --topic technologies --limit 5
python3 query.py show TECH_WRITING
python3 query.py show TECH_WRITING --level brief
python3 query.py show TECH_WRITING --level rule
python3 query.py show TECH_WRITING --level evidence
```

核对已知环境：`python3 query.py check-profile PROFILE_JSON`。输入字段取自清单的 `profile`；版本不匹配时拒绝兼容结论。

## 读取路径

| 当前问题 | 主题 | `topic` |
| --- | --- | --- |
| 本方与已相遇对手的文明、领袖、能力和专属内容 | [文明与领袖](topics/civilizations.md) | `civilizations` |
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
- 文明资料区分 `local_cached_rules_snapshot` 与 `installed_definition_catalog`。后者只补充缓存缺少的身份，保留包内 XML、modinfo 条件及未应用的更新，不模拟加载／SQL，不覆盖缓存。各层均标记来源范围与未应用状态；缺失、继承环或冲突显式报错／未知。
- 安装目录或缓存中存在资料不证明购买、启用或当前对局适用。缺失文本补自带指纹的安装文件，缺少译文保留 fallback；对手隐藏议程和意图不由静态特性推断。
- 外部关联未独立建卡。Modifier 与需求集合仅保留关联，不求值；脚本条件和活动随机树未完整解释。百科与规则表冲突时，数值以规则表为准，差异随相关卡保留。

## 更新

按顺序执行：

```sh
python3 capture.py
python3 build.py --source sources/snapshot.json --output .
python3 query.py validate
```

`capture.py` 只读采集本机来源，其他安装位置用 `--help` 指定。来源变化、缺失或缓存早于真实启动时间时保留旧快照并报错。修改指南后从构建步骤开始，刷新文件指纹；独立重建时将 `--output` 指向新目录。

后续覆盖见 [整理优先级](NEXT.md)。
