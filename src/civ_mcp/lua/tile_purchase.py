"""Native tile purchase receipt: exact city, target plot, and gold consumption."""
import json

from civ_mcp.lua._helpers import SENTINEL


_CONTEXT = '''
local function number(value)
 assert(type(value)=="number" and value==value and value~=math.huge and value~=-math.huge,"NUMBER_UNAVAILABLE")
 return value
end
local function cityFor(owner,id)
 local city=CityManager.GetCity(owner,id%65536)
 assert(city and city:GetOwner()==owner and city:GetID()==id,"CITY_ID_CHANGED")
 return city
end
local function sameScope(r)
 local ok,same=pcall(function()
  return Game.GetLocalPlayer()==r.owner and Game.GetCurrentGameTurn()==r.turn
   and PlayerConfigurations[r.owner]:GetCivilizationTypeName()==r.civ
   and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==r.seed
 end)
 if not ok or not same then r.invalid=true end
 return not r.invalid
end
'''


def build_purchase_tile(city_id: int, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local token={json.dumps(token)}
local ok,r=pcall(function()
 assert(__civmcpTilePurchase==nil or type(__civmcpTilePurchase)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local city=cityFor(owner,{city_id})
 local plot=Map.GetPlot({x},{y})
 assert(plot and plot:GetX()=={x} and plot:GetY()=={y} and plot:GetOwner()==-1,"PLOT_UNAVAILABLE_OR_OWNED")
 local targets=CityManager.GetCommandTargets(city,CityCommandTypes.PURCHASE,{{[CityCommandTypes.PARAM_PLOT_PURCHASE]=1}})
 local indices=targets and targets[CityCommandResults.PLOTS]
 assert(type(indices)=="table","PURCHASABLE_PLOTS_UNAVAILABLE")
 local eligible=false
 for _,index in pairs(indices) do if index==plot:GetIndex() then eligible=true end end
 assert(eligible,"PLOT_NOT_PURCHASABLE")
 -- Native PlotInfo.OnClickPurchasePlot uses this exact command and parameter set.
 local params={{[CityCommandTypes.PARAM_PLOT_PURCHASE]=1,[CityCommandTypes.PARAM_X]={x},[CityCommandTypes.PARAM_Y]={y}}}
 assert(CityManager.CanStartCommand(city,CityCommandTypes.PURCHASE,params)==true,"CANNOT_PURCHASE")
 local cost=number(city:GetGold():GetPlotPurchaseCost({x},{y}))
 local balance=number(Players[owner]:GetTreasury():GetGoldBalance())
 assert(cost>0 and balance>=cost,"INSUFFICIENT_GOLD_OR_COST_UNAVAILABLE")
 assert(type(CityManager.RequestCommand)=="function" and CityCommandTypes.PURCHASE~=nil
  and CityCommandTypes.PARAM_X~=nil and CityCommandTypes.PARAM_Y~=nil,"COMMAND_UNAVAILABLE")
 return {{token=token,owner=owner,city={city_id},cityX=number(city:GetX()),cityY=number(city:GetY()),
  x={x},y={y},index=number(plot:GetIndex()),cost=cost,balance=balance,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then
 print("ERR:TILE_PURCHASE_REFUSED|"..tostring(r));print("{SENTINEL}");return
end
if not (__civmcpTilePurchase and __civmcpTilePurchase.token==token) then
 __civmcpTilePurchase=r
 local params={{[CityCommandTypes.PARAM_PLOT_PURCHASE]=1,[CityCommandTypes.PARAM_X]={x},[CityCommandTypes.PARAM_Y]={y}}}
 r.entered=true
 CityManager.RequestCommand(cityFor(r.owner,r.city),CityCommandTypes.PURCHASE,params)
 r.returned=true
end
print("OK:TILE_PURCHASE_REQUESTED|city:{city_id}|({x},{y})")
print("{SENTINEL}")
'''


def build_tile_purchase_query(city_id: int, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpTilePurchase
local reason="RECEIPT_MISSING_OR_MISMATCHED"
local ok,balance=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.city=={city_id} and r.x=={x} and r.y=={y})
 reason="SCOPE_CHANGED";assert(sameScope(r))
 reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 reason="CITY_CHANGED"
 local city=cityFor(r.owner,r.city)
 assert(city:GetX()==r.cityX and city:GetY()==r.cityY)
 reason="PLOT_OWNERSHIP_UNCONFIRMED"
 local plot=Map.GetPlot(r.x,r.y)
 assert(plot and plot:GetIndex()==r.index and plot:GetOwner()==r.owner)
 local purchaseCity=Cities.GetPlotPurchaseCity(plot)
 assert(purchaseCity and purchaseCity:GetID()==r.city and purchaseCity:GetOwner()==r.owner
  and purchaseCity:GetX()==r.cityX and purchaseCity:GetY()==r.cityY)
 reason="GOLD_DELTA_UNCONFIRMED"
 local balance=number(Players[r.owner]:GetTreasury():GetGoldBalance())
 assert(balance==r.balance-r.cost)
 return balance
end)
if ok then
 print("TILE_PURCHASE_CONFIRMED|"..r.token.."|"..r.city.."|"..r.x.."|"..r.y.."|cost:"..r.cost.."|gold:"..r.balance.."->"..balance)
else print("TILE_PURCHASE_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''


def parse_tile_purchase_confirmation(lines: list[str], token: str, city_id: int, x: int, y: int) -> str | None:
    rows = [row.split("|") for row in lines if row.startswith("TILE_PURCHASE_CONFIRMED|")]
    if len(rows) == 1 and len(rows[0]) == 7:
        row = rows[0]
        if row[1:5] == [token, str(city_id), str(x), str(y)] and row[5].startswith("cost:") and row[6].startswith("gold:"):
            return f"TILE_PURCHASED|({x},{y})|city:{city_id}|{row[5]}|{row[6]}"
    return None
