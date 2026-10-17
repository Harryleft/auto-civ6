# Civilization VI 本机规则知识

这里保存市政、科技解锁、区域建设和世界议会的离线资料。先选择主题，再展开相关规则；整个 `civ6_knowledge` 目录可独立复制，查询仅需 Python 3 标准库。

## 适用范围

本次采集对应内部版本 `1.0.12.54 (951533)`、macOS 包 `1.4.6`／`376653.178`、Steam Build `15296837`；目标规则集为 `RULESET_EXPANSION_2`（《风云变幻》）。完整来源指纹、覆盖数和未验证项以 [manifest.json](manifest.json) 为准。

规则数据库是本机最后生成的缓存快照。当前加载存档、活动 DLC／模组／模式与该快照的匹配尚未验证。版本匹配只表明资料的版本字段一致；执行前仍需读取本局状态、合法候选和实际费用。

## 按问题展开

| 问题 | 先读主题 | 继续展开的条件 |
| --- | --- | --- |
| 文化路线、政策与政体解锁 | [市政](topics/civics.md) | 比较前置、鼓舞条件或具体解锁对象时 |
| 科研路线、尤里卡与科技解锁 | [科技](topics/technologies.md) | 比较前置、解锁用途或特殊限制时 |
| 区域落位、人口与相邻收益 | [区域建设](topics/districts.md) | 选择具体区域／地块，或核对加成条件时 |
| 议会议题、阶段与投票资格 | [世界议会](topics/congress.md) | 查看决议效果、目标类型、提案和禁投定义时 |

主题指南解释使用方法与机制边界；`rules/*.json` 保存实体规则卡；[sources/snapshot.json](sources/snapshot.json) 保存原始表行、双语文本与脚本证据。规则卡的 `source_refs` 用表名及行索引定位快照，关联明确区分已覆盖条目与外部对象。主题中的策略取舍属于解释，不等于游戏原始规则。

## 分层查询

在此目录执行以下命令；在项目根目录执行时给 `query.py` 加上 `civ6_knowledge/` 前缀。

```sh
python3 query.py topics
python3 query.py search 写作 --topic technologies --limit 5
python3 query.py show TECH_WRITING
python3 query.py show TECH_WRITING --level rule
python3 query.py show DISTRICT_INDUSTRIAL_ZONE --level evidence
python3 query.py validate
```

默认返回摘要；需要完整条件时选 `rule`，需要核对原始记录时选 `evidence`。搜索结果同时报告匹配总数与截断状态；未命中表示本资料未找到，不能据此判断游戏没有该规则。沿关联读取必要前置和解锁后，条件足以支持当前判断即可停止；重要例外与主规则一起读取。

已知待比较环境时，执行 `python3 query.py check-profile PROFILE_JSON`；也可在子命令前使用 `--profile PROFILE_JSON`。字段以 `manifest.json` 的 `profile` 为准。出现不匹配时，先定位差异，保留兼容性未确认状态。

## 更新与扩展

已有受控快照时，运行 `python3 build.py --source SNAPSHOT --output DIRECTORY` 重建结构化资料；输出目录是生成目标，应与原始快照分开。构建不会连接游戏。重新采集本机来源、修改主题解释后，重新执行完整性和离线测试；保留来源指纹及覆盖缺口。

本期没有完整 Modifier 求值器、实时合法性判断或最优策略结论。下一批主题见 [整理优先级](NEXT.md)。
