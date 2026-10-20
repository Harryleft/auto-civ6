# 宗教防御工具链实机验收通过

2026-10-20；集成分支 `codex/gold-terminal`，受测生产代码 `ec9824a`。MCP 已重载，使用原局已确认检查点，未加载原始T1、未建立新轮次或新开游戏。轮次 `e8b4d6a62dca49bbb256e8264beb18f2`，苏美尔／吉尔伽美什，seed `-1896272000`。

## 实际通过

| 场景 | 回合／请求 | 原生效果及后续确认 |
| --- | --- | --- |
| 宗教观测与中文 | T139#23、T144#159 | 乌鲁克文本正确；InGame／GameCore／最终范围一致；本方城市多数、信徒、累计压力和流入压力分列；和平可见外方宗教单位可读取；议会当前active有／无及剩余回合可读，无法唯一映射的effect/target保持unknown。 |
| 宗教层移动 | T139#30–32 | Apostle3145737进入埃利都35,22；原生ANY层回读确认与建造者、野战加农炮同格；warfalse；保存通过。 |
| 和平神学战 | T140#47、#49–50 | 本方HP100→86，瑞典目标5570570 HP54→0；精确CombatVisEnd、targetRemoved和双原生查找缺席；无宣战；保存通过。 |
| 审判前提与购买 | T141#78–81 | 同一使徒消耗、原生审判false→true；正常支付100信仰买审判官3276804，初始0MP／3charges，等待下回合。 |
| 普通清除 | T142#104–106 | 城131073异教8累计压力406.8515625→101.7109375，次数3→2；原生Started、独立身份／压力回读、保存均通过。 |
| 连续清除 | T143#133–134 | 异教压力121.109375→30.27734375，次数2→1；确认T142→143及保存。 |
| 最后一次清除 | T144#154–160 | 异教压力49.67578125→12.41796875，次数1→0；Started／Removed均true，精确审判官两条查找均缺席；后续建造者skip和保存成功；pending=false。 |

三次清除均在原精确自有城心执行，原生真实变化为75%异教压力减少；工具并不以固定比例或转化作为成功条件。相邻／离城等其他原生返回形状未在本次实机覆盖。查询的可见单位扫描如返回partial会原样保留，不能据此推断全图没有宗教威胁。T144仍有可见瑞典使徒，验收通过不表示对局已获胜。

## 阻塞、修复与审查

| 阻塞 | 发生与修复 | 实机复验 |
| --- | --- | --- |
| Q01 UTF-8损坏 | T141#7／T139#14；真实Lua locale下RED；2910c63显式ASCII控制过滤 | #23通过。 |
| M01 宗教／平民堆叠误拒绝 | T139#18；3bfbc4c依据第一方宗教层；规范审查发现隐藏占用泄露，4b87cc7补严格可见性 | #30–32通过。 |
| C01 完成战斗仍未知 | T140#38；a2be8bf使未匹配的通用Started仅为补充，保留请求返回、精确战斗、效果与身份范围 | 旧#38仍记录unknown；显式#43恢复战前确认检查点后，新#47通过。 |
| P01 原生清除只返回描述 | T142#96–97；96aaff7绑定精确自有城心；规格审查发现缺失常量会伪装无坐标，d506698验证原生整数枚举 | #104／133／154通过。 |

每项均按[to-spec规格与工作项](../../../../../docs/specs/2026-10-20-religion-native-fixes/spec.md)→隔离worker实施→merger集成→独立Spec／Standards审查→父代理实机复验。两轴最终均无实质问题；两个审查发现均由原实施者补修。Q01相关248、M01最终259、C01相关226、P01最终256是不同阶段的受影响检查，不相加为通过总数。最终集成版本10个直接相关套件 **710 passed in8.70s**，compileall／diff检查通过，83个本地链接目标存在。4个实施worktree已清理。

巴西停止传教要求已在#59–64明确POSITIVE、核对“谢谢”／EXIT、退出并确认无待办。加拿大要求撤间谍、瑞典对话、议会、新时代着力点和总督等推进待办也均按当前原生选项处理。没有用未决重发或直接设置游戏数值推进。

## 证据与停止位置

- [机器可读结果与10个新检查点指纹](results.json)；[逐步记录](README.md)；[原证据保护清单](preserved-evidence.json)。原始工具日志、终局截图、终局JSON、进度报告与三个原检查点共7项，结束时SHA-256及大小全部不变。
- 新原始日志独立为 `evidence/tool-calls.jsonl`，请求#1–160连续且唯一；C01只读诊断为 `evidence/t140-combat-receipt-diagnostic.json`。新存档副本也保留在独立evidence目录，均不覆盖原件。
- 停在原局验收分支T144，`gold_val_20261020_t144_religion_acceptance`，940037bytes，SHA-256 `437eb0ef7d8c590ec90dcde6ee1c6e55a9bfc6cae963d66e10345ea5b9e2cc04`；同轮次原生完成、身份和文件确认通过。MCP客户端干净关闭，当前无未决请求。
- 原始比赛结论仍为 **T141瑞典宗教胜利、苏美尔失败**。验收分支改变了T139后的行动，仅用于能力验证；不替换原终局或复盘因果证据。

复验命令（隔离离线适配器，不连接游戏）：

```bash
uv run --locked pytest -q tests/test_religion_defense_workflow.py tests/test_religion_defense.py tests/test_remove_heresy.py tests/test_religious_attack.py tests/test_religious_movement.py tests/test_launch_inquisition.py tests/test_condemn_heretic.py tests/test_unit_effect_confirmation.py tests/test_pending_dispatch.py tests/test_action_scope.py
uv run --locked python -m compileall -q src tests
git diff --check
```
