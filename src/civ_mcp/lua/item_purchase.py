"""Purchase receipts bind the original city, currency and newly created product."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE
from civ_mcp.lua.purchase_facts import LUA_PURCHASE_FACTS


_CONTEXT = _LUA_ACTION_SCOPE + LUA_PURCHASE_FACTS + '''
local function number(value)
 assert(type(value)=="number" and value==value and value~=math.huge and value~=-math.huge,"NUMBER_UNAVAILABLE")
 return value
end
local function cityFor(owner,id)
 local city=CityManager.GetCity(owner,id%65536)
 assert(city and city:GetID()==id and city:GetOwner()==owner,"CITY_ID_CHANGED")
 return city
end
local function balanceFor(owner,currency)
 if currency=="YIELD_FAITH" then return number(Players[owner]:GetReligion():GetFaithBalance()) end
 return number(Players[owner]:GetTreasury():GetGoldBalance())
end
local function sameCity(city,r)
 return city and city:GetID()==r.city and city:GetOwner()==r.owner
  and city:GetX()==r.x and city:GetY()==r.y
end
'''


def build_purchase_item(city_id: int, item_type: str, item_name: str, yield_type: str, token: str) -> str:
    return _CONTEXT + f'''
local token,kind,name,currency={json.dumps(token)},{json.dumps(item_type)},{json.dumps(item_name)},{json.dumps(yield_type)}
local ok,r=pcall(function()
 assert(__civmcpItemPurchase==nil or type(__civmcpItemPurchase)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local city=cityFor(owner,{city_id})
 local item=(kind=="UNIT" and GameInfo.Units or GameInfo.Buildings)[name]
 assert(item,"ITEM_UNAVAILABLE")
 local before={{}}
 if kind=="UNIT" then
  for _,unit in Players[owner]:GetUnits():Members() do before[number(unit:GetID())]=true end
 else
  assert(city:GetBuildings():HasBuilding(item.Index)==false,"BUILDING_ALREADY_PRESENT_OR_UNKNOWN")
 end
 -- Re-read the same native facts used by the quote immediately before entry;
 -- a previously displayed price never authorizes a stale purchase.
 local quote=purchaseFacts(city,kind,item,currency,false)
 assert(quote.can_purchase==true,"CANNOT_PURCHASE: "..table.concat(quote.reasons,"; "))
 local cost,balance,params=quote.cost,quote.balance,quote.params
 assert(type(CityManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
 return {{token=token,kind=kind,name=name,currency=currency,itemIndex=number(item.Index),itemHash=number(item.Hash),
  owner=owner,city={city_id},x=number(city:GetX()),y=number(city:GetY()),before=before,
  cost=cost,balance=balance,params=params,civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then
 print("ERR:ITEM_PURCHASE_REFUSED|"..tostring(r));print("{SENTINEL}");return
end
if not (__civmcpItemPurchase and __civmcpItemPurchase.token==token) then
 __civmcpItemPurchase=r
 r.entered=true
 CityManager.RequestCommand(cityFor(r.owner,r.city),CityCommandTypes.PURCHASE,r.params)
 r.returned=true
end
print("OK:ITEM_PURCHASE_REQUESTED|"..name)
print("{SENTINEL}")
'''


def build_item_purchase_query(city_id: int, item_type: str, item_name: str, yield_type: str, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpItemPurchase
local reason="RECEIPT_MISSING_OR_MISMATCHED"
local ok,product,balance=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.city=={city_id}
  and r.kind=={json.dumps(item_type)} and r.name=={json.dumps(item_name)} and r.currency=={json.dumps(yield_type)})
 reason="SCOPE_CHANGED";assert(sameScope(r))
 reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 reason="CITY_CHANGED";local city=cityFor(r.owner,r.city);assert(sameCity(city,r))
 reason="ITEM_CHANGED"
 local item=(r.kind=="UNIT" and GameInfo.Units or GameInfo.Buildings)[r.name]
 assert(item and item.Index==r.itemIndex and item.Hash==r.itemHash)
 reason="PRODUCT_UNCONFIRMED"
 local product
 if r.kind=="UNIT" then
  local matches={{}}
  for _,unit in Players[r.owner]:GetUnits():Members() do
   local id=number(unit:GetID())
   if not r.before[id] and unit:GetOwner()==r.owner and unit:GetType()==r.itemIndex then
    local plot=Map.GetPlot(unit:GetX(),unit:GetY())
    -- Purchased naval/air units may appear in a district, not the city center.
    -- The owned plot must still belong to the original purchasing city.
    if plot and sameCity(Cities.GetPlotPurchaseCity(plot),r) then table.insert(matches,id) end
   end
  end
  -- Native bonuses may create more than one unit. Under the single-controller
  -- contract, enumerate the exact new products instead of assuming a count.
  assert(#matches>0);table.sort(matches);product="unit:"..table.concat(matches,",")
 else
  assert(city:GetBuildings():HasBuilding(r.itemIndex)==true)
  product="building:"..r.itemIndex
 end
 reason="BALANCE_DELTA_UNCONFIRMED"
 local balance=balanceFor(r.owner,r.currency);assert(balance==r.balance-r.cost)
 return product,balance
end)
if ok then
 print("ITEM_PURCHASE_CONFIRMED|"..r.token.."|"..r.city.."|"..r.name.."|"..r.currency.."|"..product.."|cost:"..r.cost.."|balance:"..r.balance.."->"..balance)
else print("ITEM_PURCHASE_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''


def parse_item_purchase_confirmation(lines: list[str], token: str, city_id: int, item_name: str, yield_type: str) -> str | None:
    rows=[row.split("|") for row in lines if row.startswith("ITEM_PURCHASE_CONFIRMED|")]
    if len(rows)==1 and len(rows[0])==8:
        row=rows[0]
        if row[1:5]==[token,str(city_id),item_name,yield_type] and row[5].startswith(("unit:","building:")):
            return f"PURCHASED|{item_name}|city:{city_id}|{yield_type}|{row[5]}|{row[6]}|{row[7]}"
    return None
