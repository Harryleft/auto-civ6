# Civ6 游戏机制知识库（官方百科蒸馏）

> **用途**：作为智能体做游戏内决策时的机制知识库，按需读取。
> **原则**：渐进式披露——每篇文档顶部是 **L0 决策速查**（可直接依据做决策），需要细节时再展开 **L1 机制**、**L2 数据附录**。不要把整篇复制进会话。
> **来源**：以 [Civilization Wiki (Fandom)](https://civilization.fandom.com/wiki/Civilization_VI) 为权威数据源，术语与中文名对照 [文明VI中文维基（灰机）](https://civ6.huijiwiki.com/wiki/文明)。与游戏内实际状态冲突时，**以游戏内为准**。

## 如何使用（渐进式披露）

1. 遇到决策需求，先在本表找到对应领域，读取该文档的 **L0 决策速查**（约 10 条内）。
2. 需要具体数值或机制细节时，再读取该文档的 **L1 / L2** 对应小节。
3. 跨领域决策（如宣战评估涉及军事 + 外交 + 经济）同时读取相关文档的 L0。
4. 知识库不替代回合规则（[docs/agent-turn-loop.md](../agent-turn-loop.md)）与策略（[docs/agent-strategy.md](../agent-strategy.md)）；机制与策略冲突时以策略文档为准。

## 文档总表

| 文档 | 定位 | 何时读 | 关联清单条目 |
|---|---|---|---|
| [science.md](science.md) | 科技树 / 研究 / 尤里卡 | 选研究项目、规划科技路线 | B. 科技 |
| [civics.md](civics.md) | 市政树 / 政策卡 / 政府 | 选市政、换政策卡 | C. 市政 |
| [economy.md](economy.md) | 金币 / 信仰 / 商路 / 资源 / 改良 | 购买决策、商路分配、奢侈品交易 | D. 经济 |
| [military.md](military.md) | 单位 / 战斗机制 / 升级 / 攻城 | 宣战评估、单位升级、防御 | A / E. 安全与军事 |
| [diplomacy.md](diplomacy.md) | 外交 / 同盟 / 世界议会 / 紧急事件 | 外交行动、议会投票 | G. 外交 |
| [culture.md](culture.md) | 旅游 / 伟作 / 奇观 / 文化胜利 | 文化路线、伟人使用 | H. 文化与胜利 |
| [religion.md](religion.md) | 万神殿 / 信仰 / 教条 / 传教士 | 宗教路线、信仰利用 | I. 宗教与信仰 |
| [cities.md](cities.md) | 城市增长 / 生产 / 忠诚 / 区域 / 总督 | 每城生产、增长与忠诚处理 | F. 城市 |
| [barbarians.md](barbarians.md) | 蛮族营地 / 清剿机制 | 每回合威胁评估 | A. 安全 |
| [victory.md](victory.md) | 五种胜利条件与路线 | 路线选择、胜利进度解读 | H. 胜利路线 |
| [great-people.md](great-people.md) | 伟人类型 / 招募 / 加速 | 伟人点数使用、购买伟人 | D / H. 经济与文化 |
| [wonders.md](wonders.md) | 奇观判定 / 加成 / 优先级 | 奇观建造决策 | F / H. 城市与文化 |

## 文档模板（新文档必须遵循）

```markdown
# <领域>（Civ6 知识库）

> **定位**：一句话
> **何时读**：触发条件
> **关联清单条目**：CHECKLIST.md 对应项

## L0 决策速查（10 条内，可直接依据决策）
- …

## L1 核心机制
### <小节>
- …

## L2 数据附录
| 项目 | 数值 / 说明 | 来源 |

## 相关文档
- 横向链接（docs/wiki 内相对路径）
```

## 维护

- 数据有疑问时以 [Fandom](https://civilization.fandom.com/wiki/Civilization_VI) 为准，并在对应条目标注来源链接。
- 术语以灰机中文维基为准。
- 新文档先写 L0；机制细节逐步补 L1 / L2。
