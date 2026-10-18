# CI：未决调度测试替身同步

- 失败版本：`fec94d278df1c1685c307d9167c5380ac9f433df`。
- [GitHub Actions](https://github.com/Harryleft/auto-civ6/actions/runs/39128367988)：2035 项通过、12 项失败，均为 `PendingGame` 缺少 `diplomacy_pending`，尚未进入原测试断言。本地对应模块复现 12 失败、2 通过。
- 修复：为测试替身补齐公开未决事实，并将外交未决加入阻止玩法写入、允许只读查询、允许显式故障恢复的既有参数化场景；运行时代码不变。
- 验证：`pytest -q tests/test_pending_dispatch.py tests/test_server.py`，194 项通过（1.82 秒）；`git diff --check` 通过。
- 本地提交后暂不 push；远程 CI 尚未验证修复版本。实机续测另记，现有存档及历史验收文件保持原样。
