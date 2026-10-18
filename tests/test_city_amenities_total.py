"""Native amenities totals through the complete city-query Lua 5.1 boundary."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_resource_hint_visibility import ResourceConn


class AmenitiesConn(ResourceConn):
    def __init__(self, total, needed):
        super().__init__()
        native = self.lua.globals()
        native.amenities_total = total
        native.amenities_needed = needed
        self.lua.execute('''
amenities_need_reads=0
local growth=city:GetGrowth()
growth.GetAmenities=function() return amenities_total end
growth.GetAmenitiesNeeded=function()
    amenities_need_reads=amenities_need_reads+1
    return amenities_needed
end
growth.SetAmenities=native_request
growth.ChangeAmenities=native_request
UI={RequestPlayerOperation=native_request}
PlayerManager={RequestOperation=native_request}
''')


@pytest.mark.parametrize("total,needed", [(9, 9), (5, 6), (0, 9), (4, 9), (12, 9)])
def test_city_query_reports_native_amenities_total(total, needed):
    conn = AmenitiesConn(total=total, needed=needed)
    cities, _ = asyncio.run(GameState(conn).get_cities())
    assert cities[0].amenities == total
    assert conn.lua.globals().amenities_need_reads == 0
    assert conn.lua.globals().native_requests == 0
