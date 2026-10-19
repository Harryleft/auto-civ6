# 本轮推进与阻塞记录

起点：`benchmark_gold_tun_1`。起始代码：`b8ddb0268e56c2920e2f3bb3907d2b9cebdac3e9`（main）。

| 编号 | 游戏回合 | 触发／实际表现 | 原因／处理 | 验证 |
| --- | --- | --- | --- | --- |
| B01 | 加载前，目标 T1 | `load_save_from_menu` 返回 `Checkpoint completion and expected identity are unavailable; no load sent.` | checkpoint 仅接受旧基准固定指纹及当前旧轮次的确认存档；补充新基准身份与轮次隔离 | 待实施与实机复验 |

进展：游戏进程和 FireTuner 已启动；通过原生菜单只读获取了新档身份。尚未发送新档加载，尚未推进回合。

规格已写入本轮 changes/gold-baseline 并从本地 tracker 引用。to-spec / tdd 要求确认测试 seam；已向用户提出 checkpoint / GameState / lifecycle 的现有边界及 T1→T2 实机范围，等待回复；尚未编写测试或改动运行代码。原件已逐字节归档到本轮 evidence，SHA-256 一致。
