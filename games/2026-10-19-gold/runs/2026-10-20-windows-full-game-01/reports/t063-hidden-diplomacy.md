# 第 63 回合无界面外交会话

状态：代码与回归通过，原回合实机验收完成。

原生巴西会话 `2` 的参与者为 `FromPlayer=2, ToPlayer=0`，`IsSessionIDOpen=true`。同时原生 `DiplomacyActionView` 隐藏、选中玩家 `-1`，观察器没有语句记录，`HasQueuedSession=false`。第 62 回合议会前检查点以相同三项着力点、政策及投票重放，也在第 63 回合重现，故不能简单归因为第 63 回合读档。

常规回应需要实际原生选项；这里没有可核验选项，因此没有伪造 EXIT、默认接受或合成语句事件。补充公开 `close_diplomacy_session(other_player_id)`，由调用方明确取消一个指定参与者的现有会话。单次使用原生 `DiplomacyManager.CloseSession`；确认需要原始令牌、世界/回合/参与者、请求返回、精确 `DiplomacySessionClosed` 事件、`IsSessionIDOpen=false`，以及该双方之间没有开放会话。它不自动运行，也不能绕过既有未知操作。

首次实机请求凭据 `0e2bec2d1a924ab1ad474dfe5e4f6199` 独立只读确认 entered/returned/closed 均 true、原会话 open=false。原生 `FindOpenSessionID` 此时不返回值，赋值后为 nil；确认器最初只接受负数，已补齐本机实际返回形状。此兼容仅在精确事件和明确关闭读数均满足时允许 nil；getter 抛错、事件缺失或替换会话仍保持未知。

修复后从本轮第 63 回合检查点恢复，相同正常动作再现巴西无界面会话。公开关闭入口返回 `DIPLOMACY_SESSION_CLOSED|player:2|session:2`，只读会话查询确认为空，原生结束回合确认 `63 -> 64`。真正第 64 回合存档已确认 758291 字节，覆盖此前误命名为 t064、实际为第 63 回合的文件；随后正常推进到第 65、66 回合。第 64 回合末新出现的巴西正常对话提供原生 EXIT，已按常规入口关闭，无需使用恢复入口。

公开 MCP/GameState/实际 Lua 套件新增 26 项，覆盖无界面、nil 与负数缺席、未知读取、丢回应、延迟、身份变化锁存、替换会话和门禁。与多会话、上下文、交易阶段、结束回合、pending 分派联合运行 **343 passed in 10.33s**。先前 341 项是修补 nil 分支前的版本，不相加统计。没有提交或推送。
