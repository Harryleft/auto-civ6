# 外交回应含义修复与 T108 实机复验

修复 `004f4e1` 已通过隔离测试、独立代码审查和同一撤军警告的在线复验。CLI 现在显示当前原生选项的本地化文案、可用说明和外交行动类型。它不根据 `POSITIVE/NEGATIVE` 推测效果，也不替模型选择。当前保存停在 T108，与瑞典未交战；尚未推进承诺之后的回合，长期外交后果未验证。

## 原因与修改

[此前观察](t102-t122-results.md)中，模型把 `NEGATIVE` 当作普通拒绝，实际触发宣战。原生 `DiplomacyStatementSupport.lua` 提供 `Text`、`Tooltip`、`Key`、`DiplomaticActionType`；`DiplomacyActionView.lua` 用 `Locale.Lookup(selection.Text)` 展示按钮。安装数据 `Base/Assets/Gameplay/Data/DiplomacyStatements_Warning.xml` 的 `WARNING_TOO_MANY_TROOPS_NEAR_ME / NONE / AI` 对应选项集将否定回应绑定 `DIPLOACTION_DECLARE_SURPRISE_WAR`。

原实现采集时只保留键，GameState 又把键填为文字，展示层也只列键。修复把同一次原生合法选项中的描述直接传到现有 `DiplomacyChoice`，用十六进制字段保留中文、竖线和换行。缺失或未解析的本地化文案显示含义未知；说明字段是原生选项说明，不声称总是屏幕悬浮提示。提交前仍检查当前参与者、语句序号、合法键和处理器；未知响应只确认原请求，不新增自动回应或重发路径。

## 验证

- 修复前，`tests/test_diplomacy_choice_meaning.py` 的真实 Lua → GameState → 展示链测试因丢失宣战文案而失败；修复后通过。夹具验证原生选项描述传递，游戏警告到宣战的映射另由安装数据和实机确认，不能把夹具说成完整引擎模拟。
- 外交描述、上下文、多会话、响应确认及交易进度共 207 项通过；来约、未决调度及服务共 287 项通过。最后移除未使用字段并收窄测试说明后，相关 22 项复跑通过。编译与 `git diff --check` 通过。
- 独立只读审查未发现必须修复的代码问题；指出的夹具覆盖范围已明确记录。没有改变禁用选项、原语句复核或 pending 的归属。

在线使用唯一 CLI `63e50a6e8923459492a96d59d0a14913`、Runtime `78cff80152c54f5ebd4bec41f38c3d9a`，代码 `004f4e1`。旧 CLI 在所有 pending 为 false 时正常退出，再启动新版本；没有重启绕过门禁。经 CLI 显式加载本轮已确认的 `speed_20261101_continuation_t102`，模型逐回合读取事实并推进到 T108，没有通用策略脚本。这六回合属于修复复验，不作为新的速度样本，也不替换已中断的预登记。

本轮 `evidence/dialogue-meaning-t102/stdout.jsonl` 的关键事件：

| seq | 证据 |
| --- | --- |
| 10 | T102 检查点完整加载；原生总分 287→291 而分类向量不变，此不一致原样保留，不声称全世界状态完全相等。 |
| 249 | 巴西大使馆请求显示肯定、否定和退出的中文文案。 |
| 278 | 瑞典撤军警告显示 `POSITIVE: 我的军队仅仅是路过而已。`、`NEGATIVE: 你的担心是对的（宣战）！`，并显示 `DIPLOACTION_DECLARE_SURPRISE_WAR`。 |
| 282、285、289 | 模型明确选择 POSITIVE；回读“谢谢”及 EXIT 后显式退出，会话关闭。 |
| 292、295 | 无待回应会话；外交回读瑞典为 UNFRIENDLY，可宣战而非 AT WAR，巴西和加拿大仍为 DECLARED_FRIEND。 |
| 299、301 | 新检查点原生保存及文件确认；全部 pending 为 false，`transport_unknown=null`。 |

复验中一次 `great-person list --individual-id` 拼写错误在 CLI 参数层被拒，改为帮助中的 `--individual` 后继续，没有游戏提交。当前结果仅证明这次文案展示、显式和平回应、关闭和保存；不保证所有外交场景的说明都可取得，也不证明最终胜利或两倍推进速度。

## 保存与证据

| 保存名称 | 字节 | SHA-256 |
| --- | ---: | --- |
| `speed_20261101_t108_dialogue_meaning_verified` | 989770 | `300b150def4079f0278e6679353145a557c7a1b44bc8a6834dfc51162215fd35` |
| `speed_20261101_t108_dialogue_war_failure`（保留） | 989964 | `356df0df09720e8600394d76124381e52e787325262bb0d5d953c9c5fb42bda0` |
| `speed_20261101_continuation_t102`（未变） | 973161 | `cec18a7c666f78d452557eaa7acf2e4b47fa10400d819056b825c76e7c0f2a3c` |

`evidence/dialogue-meaning-t102/dialogue-meaning-verification.json` 保存关键回读、源文件指纹和存档重新读取结果。原失败分支、基准和前次速度观察均保留。两倍速度与原生获胜目标仍未完成。
