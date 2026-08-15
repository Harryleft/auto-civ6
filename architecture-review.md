# 架构审查：项目臃肿度与模块化分析（修订版）

> 客观数据快照（基于仓库实际扫描；本版修正第一版的两处误判，并补充 roadmap 对齐结论）

## 0. 数据快照

| 区域 | 文件数 | 行数 | 备注 |
|---|---|---|---|
| src/civ_mcp（MCP 适配层） | 49 | 31,099 | 含 lua/ 子包 17 文件 |
| src/civ6_belief_engine（域包） | 23 | 8,711 | belief + governance + graph |
| tests | 38 | 9,664 | 与生产代码比约 1:4，合理 |
| evals（civbench 基准） | ~10 | 2,511 | inspect-ai 独立子系统 |
| docs | 33 | 12,624 | 其中 devlog 12 篇约 8,500 行 |
| web（Next.js + Convex 站点） | ~60 | ~11,000 | node_modules 919MB |
| scripts | 23 个脚本 | — | 5 个一次性工具无任何引用 |

仓库 1.3GB（web/node_modules 919MB），462 次提交。最大单文件：server.py 5,745 行/146 函数/104 工具 · belief_engine.py 2,671 行 · game_launcher.py 2,473 行 · narrate.py 2,238 行。

## 1. 最重要的修正：臃肿是进行中的迁移，不是无规划的膨胀

graph_plan/README.md 明确写明了演进顺序：

- 阶段四：『最后才拆 server.py 和 end_turn.py』 —— server.py 瘦身被路线图刻意安排在最后一步，因为图路径替换旧路径后 server.py 里的 belief/governance 适配层（约 1,600 行）会自然消亡。
- 阶段三（进行中）：DTO 边界倒置 —— Military 已移除对 civ_mcp.lua.models 的直接导入；剩余 3 处（governance 的 models/snapshot、departments/diplomacy）正是阶段三/四未完的活。
- 验收判据第一条：Department 不依赖 MCP/Lua 类型。

结论：第一版报告建议『现在就拆 server.py』与项目自身路线图冲突，予以撤销。在迁移中途拆 adapter 会与图工程双线打架，还可能在游戏运行链路上引入风险。正确的做法是不抢跑，让阶段三/四自然完成 server.py 的瘦身。

## 2. 真正可以立即动手的问题（不与 roadmap 冲突）

### 2.1 docs 里 8,500 行是游戏战报，不是项目文档
devlog/ 12 篇（game_001 单篇 3,525 行）是过程记录。web 站点不消费它们（已验证 web/content、web/src、web/convex 无 devlog 引用），仅 docs/README.md 有一处链接。可安全移出 docs/（如 docs/devlog → devlog/），docs 实际只剩约 3,000 行运维/架构文档。

### 2.2 scripts/ 有 5 个零引用的死脚本
generate_sas_token.py、scrape_wiki_images.py、publish_hf_dataset.py、split_game_log.py、menu_audit.py 在全仓库（代码/文档/CI）无任何引用，可移入 scripts/_archive/ 或删除。

### 2.3 兼容 shim 只有测试在用
civ_mcp/governance/*（5 个转发文件）与 civ_mcp/belief_engine.py 在生产代码中零引用，仅 tests/ 的 6 个文件（含 test_product_package_boundary.py 的 Legacy 断言）在用。这是 AGENTS.md 承认的兼容债务；清理 = 删 shim + 改 6 个测试文件。值得做，但需要接受放弃旧兼容面的决定。

### 2.4 CI 验证缺口（不是臃肿，但更实质）
.github/workflows/ci.yml 的 Python job 只跑 py_compile src/civ_mcp/server.py，不跑测试。对一个 38 个测试文件的项目，这是比结构更值得先解决的问题。

## 3. 修正后的误判清单（第一版报告的错误）

| 原结论 | 事实 | 修正 |
|---|---|---|
| dist/ 提交了构建产物 | dist/.gitignore 含通配忽略，git 未跟踪 | 不是问题，删除该建议 |
| P0 拆分 server.py | 路线图阶段四明确最后才拆 | 撤销；改为支持阶段三/四 |
| P0 倒置 3 处 import | 阶段三进行中，Military 已示范 | 不抢跑；这是进行中的活 |

## 4. 建议（按不与 roadmap 冲突排序）

| 级别 | 动作 | 说明 |
|---|---|---|
| 立即 | devlog 移出 docs/，更新 docs/README.md 链接 | 零风险，感知体积减半 |
| 立即 | 死脚本移入 scripts/_archive/ | 零风险 |
| 短期 | CI 增加 pytest 运行 | 提升验证信心，比任何重构都值 |
| 短期 | shim 清理（删 shim + 改 6 个测试） | 需要你确认放弃旧兼容面 |
| 中期 | 跟随 graph_plan 阶段三/四：DTO 边界、server.py 自然瘦身 | 已在计划内，不重复造轮子 |
| 产品决策 | web/、evals/ 是否继续维护：活跃则保留单仓明确分区，停更则归档 | 不是技术问题 |

## 5. 执行记录（2025-08-15）

已按上文执行：devlog 移至根目录（链接已更新）、4 个死脚本归档至 scripts/_archive/（publish_hf_dataset.py 保留——它是 HF 数据集流水线的入口）、CI 补 pytest（344 测试）、兼容 shim 删除并改写 7 个测试（新增依赖方向守护测试）。全部通过离线回归。未动：server.py 拆分与 DTO 倒置（按 graph_plan 阶段三/四节奏推进）。

## 6. server.py 拆分执行记录（2026-08-15，同日第二次）

在拿到 server.py 内部结构图（banner 分节即天然拆分线；belief/governance 段 3200–5219 行是连续整删单元）后，用户裁决推翻第 1 节对"现在拆"的撤回：拆分为 move-only 单提交。依据：纯移动 + tokenize 级调用点限定不改变行为，104 个工具的 name/description/inputSchema 快照逐字节一致；belief/governance 适配层独立成 `server/tools/belief.py` 后，阶段四删除从"在 5939 行文件里做外科手术"变成"删一个模块加一行再导出"。

- 结构：`server/assembly.py`（lifespan/入口/mcp 对象）、`server/pipeline.py`（`_logged` 管道 + 门禁表 + 预检三件套，未来 ActionPipeline 边界）、`server/tools/{queries,actions,end_turn,belief,world,system}.py`。
- 契约保持：`civ-mcp` 入口、`from civ_mcp.server import mcp` 的注册副作用、工具 schema 逐字节不变；测试 patch 目标迁移至 `civ_mcp.server.pipeline` / `...assembly`。
- graph_plan 阶段四对应条目已同步改写。

## 6. 一句话结论

项目的臃肿感一半来自真实但已被计划的迁移债（server.py、DTO 边界，路线图正在处理），一半来自感知混杂（devlog 战报混进文档、死脚本、卫星仓库同住一仓）。真正现在该做的只有卫生类工作：devlog 搬家、死脚本归档、CI 补测试。代码结构的瘦身请交给 graph_plan 阶段四，别抢跑。