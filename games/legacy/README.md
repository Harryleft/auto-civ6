# 历史对局索引

以下 12 份报告来自原项目的 LLM 对局，保留逐回合记录与策略分析。表中的状态、模型与原统计沿用历史索引，未在本次整理中重新实测；它们不定义当前 Runtime 的行为。返回[全部对局资料](../README.md)。

| # | Civ / Leader | Turns | Outcome | Key Lesson |
|---|-------------|-------|---------|------------|
| [1](game-001-poland-jadwiga/README.md) | Poland / Jadwiga | 323 | Conceded | First game — protocol reverse-engineering |
| [2](game-002-rome-trajan/README.md) | Rome / Trajan | 221+ | Ongoing | Turn loop development |
| [3](game-003-macedon-alexander/README.md) | Macedon / Alexander | 70 | Eliminated | Early-game survival failures |
| [4](game-004-byzantium-basil/README.md) | Byzantium / Basil II | 182 | Defeated (religion) | Sensorium effect identified |
| [5](game-005-macedon-alexander/README.md) | Macedon / Alexander | 110 | Conceded | Tooling stress-test |
| [6](game-006-india-gandhi/README.md) | India / Gandhi | 245 | Defeated (religion) | Civ identity blindness — zero Holy Sites, 9,599 unspent faith |
| [7](game-007-portugal-joao/README.md) | Portugal / Joao III | 318 | Defeated (diplomatic) | Score-leading but lost — World Congress mismanagement |
| [8](game-008-scythia-tomyris/README.md) | Scythia / Tomyris | 228 | Defeated (culture) | Culture victory blindness |
| [9](game-009-england-victoria/README.md) | England / Victoria | 135 | Conceded | Irrecoverable deficit — 17 turns spent on barbarian camp search |
| [10](game-010-india-chandragupta/README.md) | India / Chandragupta | 431 | Defeated (diplomatic) | Longest game — tech lead for 400+ turns, lost to WC mismanagement |
| [11](game-011-mali-mansa-musa/README.md) | Mali / Mansa Musa | 271 | **Won** (science) | First victory — gold-purchase synergy bypassed -30% production penalty |
| [12](game-012-korea-seondeok/README.md) | Korea / Seondeok | 216 | Conceded | Hallucination of competence — narrative overwriting game state |

**Record: 1 win, 7 defeats, 4 concessions** (Games 1–11: Claude, Game 12: Gemini 2.5 Pro)

See [Cross-Game Analysis](analysis/games-001-004.md) for recurring failure patterns identified across games 1–4.
