# 离线调试 CLI 第一期验收

2026-10-24 在 macOS、Python 3.12.13 下验证 `civ6` 的 doctor、知识验证、六个领域测试及日志查询。未连接 FireTuner，未启动、加载或操作真实游戏。命令与 JSON 契约见[使用指南](../cli.md)。

## 实现范围

- `civ6 doctor` 核对解释器与实际检出、依赖、知识包及真实 stdio MCP 初始化／工具发现，不调用游戏工具。
- `civ6 verify knowledge` 比较真实 CLI 与 stdio MCP 的知识搜索及三个读取层级，核对静态适用范围、知识包未变与子进程清理。
- `civ6 test` 提供显式领域选择和受限 pytest 参数透传，隔离环境中的额外选项与插件加载；保留实际 pytest 退出码，不把仅收集或无命中解释成用例通过。
- `civ6 logs list/show/timeline` 从现有动作 JSONL 读取有限快照，支持筛选、排序、坏行统计与源指纹，查询不改写原文件。
- 业务结果使用统一 JSON，原始过程保存在独立目录；技能 helper 复用项目包中的实现。

## 离线验证

最终 CLI 回归命令 `uv run --locked --no-sync pytest -q tests/test_dev_cli.py`：19 项通过。覆盖真实 CLI／MCP 查询、坏知识包、过滤与乱序日志、残缺行和非法时间、非 JSON 数字、参数错误、额外测试路径与插件拒绝、环境透传隔离、无命中、证据防覆盖及超时输出保留。

安装后的控制台入口逐项实际运行六个领域，结果如下；这些结果来自运行，不是收集数量。

| 命令 | 通过用例 |
| --- | ---: |
| `civ6 test knowledge` | 27 |
| `civ6 test actions` | 96 |
| `civ6 test turns` | 152 |
| `civ6 test checkpoints` | 109 |
| `civ6 test diplomacy` | 232 |
| `civ6 test connection` | 56 |
| 合计 | 672 |

控制台 doctor、知识验证、领域发现、三个日志命令及旧技能 helper 均退出 0。日志配方使用独立样本文件，并确认查询前后 SHA-256 一致。另运行 `python -m compileall -q src tests`、`git diff --check`，通过技能元数据及文档本地引用检查。

`uv build --wheel` 成功；核对 wheel 的 `civ6 = civ_mcp.cli:main` 入口，以及 CLI 和 `devtools/` 模块字节与受测源码一致。

## 证据

最终控制台验收索引位于本地忽略目录 `.scratch/civ6-acceptance/7c35e42c6fa3441781519ef75b52590a/index.json`。各子目录保留统一摘要、解释器和源码指纹、子进程命令及输出；知识配方保留初始化、工具发现、四次知识调用和原始回应。所有证据在清理后仍存在，未提交原始本机路径或日志。

## 未验证范围

未验证 Windows 上的实际 CLI 执行、真实游戏状态／动作／推进／恢复，以及未来的前台调试会话。领域测试使用现有隔离夹具，不能据此推断实机通过。CLI 依赖源码检出中的测试与知识包；单独安装 wheel 不提供这些开发资料。
