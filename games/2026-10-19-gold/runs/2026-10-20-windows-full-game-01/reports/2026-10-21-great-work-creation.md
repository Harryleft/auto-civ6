# 作家、艺术家创作巨作修复验收

## 结论

已修复文化伟人被普通行动次数检查错误拒绝的问题。Windows 实机通过公开 `unit_action(action="activate")` 确认托尔斯泰两部著作、葛饰北斋三件美术巨作，最后一次创作后两名原伟人均被原生消耗。五件作品、城市槽位和创作者已独立回读。

本次从同轮已确认的第 239 回合检查点恢复，净推进到第 241 回合，作为独立修复验收；保留原第 243 回合外交胜利记录及所有原检查点。最终另存 `greatwork_20261021_t241_final`。精简证据见 [great-work-evidence.json](great-work-evidence.json)，原始调用在本轮本地 `evidence/great-work-followup.jsonl`。

## 原因与原生对照

`unit_effects.py` 原先将所有非先知伟人都视为消耗普通 `GetActionCharges()` 的激活，提交前要求次数大于零，确认时要求次数减一或最后一次消耗单位。

原生 `GreatPeople_Writers.xml`、`GreatPeople_Artists.xml` 将普通 `ActionCharges` 设为零。实机托尔斯泰站在伊辛剧院广场 `(38,26)` 时，普通次数为零、原生 `CanStartCommand` 为真，旧公开入口仍返回 `UNIT_EFFECT_REFUSED|NO_CHARGES`。

文化伟人仍使用原生 `UNITCOMMAND_ACTIVATE_GREAT_PERSON`，不是新增的“创建巨作”命令。原生 `UnitPanel.lua` 使用命令哈希提交；本机该命令哈希为 `374670040`，而 `UnitCommandTypes.ACTIVATE_GREAT_PERSON` 不存在，诊断时不能用这个不存在的常量判资格。

先在第 242 回合做了一次明确的原生命令诊断，确认事件参数：

- `UnitGreatPersonActivated(owner, unitID, classID, individualID)`。
- `GreatWorkCreated(owner, creatorUnitID, cityX, cityY, buildingID, greatWorkIndex)`。
- 作品实际进入空槽，`Game.GetGreatWorkDataFromIndex` 给出作品类型、创作者和创作回合；普通行动次数仍为零。

诊断回执对应请求 13、14；该诊断产生的作品与随后恢复第 239 回合进行的公开 MCP 验收分开记录，不重复计数。原生事件及槽位 API 的读取依据为游戏安装目录中的 `GreatWorkShowcase.lua`、`GreatWorksOverview.lua` 和 `Civ6Common.lua`。

## 修复与确认边界

- 在原激活入口按真实单位类型及伟人类别识别作家、艺术家、音乐家，使用原生资格，不再要求普通次数大于零。静态作品集合只绑定作品身份，不当作剩余次数。
- 提交前读取本方城市实际槽位和已有作品；执行前再次核对单位、个体、类别、位置、世界及原生资格。
- 只有原单位的激活事件、原单位的巨作创建事件、匹配个体的新作品进入提交前空槽三者一致，才返回 `GREAT_WORK_CREATED`。
- 作品实例必须是新实例；作品类型、创作者、创作回合及城市身份均须匹配。最后一次创作还要求原单位移除事件，以及原生单位查询和所属单位集合均确认缺席。
- 暂时不可读的字段保持未知；已知身份变化永久失效，不能在恢复旧值后借用后来事件确认。回应丢失时只确认原请求，不重发创作。原生命令内部抛错且未返回时保留未知门禁。
- 普通伟人与先知的既有确认规则保持不变。成功结果不推算文化、旅游收益或剩余作品次数。

## Windows 实机

基准身份为苏美尔／吉尔伽美什，种子 `-1896272000`，沿用完整对局轮次 `44138f888bef42dabb57f55030244f98`。

| 回合 | 原单位 | 公开 MCP 确认的作品 | 实例 | 目标城市／建筑／槽位 | 请求 |
| --- | --- | --- | --- | --- | --- |
| 239 | 葛饰北斋 `3276816` | `GREATWORK_HOKUSAI_1` | 98 | 乌鲁克 `65536`／23／2 | 108 |
| 239 | 托尔斯泰 `3735569` | `GREATWORK_TOLSTOY_1`《战争与和平》 | 99 | 伊辛 `262147`／14／0 | 111 |
| 240 | 托尔斯泰 `3735569` | `GREATWORK_TOLSTOY_2`《安娜·卡列尼娜》 | 100 | 伊辛 `262147`／14／1 | 128 |
| 240 | 葛饰北斋 `3276816` | `GREATWORK_HOKUSAI_2` | 101 | 西巴尔 `131073`／23／0 | 133 |
| 241 | 葛饰北斋 `3276816` | `GREATWORK_HOKUSAI_3` | 102 | 西巴尔 `131073`／23／1 | 148 |

请求 149 独立回读五个作品实例、创作者与回合，以及两名原伟人在两处查询中均缺席。最终补充未知字段回归后，用最终生成的确认 Lua 再核对原艺术家最后一次创作的原生回执，请求 161 返回 `UNIT_EFFECT_CONFIRMED`，没有再次提交创作。

另以刘易斯验证最终代码的原生资格拒绝：移动在 `(35,26)` 耗尽行动力时，公开入口返回 `UNIT_EFFECT_REFUSED|CANNOT_ACTIVATE`，没有把普通次数为零误报为故障或声称创作。随后显式取消通往 `(34,26)` 的测试路径，保留位置，未替她继续创作。

推进期间只处理实际阻塞的生产、使者和单位待办。第 239 回合原生议会进入 Stage2，明确对宗教紧急议案投一票反对，并确认原推进请求完成；第 240 回合原地驻防、派遣已读取的三名使者后进入 241。未增加资源、生成单位或改写巨作数据。

最终存档 `greatwork_20261021_t241_final.Civ6Save`：1120432 字节，SHA-256 `8411d822875738a77941b02d5dc3f8689b3b4967dbc0252f9506e95f03d878bb`。正常关闭独立 SDK/MCP 会话后，游戏仍运行，停在第 241 回合。原始存档、日志和 JUnit 文件保留本地，不提交大型证据。

## 离线检查

```powershell
.scratch\review-venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_great_work_creation.py tests/test_unit_effect_confirmation.py tests/test_spread_core_followers.py tests/test_religion_defense_workflow.py tests/test_action_confirmation.py tests/test_server.py --tb=short --junitxml=.scratch/great-work-tests-final.xml
```

结果：**443 passed，28.15 秒**，无跳过。其中新增 39 项，覆盖作家／艺术家／音乐家、最后一次消耗、下一次显式创作、错误事件／作品／城市、缺少原生能力、回应丢失、已知身份变化失效与未知字段恢复。现有单位效果、宗教防御、动作确认和 MCP 协议回归同时通过。

编译及 `git diff --check` 通过。本次未重复运行全库测试；此前 5376 项通过记录属于上一轮提交复核。仅 Windows 做了本次实机验证，音乐家由实际 Lua 离线用例覆盖，没有音乐家实机案例，也未重测 Mac 实机。
