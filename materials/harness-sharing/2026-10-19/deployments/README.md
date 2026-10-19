# 历史部署包

三份压缩包均为原文件，仅改名。包内含 `dist/`，按以下顺序保留版本演进；不是新的发布结果。

| 部署包 | 内容 | 对应入口 |
| --- | --- | --- |
| [explainer-v1.tar.gz](explainer-v1.tar.gz) | 初版静态说明页 | 当前演示仓库的 `dist/explainer-v1.html` |
| [interactive-prototype.tar.gz](interactive-prototype.tar.gz) | 分步交互原型 | 保留当时的 `dist/index.html` 与原型页面 |
| [sequence-prototype.tar.gz](sequence-prototype.tar.gz) | 模型／Harness／游戏三列时序图 | 包内 `dist/index.html` 与当前本地演示入口逐字节相同 |

[manifest.json](manifest.json) 保存各包与入口 HTML 的 SHA-256。包内托管元数据和 macOS 附属文件保持原样；查看历史内容时解包到新的临时目录，避免覆盖当前演示。返回[分享资料](../README.md)。
