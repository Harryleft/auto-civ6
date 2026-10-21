# Harness 团队分享资料（2026-10-19）

围绕文明 6 长任务 Agent 的讲解演示、三版部署包和总体设计画板。当前本地演示为模型／Harness／游戏三列时序图；演示使用内存数据，不连接真实游戏。

| 资料 | 入口与用途 |
| --- | --- |
| 当前演示 | 本地 `demo/dist/index.html` 与 `demo/PROTOTYPE-CONTEXT.md`；公开副本见[三版部署包](deployments/README.md)。`demo/` 是独立 Git 仓库，保留原分支、提交、托管配置、截图与 QA 材料 |
| 历史版本 | 本地独立演示目录中的 `demo/dist/explainer-v1.html`；三版公开脱敏包与原包指纹见[部署包索引](deployments/README.md) |
| 总体设计图源 | [SVG](diagrams/overall-design/diagram.svg)、[PNG](diagrams/overall-design/diagram.png)、[绘图脚本](diagrams/overall-design/draw.py)；[插入片段](diagrams/overall-design/insert.xml)中的 `@./` 路径相对外层 `civ6/` |
| 画板排版过程 | [最终验收](diagrams/layout-polish/VALIDATION.md)、[原始方案](diagrams/layout-polish/POLISH-PLAN.md)；原始节点、回读和前后截图保留在同目录 |
| 对应游戏成果 | [T233 对局与分享稿](../../../games/benchmark-sumeria-719665280/runs/2026-10-18-t233/README.md) |

直接打开 `demo/dist/index.html` 可查看演示；若需本地 HTTP 访问，在本目录运行 `python3 -m http.server 8787 --bind 127.0.0.1 --directory demo/dist`，访问 `http://127.0.0.1:8787/`。原有辅助脚本以 `demo/` 为工作目录，保留为制作过程记录。

`demo/` 是本地保留的独立仓库，未纳入当前仓库；公开读者从部署包查看演示。部署和浏览器检查结果保存在本地 `demo/qa-verification.json`、`demo/qa-prototype-verification.json`，其中包含各自版本与验证限制。公开包的隐私脱敏不构成重新发布或线上复验。返回[资料索引](../../README.md)。
