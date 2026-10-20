# 测试替身与执行耗时优化验收（2026-10-20）

受测代码：`2328e689488b55e75d562e54df386c71be812bc8`。按[测试效益审计](2026-10-20-test-efficiency-audit.md)修复过期替身并优化构建与等待后，**全量 5,235 项通过，耗时 88.95 秒；相比基线 150.84 秒减少 61.89 秒（41.0%）**。原始审计及其失败记录保持原样。

## 改动与行为保护

| 对象 | 改动 | 保留的验证边界 |
| --- | --- | --- |
| 开放边界跨入口测试 | 使用已有 `OpenBordersTradeConn`，提供绑定工作交易的原生摘要，移除旧子类型覆盖 | GameState／MCP 两个入口、未知后只确认、目标不匹配门禁、无重复提交及原生动作顺序 |
| 5 个只读知识模块、23 项用例 | 新增会话级 `committed_knowledge_root`，完整快照构建从 10 次合并为 1 次 | 每次查询仍启动真实 CLI；规则、来源、版本、证据层级及正式指纹检查继续执行 |
| 确认测试与 3 个无关慢实例 | 显式使用测试夹具，将普通确认预算设为 0.1 秒、轮询间隔设为 0.001 秒 | 多次观察、旧／中间／最终状态、未决门禁、原请求保留和提交次数；专门的截止时间测试继续覆盖自己的预算 |

共享构建仅用于 diplomacy、resources、combat、economy、victory 五个只读知识模块。变更输入、损坏产物、迁移路径、捕获与其他隔离测试仍使用各自的目录。确认夹具仅供 `test_action_confirmation.py`、`test_action_lua_execution.py` 及两个 server 测试函数显式使用，测试结束后由 `monkeypatch` 恢复。

正式 `src/` 代码、游戏确认默认预算和原生资格守卫未修改。源码形状断言仍保留，本次未进行覆盖迁移。

## 全量对比

两次均使用项目 `.venv/bin/python`、同一只读计时插件和相同 pytest 参数。pytest 总耗时与 setup／call／teardown 累计阶段耗时分别统计。

| 指标 | 基线 `34887fc` | 优化后 `2328e68` |
| --- | ---: | ---: |
| 执行实例 | 5,235 | 5,235 |
| 通过／失败 | 5,233／2 | 5,235／0 |
| pytest 总耗时 | 150.84 秒 | 88.95 秒 |
| 累计阶段耗时 | 146.02 秒 | 86.63 秒 |

| 同一批用例的累计阶段耗时 | 实例 | 基线秒 | 优化后秒 |
| --- | ---: | ---: | ---: |
| 五个只读知识模块 | 23 | 63.282 | 27.439 |
| 动作确认模块 | 71 | 9.271 | 0.170 |
| 三个无关 5 秒等待实例 | 3 | 15.031 | 0.308 |

共享构建的准备耗时计入首次消费夹具的用例，因此按模块组比较。CLI 查询各自加载和核验数据的成本仍存在。耗时来自同一本地环境的一次基线与一次优化后全量运行，未控制系统负载及缓存，41.0% 是本次实测降幅。

## 验证与证据

- 针对性回归：10 个相关测试文件，**495 项通过，32.90 秒**，包含交易原生摘要与拒绝条件回归。
- 全量回归：**5,235 项通过，88.95 秒**，收集清单逐项对比，无新增、删除或跳过实例。
- 断言保留：修改文件中的 197 个测试函数、572 条 `assert` 经 AST 比较，在统一夹具变量名后完全一致；查询入口和行为断言继续执行。
- `.venv/bin/python -m compileall -q src tests` 与 `git diff --check` 通过。
- 未加载审计期间的 `optimize_probe`、`adapter_probe` 或其他替换插件；本次结果来自提交的测试代码。没有操作真实游戏。

[机器可读摘要](2026-10-20-test-efficiency-optimization/summary.json)保留受测提交、完整实例清单摘要、各组耗时和断言对比结果；[逐文件耗时](2026-10-20-test-efficiency-optimization/files.csv)覆盖 101 个实际执行测试文件。原始日志、JUnit、收集清单、三阶段计时和比较脚本保存在本机忽略目录 `.local/test-audit/2026-10-20-optimization/`，原始基线仍在 `.local/test-audit/2026-10-20/`。

复现全量计时：

```bash
mkdir -p .local/test-audit/2026-10-20-optimization/recheck
CIV6_AUDIT_OUTPUT="$PWD/.local/test-audit/2026-10-20-optimization/recheck" \
PYTHONPATH="$PWD/.local/test-audit/2026-10-20-optimization" \
.venv/bin/python -m pytest tests -q -p civ6_audit_plugin -p no:cacheprovider \
  --durations=30 \
  --junitxml=.local/test-audit/2026-10-20-optimization/recheck/results.xml
```

计时插件为本机证据文件；其他检出环境可移除 `-p civ6_audit_plugin`、两个环境变量及 JUnit 输出参数，直接运行离线全量测试。
