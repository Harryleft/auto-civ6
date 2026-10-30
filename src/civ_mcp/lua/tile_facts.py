"""Pure tile purchase facts shared by quote and action preflight."""

LUA_TILE_PURCHASE_FACTS = '''
local function tilePurchaseQuote(city,owner,plot)
 local function amount(fn)
  local ok,v=pcall(fn)
  if ok and type(v)=='number' and v==v and v>=0 and v<math.huge then return v end
  return nil
 end
 local cost=amount(function()return city:GetGold():GetPlotPurchaseCost(plot:GetX(),plot:GetY())end)
 local balance=amount(function()return Players[owner]:GetTreasury():GetGoldBalance()end)
 local params={[CityCommandTypes.PARAM_PLOT_PURCHASE]=1,
  [CityCommandTypes.PARAM_X]=plot:GetX(),[CityCommandTypes.PARAM_Y]=plot:GetY()}
 local ok,eligible=pcall(function()return CityManager.CanStartCommand(city,CityCommandTypes.PURCHASE,params)end)
 if not ok or type(eligible)~='boolean' then eligible=nil end
 local allowed,reason=nil,'OBSERVATION_UNAVAILABLE'
 if cost~=nil and balance~=nil and balance<cost then allowed=false;reason='INSUFFICIENT_GOLD'
 elseif eligible==false then allowed=false;reason='NATIVE_INELIGIBLE'
 elseif eligible==true and cost~=nil and balance~=nil then allowed=true;reason='ELIGIBLE' end
 return cost,balance,allowed,reason
end
'''
