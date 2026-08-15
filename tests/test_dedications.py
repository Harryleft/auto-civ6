"""Contracts for the forced current-turn era-dedication flow."""

from civ_mcp.lua.governance import parse_dedications_response
from civ_mcp.narrate import narrate_dedications


def test_required_dedication_is_presented_in_chinese_with_exact_index() -> None:
    status = parse_dedications_response(
        [
            "STATUS|Golden|2|31|20|30|1",
            "CHOICE|4|COMMEMORATION_MONUMENTALITY|Normal bonus|Golden bonus|Dark bonus",
        ]
    )

    text = narrate_dedications(status)

    assert "当前时代：黄金时代 · 中世纪纪元" in text
    assert "本纪元必须选择 1 个时代着力点" in text
    assert "[4] COMMEMORATION_MONUMENTALITY：Golden bonus" in text
    assert "choose_dedication(dedication_index=候选索引)" in text
