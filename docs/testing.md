# 测试体系统一管理

本仓库的测试按**类型分类**、**统一配置**、**统一入口**管理。新增测试前先读本文。

## 分类体系

pytest 标记（注册于 `pyproject.toml [tool.pytest.ini_options]`）：

| 标记 | 含义 | 典型文件 | 运行 |
|---|---|---|---|
| （无标记） | 常规单元/行为测试 | `test_belief_engine.py`、`test_governance_*.py` 等 | 默认收集 |
| `property` | hypothesis 属性测试：随机化不变量（重放一致性、幂等、边界） | `test_belief_properties.py` | `pytest -m property` |
| `golden` | Lua 生成黄金契约：哨兵、参数注入、错误路径 | `test_lua_golden.py` | `pytest -m golden` |
| `manual` | 需要真实游戏实例（EnableTuner=1）的冒烟脚本 | `tests/manual/` | **不被收集**，直接运行 `python tests/manual/test_*.py` |
| （变异测试） | mutmut 变异杀死率审计（非 pytest，**可选工具，未声明为依赖**） | 配置在 `[tool.mutmut]` | 见下节 |

`manual` 的实际排除机制是 `tests/manual/conftest.py` 的 `collect_ignore_glob`，
**不是** marker：目前没有任何测试带 `pytest.mark.manual`，该 marker 只是登记在
`pyproject.toml` 中备用。

新增测试文件的约定：

- 属于 `property`/`golden` 类别 → 文件内声明 `pytestmark = pytest.mark.<类别>`；
  其余不加标记（默认类别）。
- 需要事件溯源引擎实例 → 使用 `tests/conftest.py` 的公共 `engine` fixture，
  **不要**在测试文件里重复定义。
- **不得写入用户真实账本**：`tests/conftest.py` 有 autouse 夹具把
  `default_beliefs_directory()` 重定向到 `tmp_path`；即便如此，构造
  `BeliefEngine` 时仍应显式传 `directory=`。
- 引用第三方包前确认它已声明在 `pyproject.toml`（`tests/test_test_dependencies.py`
  会守护；未声明时全新环境上 pytest 收集失败会导致整批测试不执行）。
- 文件命名：`test_<被测模块或机制>.py`。

## 运行入口

```bash
# 全量离线测试（CI 同款）
.venv/bin/python -m pytest tests/ -q

# 静态检查（CI 同款）
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff check src tests --fix        # 可自动修复的部分

# 分类子集
.venv/bin/python -m pytest -m property          # 属性测试
.venv/bin/python -m pytest -m golden            # Lua 黄金测试
.venv/bin/python -m pytest -m "property or golden"

# 单文件 / 单用例
.venv/bin/python -m pytest tests/test_derivation.py -q
.venv/bin/python -m pytest tests/test_derivation.py::test_xxx -q

# 变异测试（可选；mutmut 未在 pyproject 中声明，需先 uv pip install mutmut）
# 约 20 分钟，生成 mutants/ 产物后清理
.venv/bin/python -m mutmut run --max-children 4
rm -rf mutants .mutmut-cache
```

`pytest` 控制台脚本不把 CWD 加入 `sys.path`，因此 `pytest` 与 `python -m pytest`
曾对「`evals` 能否导入」判断不一致。`[tool.pytest.ini_options] pythonpath = ["."]`
已固定该行为，两种入口等价。

## 测试类型速览

| 类型 | 覆盖什么 | 典型断言 |
|---|---|---|
| 单元/行为 | 引擎状态机、治理门禁、部门评估、连接/协议解析 | 状态转移、异常类型与消息、返回值结构 |
| 属性 | 事件溯源不变量（任意操作序列可重放、派生幂等、概率边界） | 重放后状态逐字段一致、version 不变、[0,1] 边界 |
| 黄金 | 发给游戏的 Lua 线协议契约 | 哨兵结尾、参数注入、`_bail` 错误路径、规则守卫 |
| 静态检查 | 未定义名、重复定义、assert/异常误用、语法错误（`ruff` 窄规则集） | 零告警；见 `pyproject.toml [tool.ruff.lint]` |
| 变异 | 测试自身的有效性 | 变异体被杀计数、按函数盲区清单 |

**关于变异测试**：`[tool.mutmut]` 只覆盖 `belief_engine.py` / `derivation.py` /
`ids.py` 三个文件，且 mutmut 不在 `uv.lock` 也不在 CI 中，属**手动可选**工具。
此前文档记录的「杀死率 62%」是一次性快照，没有 artifact 跟踪，随代码演进已失效——
引用前请先实跑，不要把它当作当前指标。

## 维护规则

- **conftest 公共 fixture 优先**：公共夹具放 `tests/conftest.py`；测试文件内只留
  本文件专用的构造辅助函数。
- **不测呈现层措辞**：叙述文本、错误消息的具体用词不纳入断言（行为契约除外）。
- **变异测试后的清理**：验证变异杀死性时临时改源码后必须恢复，并清除
  `__pycache__`（字节码缓存曾导致测试随机翻转）。
- **修 bug 时做反向验证**：补完回归测试后，临时撤掉修复、确认该测试确实转红，
  再恢复。否则无法区分「测试守住了这个行为」与「测试恰好在两种情况下都通过」。
  本次修复的 21 个提交全部按此验证。
- **新增游戏动作**必须配离线回归测试（AGENTS.md 硬规则）。
- **新增 MCP 工具**必须同时归类到 `pipeline._BELIEF_GATED_TOOLS` / `_ROUTINE_TOOLS` /
  `_CONDITIONAL_GATE_TOOLS` 之一（`test_tool_gate_coverage.py` 会守护），并确认
  `AGENTS.md` 中「112 个工具」的数量声明同步（`test_tool_surface.py` 会守护）。
