"""Shared wire support is exercised in the actual Lua 5.1 query readers."""
import pytest

from test_city_growth import GrowthConn, read as read_growth
from test_city_workforce import WorkforceConn, read as read_workforce
from test_public_reports import execute


@pytest.mark.parametrize("change", [
    "Game.GetCurrentGameTurn=function()return 1.5 end",
    "Game.GetCurrentGameTurn=function()return math.huge end",
    "GameConfiguration.GetValue=function()return 7.5 end",
    "PlayerConfigurations[0].GetCivilizationTypeName=function()return 'CIVILIZATION_?'end",
])
def test_readers_share_strict_world_identity_without_certifying_invalid_facts(change):
    growth = read_growth(GrowthConn(change))
    workforce = read_workforce(WorkforceConn(change))
    report, _ = execute("climate", change)
    for result in (growth, workforce, report):
        assert result["status"] == "unknown"
        assert "world.identity" in result["unknown_fields"]
    assert growth["cities"] is None
    assert workforce["plots"] is None
    assert report["data"] is None


def test_city_queries_do_not_depend_on_unrelated_diplomacy_getters():
    change = "Players[0].GetDiplomacy=function()error('Unrelated diplomacy read')end"
    assert read_growth(GrowthConn(change))["status"] == "ready"
    assert read_workforce(WorkforceConn(change))["status"] == "ready"


def test_lost_final_world_witness_returns_unknown_instead_of_mixed_city_facts():
    change = "city.GetPopulation=function()Game.GetLocalPlayer=function()error('lost world')end;return 12 end"
    result = read_growth(GrowthConn(change))
    assert result["status"] == "unknown" and result["cities"] is None
    assert result["error"] == "World identity incomplete or changed during read"
