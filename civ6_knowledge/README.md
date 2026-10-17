# Civ6 本机规则知识

市政、科技解锁、区域建设和世界议会的离线资料。目录可独立复制，查询只需 Python 3 标准库。

## 读取路径

| 当前问题 | 主题 |
| --- | --- |
| 文化路线、政策与政体解锁 | [市政](topics/civics.md) |
| 科研路线、尤里卡与科技解锁 | [科技](topics/technologies.md) |
| 区域容量、落位与相邻收益 | [区域建设](topics/districts.md) |
| 议题、阶段与投票资格 | [世界议会](topics/congress.md) |

先读主题，再按实体 ID 查询；条件足够支持当前判断时停止展开。`topics/` 解释机制，`rules/` 保存规则卡，`sources/` 保存原始证据。通过命令读取单条记录，避免整份加载 JSON。

## 资料边界

- 目标规则集为《风云变幻》。内部版本、包版本、Steam Build 和来源指纹以 [manifest.json](manifest.json) 为准。
- 来源为本机规则缓存；当前存档及活动 DLC／模组／模式尚未核对。本局候选、进度、费用和地块状态从游戏读取。
- 未命中不能证明规则不存在；外部关联未独立建卡。本期不完整解释 Modifier、脚本条件和活动随机树；策略取舍结合本局事实判断。

## 查询

以下命令在知识目录执行；从项目根目录先进入 `civ6_knowledge`。

```sh
python3 query.py search 写作 --topic technologies --limit 5
python3 query.py show TECH_WRITING
python3 query.py show TECH_WRITING --level rule
python3 query.py show TECH_WRITING --level evidence
```

默认读取摘要，`rule` 展开条件与关联，`evidence` 返回原始行、双语文本和相关脚本。搜索同时返回总数与截断状态；证据中的 `source_refs` 定位来源表及行索引。

核对已知环境：`python3 query.py check-profile PROFILE_JSON`。输入字段取自清单的 `profile`；版本不匹配时拒绝兼容结论。

## 更新

按顺序执行：

```sh
python3 capture.py
python3 build.py --source sources/snapshot.json --output .
python3 query.py validate
```

`capture.py` 只读采集本机来源，其他安装位置用 `--help` 指定。来源变化、缺失或缓存早于真实启动时间时保留旧快照并报错。修改指南后从构建步骤开始，刷新文件指纹；独立重建时将 `--output` 指向新目录。

下一批主题见 [整理优先级](NEXT.md)。
