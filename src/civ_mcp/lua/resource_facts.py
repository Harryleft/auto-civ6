"""Pure, visibility-bounded resource plot observations; no operation probes."""

LUA_RESOURCE_FACTS = r'''
local function resourceWorkUnit(row)
 if row.ResourceClassType=='RESOURCECLASS_ARTIFACT' or row.ResourceType=='RESOURCE_ANTIQUITY_SITE' or row.ResourceType=='RESOURCE_SHIPWRECK'then return 'UNIT_ARCHAEOLOGIST' end
 if row.ResourceClassType=='RESOURCECLASS_BONUS'or row.ResourceClassType=='RESOURCECLASS_LUXURY'or row.ResourceClassType=='RESOURCECLASS_STRATEGIC'then return 'UNIT_BUILDER' end
end
local function resourcePlotFacts(me,plot,row)
 local function read(fn)local ok,v=pcall(fn);if ok then return v end end
 local unknown={}
 local function missing(field)unknown[#unknown+1]=field end
 local owner=read(function()return plot:GetOwner()end)
 local visible=read(function()return PlayersVisibility[me]:IsVisible(plot:GetIndex())end)
 if type(visible)~='boolean'then visible=nil;missing('visible_now')end
 local result={visible_now=visible,work_unit=resourceWorkUnit(row),unknown_fields=unknown}
 if owner==me or visible==true then
  if type(owner)=='number'and owner==owner and owner>=-1 and owner<=63 and owner==math.floor(owner)then result.owner_id=owner
  else missing('owner_id')end
  local index=read(function()return plot:GetImprovementType()end)
  if type(index)=='number'and index==index and math.abs(index)<math.huge and index==math.floor(index)then
   if index<0 then result.improvement_type='NONE';result.pillaged=false
   else
    local info=read(function()return GameInfo.Improvements[index]end)
    result.improvement_type=info and info.ImprovementType or nil
    if type(result.improvement_type)~='string'or result.improvement_type==''then result.improvement_type=nil;missing('improvement_type')end
    local pillaged=read(function()return plot:IsImprovementPillaged()end)
    if type(pillaged)=='boolean'then result.pillaged=pillaged else missing('pillaged')end
   end
  else missing('improvement_type');missing('pillaged')end
  result.details_status=#unknown==0 and 'current'or 'unknown'
 else result.details_status=visible==false and 'withheld_fog'or 'unknown'
 end
 return result
end
local function printResourceFacts(scope,me,plot,row,observed)
 local facts=observed or resourcePlotFacts(me,plot,row)
 local function wire(v)if v==nil then return '?'end;return tostring(v)end
 print('RESOURCE_DETAIL|'..scope..'|'..plot:GetX()..','..plot:GetY()..'|'..wire(facts.owner_id)..'|'..wire(facts.visible_now)..'|'..wire(facts.improvement_type)..'|'..wire(facts.pillaged)..'|'..wire(facts.work_unit))
end
'''
