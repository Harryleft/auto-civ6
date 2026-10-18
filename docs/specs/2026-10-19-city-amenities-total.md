# 城市宜居度总量

状态：complete。集成分支 codex/native-pillage，[工作项](2026-10-19-city-amenities-total-tickets.md)。

## Problem Statement

T206 城市查询将 GetAmenitiesNeeded 与 GetAmenities 相加，虚增宜居度。原版 CitySupport 明确 GetAmenities 是总量，净值为总量减需求。Agent 据此可能错误判断政策代价；本次尚未据错误值换政策。

## Solution

城市公开查询展示原生宜居度总量，保留现有字段、格式及纯读行为，不把需求相加、不改变实际游戏。

## User Stories

1. 作为游戏 Agent，我要读到真实宜居度总量，以免把需求误算成额外供给。
2. 作为游戏 Agent，我要在原生总量为零、低于需求或高于需求时仍获得原始值，以免被静默调整。
3. 作为维护者，我要保留城市模型与 MCP 查询契约，避免单值修复扩展成展示架构改造。
4. 作为维护者，我要用执行 Lua 的回归证明读取语义和纯读，以免测试只复述源码。

## Implementation Decisions

城市查询 amTotal 直接读取原生 GetAmenities；删除仅供错误加法的需求读取。保留 CityInfo.amenities 与当前 Amenities 展示，不新增净值字段、缓存或动作；不改旧历史验收数据。原版 CitySupport 的 AmenitiesNum、AmenitiesNetAmount 定义作为 API 依据。

## Testing Decisions

沿用已确认执行 Lua5.1、GameState／FakeConn 的既有边界，不连接游戏。以完整生成查询执行并解析验证不同总量与需求组合，不用字符串替换关键受测逻辑，不产生写入；只运行新增或直接相关模块、编译与差分检查。

## Out of Scope

宜居度规则模拟、幸福阈值猜测、模型扩展、旧记录改写、策略自动化、全量测试、合 main、push。

## Further Notes

现场1753–1755，1754临时原生读取中 surplus 标签写错；实际数值为总量，不是净值。以原版 Base/Assets/UI/CitySupport.lua526–539为准；Capital需要9、总9，旧城市展示18。同轮保存后更新客户端即可，不重新加载游戏。
