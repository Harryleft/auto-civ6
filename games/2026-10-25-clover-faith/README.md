# 四叶草神级：苏美尔信仰与海军对局

本轮从 `fixtures/saves/benchmark_gold_tun_1.Civ6Save` 的 T1 开始，苏美尔／吉尔伽美什，神级，seed `-1896272000`。2026-10-27 从已确认的 T173 暂停检查点续局，T237 原生返回 `GAME OVER: VICTORY — 苏美尔, VICTORY_CONQUEST`。

本轮原 CLI 证据 ID 为 `322c1b851bd84d0e90bef56e91af6898`；修复攻城入口后，通过正常退出并重启 CLI 延续本轮，证据 ID 为 `8ff2cb2d3d1d485cac97486d5b603713`。不以这些 CLI 运行 ID 代替检查点轮次登记。

[T162 攻城修复与实机验收](runs/322c1b85/changes/2026-10-26-city-attack.md)。原始基准和既有 `clover_faith_20261025_t162`、`clover_faith_20261025_t162_hold` 检查点保留。

[T173—T237 续局与征服胜利](runs/322c1b85/reports/2026-10-27-t173-t237-victory.md)记录首都占领、三项 CLI 修复、技术恢复、终局前检查点，以及最后一次攻击仍保留未决确认的原因。

[终局占城确认修复规格](runs/322c1b85/changes/2026-10-27-terminal-capture/spec.md)：用户确认采用隔离领域／Lua 回归及实体查询零重发验证；原终局、旧 CLI owner、未决请求和存档保留。
