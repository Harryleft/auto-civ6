# B12-01 Confirm native alert HOLD

Status: resolved
Dependencies: none
Spec: ../spec.md

Implement scoped native HOLD acceptance for fresh alert and tests at existing approved boundaries. No live game access by worker. Record red→green and affected validation. Parent owns recovery and live T57→T58 verification. Resolve after live validation and independent Standards/Spec review.


## Offline implementation

仅在既有 fresh-task、entered／returned／exact started 全部成立且 ready=false 的确认分支，为 alert 接纳可读整数 ACTIVITY_HOLD。沿用精确 owner／完整单位 ID／文明／seed／turn／token 和原生事件约束，不修改提交、no-op、其他任务或游戏状态；输出仍为任务开始，未承诺持续警戒。

TDD：T57 弓箭手786437、seed-1896272000、ALERT Hash-448283625／Index54、HOLD75236491、moves0 的公共 MCP／原生 Lua 用例先红后绿；普通回复与丢回复后的确认都只提交一次。补充缺／错事件直到精确事件出现、ready=true／未知、活动未知／错误、HOLD 枚举缺失／格式错误、世界／回合／完整 ID／owner／token 改变均保持未知且不重发。no-op 回读 HOLD 不确认，初始 unrelated HOLD 不被视为 ALREADY_ALERT；heal／sleep 不接纳 HOLD，automate 保留原有仅依据精确接纳事件的契约。旧“任意错误活动”测试改用999，以保留负向含义；heal／sleep 的 HOLD 拒绝另有定向验证。

最终离线：unit_tasks／unit_readiness／action_scope／server 共372 passed；compileall 与 diff --check 通过。工作者未连接游戏。独立 Standards／Spec 审查与同轮 T57→T58 实机验证尚由父代理完成，本票尚未 resolved。

Validation: 857865c／集成26c3467；372相关测试、compileall/diff-check通过，Spec与Standards独立审查无实质发现。#1020恢复T56，#1024–1041忠实重放，#1042保存T57警戒前检查点；#1044原生警戒开始确认成功。随后#1046推进被战车新本地待办阻塞，#1049单位处理被pending门禁拒绝，#1051确认保留原请求；T58联验转B13继续，未声称已推进。
