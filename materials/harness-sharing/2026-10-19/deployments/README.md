# 历史部署包

三份压缩包为历史部署的公开脱敏副本。2026-10-21 移除了私有分享链接、文件所有者和 macOS 附属元数据；原包保留本地。包内含 `dist/`，按以下顺序保留版本演进；不是新的线上发布结果。

| 部署包 | 内容 | 对应入口 |
| --- | --- | --- |
| [explainer-v1.tar.gz](explainer-v1.tar.gz) | 初版静态说明页 | 当前演示仓库的 `dist/explainer-v1.html` |
| [interactive-prototype.tar.gz](interactive-prototype.tar.gz) | 分步交互原型 | 保留当时的 `dist/index.html` 与原型页面 |
| [sequence-prototype.tar.gz](sequence-prototype.tar.gz) | 模型／Harness／游戏三列时序图 | 原包入口与本地演示相同；公开副本的私有分享入口已替换为项目对局资料 |

[manifest.json](manifest.json) 的 `sha256` 和 `index_html_sha256` 对应当前公开副本，`original_*` 字段保留原包指纹与当时的一致性记录。查看历史内容时解包到新的临时目录，避免覆盖当前演示。返回[分享资料](../README.md)。
