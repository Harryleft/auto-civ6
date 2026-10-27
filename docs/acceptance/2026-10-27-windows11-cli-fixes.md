# Windows 11 CLI 兼容性修复复验（2026-10-27）

`c63c8143ffce69a7b4f0d75af95a9aef1a9a7eec` 已在用户 Windows 机器通过离线复验：700 项定向测试中，697 通过、0 失败、3 项按平台条件跳过；[首轮验收](2026-10-27-windows11-cli.md)的 21 项失败全部消除。新的中文检出目录安装成功，UU 终端连续两次 Ctrl+C 后保持同一 owner，并继续完成中文查询和显式退出。

## 环境与版本

| 项目 | 本轮范围 |
| --- | --- |
| 操作通道 | 同一 Windows 11 机器、UU `session2` 终端及文件传输 |
| 系统与解释器 | Windows 11 `10.0.26200` x64、CPython `3.12.2` AMD64、locale `cp936` |
| 受测版本 | 上述固定提交；通过 git bundle 传输并核对 SHA-256，未 push |
| ASCII 检出 | `%USERPROFILE%\Documents\Something\civ6-cli-win11-fix-20261027`，新建，doctor PASS |
| 中文检出 | 同一父目录下的 `civ6 CLI 修复验收 20261027`，新建；完整测试集在此执行 |
| 安装与编码 | 两份检出均 `uv sync --locked`；主回归 `PYTHONUTF8=1`，中文检出的 doctor 另用 `PYTHONUTF8=0` |

原检出、首轮中文安装失败目录、原始测试输出及游戏现场保留。本轮没有连接游戏、加载存档或推进回合。

## 修复与对应证据

| 首轮问题 | 修复 | Windows 复验 |
| --- | --- | --- |
| 中文目录安装后无法导入包 | Hatch 使用 `dev-mode-exact` 和运行依赖 `editables`：`.pth` 只含 ASCII 的 import，中文路径放在 UTF-8 Python 模块中 | 新建中文目录安装；`.pth` 为 `import _editable_impl_civ6_mcp`，ASCII 检查为 true；帮助、doctor、真实 stdio 及全部回归通过 |
| Ctrl+C 后空读被当成退出 | 信号处理记录中断；输入线程仅对终端的中断空读重读，保留原输入流及 MCP 进程 | UU 实际按键两次后均可读取 pending，PID／session ID 不变，中文查询成功；最后显式 `quit` 退出 |
| 证据路径键使用反斜杠，14 项失败 | `context.source_sha256` 相对路径统一 `as_posix()` | 原 14 项证据契约测试通过，没有放宽断言 |
| relay 依赖 AF_UNIX，2 项失败 | Windows 使用仅绑定 `127.0.0.1` 的随机端口、本轮独占 endpoint 文件和随机令牌；保留 Unix socket 路径 | 原 2 项测试通过；新增 2 个真实离线 relay 会话验证自动／强制 TCP、错误令牌拒绝、重复请求不重放、中文命令及退出清理 |
| 热座控制器无条件导入 fcntl，1 项失败 | POSIX 保留 flock；Windows 用 msvcrt 首字节锁；相关控制文件显式 UTF-8 | 原控制器 stdio 初始化及预演通过；补测真实文件描述符之间的锁竞争与释放、20 项控制器夹具测试 |
| tasklist 本地化文字解码失败，1 项失败 | 系统命令按 bytes 捕获，仅匹配 ASCII PID 字段 | 原有界超时、子进程回收及 PID 回读测试通过 |
| Windows 强制结束夹具时缺少收尾回执，2 项失败 | 在真实提交和查询边界原子保存见证，不依赖最终 disconnect | 两个超时／原意图保留测试通过；单次提交和 owner 断言保留 |
| fake units 不接受 on_confirmation，1 项失败 | 替身签名与生产回调接口一致 | 原确认测试通过；本地 macOS 对照也通过 |

relay 令牌仅用于本轮本地 IPC，不进入请求回执和客户端输出。发生未知传输结果时仍保留原 owner、拒绝后续变更，不自动重建或重放。历史 `hotseat_endurance.py` 的 macOS 专用例外未移植；控制器预演通过不等于 Windows 原生热座游戏通过。

## 回归结果

| 测试入口 | 通过 | 失败 | 跳过 | pytest 耗时 |
| --- | ---: | ---: | ---: | ---: |
| `civ6 test debug --timeout 300` | 349 | 0 | 3 | 88.43 秒 |
| `civ6 test knowledge --timeout 300` | 27 | 0 | 0 | 23.01 秒 |
| `civ6 test connection --timeout 300` | 65 | 0 | 0 | 1.18 秒 |
| `civ6 test checkpoints --timeout 300` | 113 | 0 | 0 | 3.73 秒 |
| 8 个定向测试文件 | 143 | 0 | 0 | 113.78 秒 |
| 合计 | 697 | 0 | 3 | — |

定向文件沿用首轮 6 个文件，再加入 `tests/test_file_lock.py` 和 `tests/test_hotseat_agent.py`。相较首轮增加 24 项：中断输入回归 1 项、真实 relay 2 项、文件锁 1 项、控制器夹具 20 项。原 6 文件中失败的 16 项已按 JUnit 的 classname／name 逐项匹配，全部转为通过；另外 5 项位于 debug／knowledge 领域，原测试均保留。

3 项跳过仍是 POSIX 信号传递和伪终端专用测试。Windows 新增中断空读测试通过；本轮另用 UU 真实控制台验证空闲中断，不将跳过算作通过。

7 项 CLI 功能检查全部通过：JSON help、默认编码 doctor、`verify knowledge`、领域列表、`debug --offline`、中文日志过滤、无效超时按预期退出 2。`debug --offline` 的帮助、中文搜索／读取、动作 dry-run、pending 和 quit 逐事件检查无阻塞或未知结果。

本地 macOS 的两组定向测试共 326 通过、0 失败（297 项／70.40 秒，29 项／5.77 秒）；doctor、真实 stdio 知识验证、compileall 和 diff 空白检查通过。

## UU 终端 Ctrl+C

在中文检出的全新 `debug --offline` 中执行：

```text
pending
<Ctrl+C>
pending
<Ctrl+C>
pending
knowledge search 写作 --topic technologies --limit 1
quit
```

会话就绪及 3 次 pending 共 4 次身份回读均为 PID `14536`、session ID `9e9ab1c5ccde47409b6b2c2eff46a8a7`。JSONL 中命令序列与上面一致，只有显式 quit 之后的一次 `session_closing`；最终 `status=PASS`、`mcp_session_closed=true`、`context_lost=false`。本轮为无真实未决请求的离线会话，Windows 游戏 RPC 执行中的中断仍为 `NOT_RUN`。

## 证据与保全

机器可读结果见[修复摘要](2026-10-27-windows11-cli-fixes.json)。本机原始证据在 `.scratch/windows11-fix-20261027/`：

- `win11-fix-20261027/summary.json`、`results.json`：12 组检查全部 PASS；含命令、退出码与耗时。
- `civ6/<run_id>/pytest.json`、`win11-fix-20261027/windows-cli-junit.xml`：4 个领域和 8 个定向文件的实际执行结果。
- `environment.json`、`ascii-doctor.json`、`fingerprints.json`：中文 editable 映射、ASCII 对照和锁文件／知识指纹。路径均相对于 `win11-fix-20261027/`。
- `interactive-console/`、`interactive-verification.json`、`console-transcript.txt`：实际按键后的事件、身份、显式退出及终端记录。
- `relay-real/`：两个实际 relay owner 的请求与 CLI 回执。pytest 的 `current` 别名带来一份重复副本，按解析后的真实目录去重为两个会话。
- `transfer-manifest.json`：162 个文件全部通过大小及 SHA-256 回读，缺失 0、差异 0；清单 SHA-256 为 `75ed8ff947bb32fc9a81b3ba2f70274da8e9e4892e10b7ed8e32fa80b8055ccc`。远端封存副本保留在首轮检出的 `.scratch/fix-retest-results/`。

受测中文检出为干净的 `c63c814`，修复版本的 `uv.lock`、知识清单及知识快照在测试前后指纹一致。真实游戏、FireTuner、存档恢复、原生热座交接及 Windows Codex Hook 信任／自动触发未运行；Hook 脚本测试已通过。
