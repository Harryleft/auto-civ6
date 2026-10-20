# 工作项

Integration: codex/gold-terminal

| Ticket | Dependencies | Ownership |
| --- | --- | --- |
| [R01](issues/01-observation.md) | none | 只读宗教事实与原生资格 |
| [R02](issues/02-remove-heresy.md) | none | 清除异教动作 |
| [R03](issues/03-theological-combat.md) | none | 和平神学动作 |
| [R04](issues/04-integrated-validation.md) | R01–R03 | 公共流程与文档验收 |

所有实施完成后，经 merger 集成及 Spec／Standards 独立双审，修复发现，再关闭工作项与清理工作树。

原生调用依据：[本机第一方 API 契约](native-contracts.md)。没有在终局世界中试运行；不明确的返回形状必须保守拒绝并留作未来实机核对。

## 完成

R01–R04 均已 resolved，最终代码集成 af85d28。15 个相关套件 1308 passed；独立 Spec 首轮发现 1 项 P1，1443382 补修后原公开复现与 99 项神学测试复核通过。最终 Spec／Standards 均 No material issues found。仅离线验收，未加载或重开游戏；原生动作待后续授权按验收清单验证。
