# 用户授权回滚：掠夺接口与科研收益实测

## 结论

CLI已有 `unit pillage ID`，通过原生资格检查后只提交一次当前格掠夺。T80回滚实测已确认学院内一层建筑受损，军事工程学同一回合从61%升至81%，预计剩余2回合降为1回合。随后同回合议和已确认，19个单位仍在，披甲战士保持44HP。文化收益、连续建筑/区域多层掠夺、精确科技点数未实测或未量化；不能以此宣布战略质量、整局胜利或两倍速度通过。

用户明确要求检查掠夺并允许适当回滚。本次仅加载同一轮次已登记检查点，保留T125、原始基准及所有失败结果；不是连续不回档对局。T126–T146速度对照尚未开始，已标NOT_RUN。

## T115–T117失败分支

原T125路径验收检查点先核验SHA-256，pending全false后，在原CLI加载 `speed_20261101_followup_t115_war`，身份苏美尔、seed719665280、T115完整确认，另存起点。

剑客1310731于T116到达瑞典学院(12,19)，剩余1MP。`unit pillage 1310731 --execute` 返回 `PILLAGE_REFUSED|CANNOT_PILLAGE`，未提交，pending无遗留。随后等待下一回合的策略错误：T117原生通知明确剑客被瑞典市中心击杀；石弩2097157、弩手1507341被卡洛林步兵击杀，商人1769484被掠夺。该分支无成功掠夺，不算质量通过。失败现场另存 `speed_20261101_pillage_t117_failed`。

根因属于模型风险判断：低估敌城射程及道路上敌军接近能力，把到达设施与安全等待混为一谈。新的路径预览没有承诺单位存活。并非通过反复发送动作或清除pending得到成功。

原始证据：`evidence/t125-path-native/stdout.jsonl`，reload command66/result69，T116拒绝及保存截至308；`evidence/pillage-rollback-t115/stdout.jsonl` 新owner command11/result14确认T117及损失，command24保存失败。

## T80同回合验证

历史记录证实，T80披甲战士1638414已经位于敌方学院(12,19)，44HP、2/2MP。加载保留的 `speed_20261101_midgame_t080` 后重新读身份、单位、城市和pending，另存 `speed_20261101_pillage_t080_start`。未将历史单位状态直接当作当前事实。

新owner `bb1d2faba85f4fee9ad4b8ac9fdb52fe`，Runtime `b3cd9ba272b9492ba2b35394dbda2a7c`，加载源码 `d8cbdd6`：

| 证据序号 | 入口与结果 |
| --- | --- |
| 30–33 | reload确认T80、同文明和种子 |
| 48–50 | research show：军事工程学61%，2回合；行政部门46% |
| 55–58 | unit pillage 1638414 --execute；一次tool调用，confirmed；`damaged:building:4` |
| 59–61 | research show：军事工程学81%，1回合；行政部门仍46% |
| 62–66 | 单位0/2MP、44HP、原位置；全部pending=false |
| 67–75 | diplomacy peace 2原生ACCEPTED，外交会话及pending清空 |
| 79–81 | 19单位仍在；披甲战士44HP，被和平规则移至(11,17) |
| 82–87 | 独立保存T80掠夺后和平检查点，pending全false |

前后研究读取之间只执行保存和一次掠夺，没有推进回合、切换研究或其他获取科研的操作。因此可确认即时研究进度上升。现有CLI输出整数百分比，不反推精确科技点数。回执建筑索引4尚未用当前原生类型映射命名，不擅称其为图书馆或大学。该分支净推进0回合；议和后避免把44HP单位留在敌方射程中再承受一次敌方回合。

另核对到旧日志并非完全未用掠夺：`optimized-relay/stdout.jsonl` sequence1400–1403曾确认战车720903在(12,20)掠夺改良设施。此次遗漏主要是学院等区域收益机会，不能将历史全部概括成从未掠夺。

## 修复、审查与验证

独立审查发现 `pillageState` 对建筑列表直接ipairs，稀疏索引或重复ID可能漏读损坏层。`d8cbdd6`现验证键为1..N连续整数，ID唯一且为有限非负整数。异常预检/原子复查零提交，异常确认保留原pending；空表及ID0保持合法。未增加自动掠夺、选择目标或重试机制。

worker先复现旧代码失败，再完成82项掠夺测试；独立reviewer复核无新增实质问题。主代理运行 `uv run --locked pytest -q tests/test_pillage.py tests/test_pending_dispatch.py tests/test_action_confirmation.py`，191 passed。compileall及diff检查通过。旧CLI在无pending时正常quit并退出0，新owner随后启动；T80成功实测使用新源码，没有重叠控制器。畸形列表仅隔离测试覆盖，未向游戏注入异常。

## 保存与后续

所有文件字节数及SHA-256见 `evidence/pillage-rollback-t115/checkpoints.json`。当前检查点 `speed_20261101_pillage_t080_peace`：889812字节，SHA-256 `6cb26d42fd1f259776eeb1d73e69c2e21619d7febf3f3d390f6448cebacd0467`。原T125路径检查点SHA仍为 `2b5aef5cf043b5a55d9c790e8aa880446c930d4441afa5deab385e6ab70edf22`。

后续每回合由模型把掠夺纳入当前候选，结合科研/文化瓶颈、设施层、行动力、敌方火力与撤退路径决定，不建立固定策略脚本。当前主目标是从保存的和平现场恢复发展、核对获胜路线；整体两倍与原生胜利目标仍未完成。新的速度试验需另行登记实际起点，不能混用此次回滚前后样本。
