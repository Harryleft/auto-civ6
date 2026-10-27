# 01: 以精确终局证据确认最后一次近战占领

**What to build:** 同一次近战占领已取得目标城市、触发本方原生征服胜利但攻击者留在相邻格时，原请求获得准确的终局占领完成结果；普通占领及证据不完整情形保留现有严格条件。

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] 公开领域攻击入口的实景固定夹具先重现 `CAPTURE_POSITION_UNCONFIRMED`，再通过修复转绿。
- [x] 确认要求精确原战斗见证、匹配范围、目标归本方、匹配获胜队伍及征服胜利类型、有效相邻攻击者；原生操作始终仅一次。
- [x] 负例保持未决，普通异步进城、伤害与攻击者死亡确认不退化，已锁存身份冲突不能由终局覆盖。
- [x] 完成结果区分终局占领与实际进城，不伪造单位位置；定向回归通过。

## Comments

按规格使用已约定的公开领域入口与现有 Lua 执行夹具；只做隔离测试，不操作真实游戏，不修改原日志或存档。


### 实施结果（待主代理合并闭票）

- 代码提交 `8684155`；已正常合并集成分支最新规格提交，未 push。
- 沿公开 `GameState.attack_unit` 与真实 Lua 回归先重现 `CAPTURE_POSITION_UNCONFIRMED`，修复后终局相邻海格占领输出增加 `completion:terminal_conquest`，普通结果格式不变。
- 绑定提交前原生无获胜队伍、原精确战斗见证、范围、城市归属与有效相邻攻击者，并复核本方队伍的原生征服结果。原生结果／身份冲突锁存，部分 getter 未知不能遮蔽已读有效冲突。
- 原攻城回归 38 项，新增 41 项，共 79 项；相关 5 文件最终 `306 passed, 1 deselected in 7.58s`。排除项为基点已有旧错误文案断言，已在未改攻城代码的主仓库复现并交主代理处理。
- 编译检查与 `git diff --check` 通过；没有访问真实游戏、存档或原终局证据。
- 证据日志均在 `/private/tmp/civ6-terminal-capture-01-*.log`：`red`、`race-red/green`、`partial-red/green`、`boundary-red/green`、`health-red/green`、`targeted-final`、`preexisting-failure`。
- 测试使用主仓库已有锁定 venv、`--no-sync` 和指向任务 worktree 的 `PYTHONPATH`，避免受限 worktree 新建环境；测试仍导入本票源码。

### 合并验收

主代理确认代码 `8684155` 已由合并子代理快进合入集成分支，HEAD `2b059a3`；工作树干净、diff 检查通过。任务 02 已解阻，独立查询确认与集成验收继续由下一张票完成。
