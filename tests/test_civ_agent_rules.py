"""M09：Rule Search 只返回规则事实。

测试用一次性的临时 wiki 目录，不依赖真实 ``docs/wiki`` 的内容细节；另有少量
用例对着真实知识库跑，用来锁住"目录能被正确解析"这件事。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from civ_agent.rules import RuleHit, known_topics, search_rules, wiki_dir
from civ_agent.rules.search import WIKI_DIR_ENV


def _write_wiki(base: Path) -> None:
    (base / "cities.md").write_text(
        "\n".join(
            [
                "# 城市（Cities）",
                "",
                "> 定位：城市机制",
                "",
                "## L0 决策速查",
                "",
                "1. 尽早铺城，7~10 城是长期复利来源。",
                "",
                "## L1 核心机制",
                "",
                "### 忠诚度机制",
                "",
                "每回合忠诚度按压力修正；归 0 变自由城市。",
                "",
                "## L2 数据附录",
                "",
                "| 区域 | 成本 |",
                "| --- | --- |",
                "| 学院 | 54 |",
                "",
                "## 来源",
                "",
                "Fandom",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (base / "barbarians.md").write_text(
        "\n".join(
            [
                "# 蛮族（Barbarians）",
                "",
                "## L0 决策速查",
                "",
                "1. 营地会持续刷兵，尽早清剿。",
                "",
                "## L1 核心机制",
                "",
                "营地 7 格内不刷新蛮族单位。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (base / "README.md").write_text("# 索引\n\n这里是索引，不应被检索到。\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 目录解析
# ---------------------------------------------------------------------------


def test_wiki_dir_prefers_explicit_base(tmp_path: Path) -> None:
    assert wiki_dir(tmp_path) == tmp_path


def test_wiki_dir_uses_environment_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WIKI_DIR_ENV, str(tmp_path / "rules"))

    assert wiki_dir() == tmp_path / "rules"


def test_known_topics_covers_the_plan_directions() -> None:
    """方案 §11 列出的检索方向必须都在册。"""

    topics = known_topics()
    for expected in (
        "cities",
        "economy",
        "military",
        "science",
        "culture",
        "diplomacy",
        "religion",
        "victory",
        "wonders",
        "great_people",
    ):
        assert expected in topics


# ---------------------------------------------------------------------------
# 检索行为
# ---------------------------------------------------------------------------


def test_search_rules_finds_the_matching_section(tmp_path: Path) -> None:
    _write_wiki(tmp_path)

    hits = search_rules("忠诚度 压力", directory=tmp_path)

    assert hits
    assert hits[0].doc == "cities.md"
    assert hits[0].section == "忠诚度机制"
    assert hits[0].topic == "cities"
    assert "自由城市" in hits[0].excerpt


def test_search_rules_records_the_level_so_callers_can_read_l0_first(tmp_path: Path) -> None:
    _write_wiki(tmp_path)

    hits = search_rules("铺城 复利", directory=tmp_path)

    levels = {hit.level for hit in hits}
    assert 0 in levels


def test_search_rules_maps_chinese_alias_to_topic(tmp_path: Path) -> None:
    """查询"蛮族"应命中 barbarians 文档，即使正文没写"蛮族"两字。"""

    _write_wiki(tmp_path)

    hits = search_rules("蛮族", directory=tmp_path)

    assert hits
    assert all(hit.doc == "barbarians.md" for hit in hits)


def test_search_rules_prefers_more_matches_over_level(tmp_path: Path) -> None:
    """相关度优先于层级：命中更多的小节排前，即使它不是 L0。"""

    _write_wiki(tmp_path)

    hits = search_rules("忠诚度", directory=tmp_path)

    assert hits[0].section == "忠诚度机制"
    assert hits[0].level == 1


def test_search_rules_breaks_ties_by_level(tmp_path: Path) -> None:
    (tmp_path / "science.md").write_text(
        "\n".join(
            [
                "# 科技",
                "",
                "## L2 数据附录",
                "",
                "尤里卡。",
                "",
                "## L0 决策速查",
                "",
                "尤里卡。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    hits = search_rules("尤里卡", directory=tmp_path)

    assert [hit.level for hit in hits] == [0, 2]


def test_search_rules_skips_the_index_readme(tmp_path: Path) -> None:
    _write_wiki(tmp_path)

    hits = search_rules("索引", directory=tmp_path)

    assert hits == ()


def test_search_rules_respects_limit(tmp_path: Path) -> None:
    _write_wiki(tmp_path)

    assert len(search_rules("城市", limit=1, directory=tmp_path)) == 1


def test_search_rules_rejects_invalid_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        search_rules("城市", limit=0, directory=tmp_path)


def test_search_rules_returns_empty_for_blank_query(tmp_path: Path) -> None:
    _write_wiki(tmp_path)

    assert search_rules("   ", directory=tmp_path) == ()


def test_search_rules_returns_empty_for_missing_directory(tmp_path: Path) -> None:
    assert search_rules("城市", directory=tmp_path / "nope") == ()


def test_search_rules_returns_empty_when_nothing_matches(tmp_path: Path) -> None:
    _write_wiki(tmp_path)

    assert search_rules("量子隧穿", directory=tmp_path) == ()


def test_search_rules_does_not_recommend_strategy(tmp_path: Path) -> None:
    """方案 §11：Rule Search 只返回规则事实，不做自动战略推荐。"""

    _write_wiki(tmp_path)

    hits = search_rules("营地 清剿", directory=tmp_path)

    assert hits
    # 返回的是原文摘录与来源，没有额外的"建议/推荐"字段。
    assert isinstance(hits[0], RuleHit)
    assert set(RuleHit.__slots__) == {"topic", "doc", "section", "level", "excerpt", "score"}


# ---------------------------------------------------------------------------
# 真实知识库
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["城市 忠诚", "蛮族营地", "宗教 万神殿", "科技 尤里卡", "奇观"],
)
def test_real_wiki_is_searchable(query: str) -> None:
    hits = search_rules(query, limit=3)

    assert hits, f"真实知识库未能命中：{query}"
    for hit in hits:
        assert hit.doc.endswith(".md")
        assert hit.excerpt.strip()
