"""Regression tests for the trade-test narration.

``narrate_test_trade`` called ``_describe_trade_item``, which was never defined
anywhere in the repository. Ruff's undefined-name check found it. The function
is reachable from the ``test_trade`` tool (``game_state.py`` -> the narration
helper), so any trade test that returned proposed or counter items raised
``NameError`` instead of rendering the offer.
"""

from __future__ import annotations

from civ_mcp import lua as lq
from civ_mcp.narrate import narrate_test_trade


def _item(side: str, **overrides) -> lq.TestTradeItem:
    payload = {
        "side": side,
        "item_type": "GOLD",
        "amount": 100,
        "duration": 0,
        "value_id": "YIELD_GOLD",
        "subtype_id": "",
    }
    payload.update(overrides)
    return lq.TestTradeItem(**payload)


def test_renders_proposed_items_for_both_sides():
    result = lq.TestTradeResult(
        other_player_id=3,
        other_civ_name="Rome",
        proposed=[_item("US"), _item("THEM", value_id="YIELD_FAITH", amount=5)],
    )

    text = narrate_test_trade(result)

    assert "Rome" in text
    assert "We offer: Yield Gold x100" in text
    assert "Yield Faith x5" in text


def test_renders_a_counter_offer_that_differs_from_the_proposal():
    result = lq.TestTradeResult(
        other_player_id=3,
        other_civ_name="Rome",
        proposed=[_item("US")],
        counter=[_item("US", amount=150)],
    )

    text = narrate_test_trade(result)

    assert "counter-offer" in text
    assert "We give: Yield Gold x150" in text
    assert "mode='send'" in text


def test_identical_counter_is_reported_as_acceptable():
    result = lq.TestTradeResult(
        other_player_id=3,
        other_civ_name="Rome",
        proposed=[_item("US")],
        counter=[_item("US")],
    )

    assert "ACCEPTABLE" in narrate_test_trade(result)


def test_item_duration_is_rendered_when_present():
    result = lq.TestTradeResult(
        other_player_id=3,
        other_civ_name="Rome",
        proposed=[
            _item(
                "THEM",
                item_type="AGREEMENT",
                amount=1,
                duration=30,
                value_id="",
                subtype_id="DIPLOACTION_OPEN_BORDERS",
            )
        ],
    )

    assert "Diploaction Open Borders for 30 turns" in narrate_test_trade(result)


def test_empty_result_still_renders_a_header():
    result = lq.TestTradeResult(other_player_id=3, other_civ_name="Rome")

    text = narrate_test_trade(result)

    assert text.startswith("Trade test with Rome")
