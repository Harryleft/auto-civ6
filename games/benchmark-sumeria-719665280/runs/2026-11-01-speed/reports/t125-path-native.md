# T125 路径预览事实修复

`4a90ca5` 仅修复只读路径查询及呈现：采用 `GetMoveToPathEx` 的路径和原生逐节点回合标记，独立显示移动力、有效移动力、进入 ZOC、busy 和目标资格；路径异常保留 unknown。实际移动仍只提交一次目的地及 flags，展示路线不绑定到执行。旧四个位置字段及旧 PATH 行保持可读取。

## 原问题与审查

旧进程 T123 的 `unit path 2949141 12 14` 预览经过 `(12,15)`，原生移动却经过 `(11,15)` 并进入控制区。随后查询称相邻目标本回合可达，动作回读为 ZOC 下原地完成。旧查询用 `GetMoveToPath` 与可达格集合计数，既未使用实际移动预检的 Ex 数据，也未读取 ZOC／有效移动力。首次分叉的具体引擎原因未由日志证实；不把改用 Ex 当作路线一致性保证。

独立审查发现路径地块/坐标 getter 抛错未降级的问题，修复后复核通过。动作提交、确认和 pending 门禁未修改，没有自动分段或重发。

## 离线验证

- 相关路径、移动目标、Lua 动作、server 回归：369 passed。
- 在上述运行后补充原生 getter 抛错降级，最终路径定向回归：44 passed。
- compileall 与 `git diff --check` 通过。
- 隔离 Lua 用例验证零提交、旧格式、原生 turn labels、未知字段、ZOC 与正移动力并存、坏路径与异常降级。独立审查未发现遗留实质问题。

## 实机验证

旧 CLI 在 T125 保存且所有 pending=false 后正常 `quit`（旧 evidence sequence 2720，unresolved=false），relay 进程正常退出。然后才创建新唯一 owner，没有重启游戏或读档。新证据为 `../evidence/t125-path-native/`。

- CLI run：`865375514a314db6829ba7b00027db3f`；Runtime session：`41b310595eed429692fe165bf2ea072f`。
- 新鲜身份回读：苏美尔、种子 719665280、T125；新旧控制器没有并行持有游戏。
- 当前回合预览：火枪手 `2949141` 从 `(12,13)` 到 `(13,12)`，原生 labels `1,1`，moves/effective=3、ZOC=false、busy=false。随后模型明确提交这一步，回读确认到达，再显式治疗。
- 多回合预览：建造者 `3145735` 到 `(23,13)`，labels `1,1,1,2,3`，报告两次未来回合；未执行这项查询目标。
- 占位目标未返回路径时，仍如实显示独立的 `target_can_start=true`，没有将它解释为可位移。
- 将弩手升级为野战加农炮后，新 ID `3604497` 的 moves/effective 均为 0；预览目标 `(13,11)` 的 labels 为 `1,2,2`，不再宣称当前回合可达。原生目标资格仍为 true，说明该事实必须独立于移动力解释。没有提交这项目标移动。
- 净推进 0 回合；sequence 55 所有 pending=false、transport_unknown=null。ZOC=true 的新实现仍只有离线覆盖，本次没有为制造该状态重新开战或恢复旧档。

新增检查点 `speed_20261101_followup_t125_path_verified`：1048728 字节；原生保存已确认，文件 SHA-256 为 `2b5aef5cf043b5a55d9c790e8aa880446c930d4441afa5deab385e6ab70edf22`，独立文件核对记录为同目录 `t125-path-checkpoint.json`。T125 议和存档与全部旧检查点保留。

本次证明查询语义和既有动作入口在该现场可用，不新增两倍速度或胜利结论。整局目标仍未完成。
