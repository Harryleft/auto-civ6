"""Native purchase facts shared by candidate reads and pre-submit checks."""

LUA_PURCHASE_FACTS = r'''
local function purchaseNumber(value)
 return type(value)=="number" and value==value and math.abs(value)<math.huge
end
local function purchaseFacts(city,kind,item,currency,withVisibility)
 local q={currency=currency,reasons={}}
 local function reason(value) table.insert(q.reasons,tostring(value)) end
 local yieldRow=GameInfo.Yields[currency]
 if not yieldRow then reason("CURRENCY_UNAVAILABLE");return q end
 q.params={[CityCommandTypes.PARAM_YIELD_TYPE]=yieldRow.Index}
 if kind=="UNIT" then
  q.formation=MilitaryFormationTypes.STANDARD_MILITARY_FORMATION
  if q.formation==nil then reason("FORMATION_UNAVAILABLE");return q end
  q.params[CityCommandTypes.PARAM_UNIT_TYPE]=item.Hash
  q.params[CityCommandTypes.PARAM_MILITARY_FORMATION_TYPE]=q.formation
 else q.params[CityCommandTypes.PARAM_BUILDING_TYPE]=item.Hash end
 -- ProductionPanel filters currency BEFORE CanStartCommand: that command can
 -- accept a syntactically valid currency even when this purchase is disabled.
 if currency=="YIELD_GOLD" or item.PurchaseYield==currency then
  q.currency_enabled=item.PurchaseYield==currency
 else
  local enabledOK,enabled=pcall(function()
   if kind=="UNIT" then return city:GetGold():IsUnitFaithPurchaseEnabled(item.Hash) end
   return city:GetGold():IsBuildingFaithPurchaseEnabled(item.Hash)
  end)
  if enabledOK and type(enabled)=="boolean" then q.currency_enabled=enabled end
 end
 if q.currency_enabled==false then
  q.visible=false;q.can_purchase=false;reason("CURRENCY_NOT_SUPPORTED")
 elseif q.currency_enabled==nil then reason("CURRENCY_ELIGIBILITY_UNAVAILABLE") end
 if q.currency_enabled==true then
  if withVisibility then
   local ok,value=pcall(CityManager.CanStartCommand,city,CityCommandTypes.PURCHASE,true,q.params,false)
   if ok and type(value)=="boolean" then q.visible=value end
  end
  local ok,allowed,results=pcall(CityManager.CanStartCommand,city,CityCommandTypes.PURCHASE,false,q.params,true)
  if ok and type(allowed)=="boolean" then q.can_purchase=allowed
  else reason("ELIGIBILITY_UNAVAILABLE") end
  if q.can_purchase==false and type(results)=="table" then
   local reasons=CityCommandResults and results[CityCommandResults.FAILURE_REASONS]
   if type(reasons)=="table" then for _,key in ipairs(reasons) do
    local translated,text=pcall(Locale.Lookup,key)
    reason(translated and text~=key and (tostring(key)..": "..tostring(text)) or key)
   end end
  end
  -- A generic price may exist for an unsupported currency (e.g. Apostle gold).
  -- Only expose it for a native offered/allowed currency, or during execution
  -- where the same native eligibility check must succeed before submitting.
  if q.visible==false and q.can_purchase~=true then reason("NOT_OFFERED")
  else
   local costOK,cost=pcall(function()
    if kind=="UNIT" then return city:GetGold():GetPurchaseCost(yieldRow.Index,item.Hash,q.formation) end
    return city:GetGold():GetPurchaseCost(yieldRow.Index,item.Hash)
   end)
   if costOK and purchaseNumber(cost) and cost>=0 then q.cost=cost else reason("PRICE_UNAVAILABLE") end
  end
 end
 local balanceOK,balance=pcall(function()
  if currency=="YIELD_FAITH" then return Players[city:GetOwner()]:GetReligion():GetFaithBalance() end
  return Players[city:GetOwner()]:GetTreasury():GetGoldBalance()
 end)
 if balanceOK and purchaseNumber(balance) then q.balance=balance else reason("BALANCE_UNAVAILABLE") end
 if q.cost~=nil and q.balance~=nil and q.balance<q.cost then
  q.can_purchase=false;reason("INSUFFICIENT_BALANCE")
 end
 if kind=="BUILDING" then
  local presentOK,present=pcall(function() return city:GetBuildings():HasBuilding(item.Index) end)
  if presentOK and present==true then q.can_purchase=false;reason("BUILDING_ALREADY_PRESENT")
  elseif not presentOK or present~=false then
   if q.can_purchase==true then q.can_purchase=nil end
   reason("BUILDING_PRESENCE_UNAVAILABLE")
  end
 elseif item.UnitType=="UNIT_TRADER" then
  local capOK,count,cap=pcall(function()
   local count=0;for _,u in Players[city:GetOwner()]:GetUnits():Members() do
    if GameInfo.Units[u:GetType()].UnitType=="UNIT_TRADER" then count=count+1 end
   end
   return count,Players[city:GetOwner()]:GetTrade():GetOutgoingRouteCapacity()
  end)
  if capOK and purchaseNumber(cap) then
   if count>=cap then
    q.capacity_reached=true;q.can_purchase=false;reason("TRADER_CAPACITY_REACHED ("..count.."/"..cap..")")
   end
  else
   if q.can_purchase==true then q.can_purchase=nil end
   reason("TRADER_CAPACITY_UNAVAILABLE")
  end
 end
 -- Supplement an opaque native refusal only with observed missing prerequisites.
 -- These diagnostics never grant eligibility or evaluate the full rule system.
 if q.can_purchase==false and q.currency_enabled==true and kind=="UNIT" then
  pcall(function()
   local missing,seen,known,anyPresent={},0,true,false
   for row in GameInfo.Unit_BuildingPrereqs() do if row.Unit==item.UnitType then
    local building=GameInfo.Buildings[row.PrereqBuilding]
    local present=building and city:GetBuildings():HasBuilding(building.Index)
    seen=seen+1
    if present==true then anyPresent=true
    elseif present==false then table.insert(missing,row.PrereqBuilding) else known=false end
   end end
   if seen>0 and known and not anyPresent then
    reason("PREREQUISITE_BUILDINGS_ABSENT: "..table.concat(missing,", "))
   end
  end)
  if item.RequiresInquisition==1 or item.RequiresInquisition==true then
   pcall(function()
    if Players[city:GetOwner()]:GetReligion():HasLaunchedInquisition()==false then reason("INQUISITION_NOT_LAUNCHED") end
   end)
  end
 end
 if q.can_purchase==true and (q.cost==nil or q.balance==nil) then q.can_purchase=nil end
 if q.can_purchase==false and #q.reasons==0 then reason("NATIVE_PURCHASE_REFUSED_NO_REASON") end
 return q
end
local function purchaseEscape(value)
 return (tostring(value):gsub("%%","%%25"):gsub("|","%%7C"):gsub(";","%%3B"):gsub("\n","%%0A"):gsub("\r","%%0D"))
end
local function printPurchase(kind,name,q)
 local reasons={};for _,value in ipairs(q.reasons) do table.insert(reasons,purchaseEscape(value)) end
 print("PURCHASE|"..kind.."|"..name.."|"..q.currency.."|"..tostring(q.cost).."|"..tostring(q.balance)
  .."|"..tostring(q.can_purchase).."|"..tostring(q.visible).."|"..table.concat(reasons,";"))
end
'''
