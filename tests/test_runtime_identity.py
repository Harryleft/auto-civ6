"""Game-identity parser contracts for the new Runtime adapter path."""

import pytest

from civ_mcp.lua.overview import build_game_identity_query, parse_game_identity_response


def test_identity_query_uses_civilization_type_and_sync_seed() -> None:
    lua = build_game_identity_query()

    assert "GetCivilizationTypeName" in lua
    assert "GAME_SYNC_RANDOM_SEED" in lua
    assert "GAMESEED|" in lua


def test_identity_parser_normalizes_civilization_type_and_requires_seed() -> None:
    assert parse_game_identity_response(["GAMESEED|CIVILIZATION_FRANCE|42"]) == (
        "civilization_france",
        42,
    )
    with pytest.raises(ValueError, match="GAMESEED"):
        parse_game_identity_response(["ERR:GAME_IDENTITY_UNAVAILABLE"])
