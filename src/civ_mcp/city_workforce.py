"""Read current citizen-management facts without changing allocation or UI.

MANAGE arrays establish the engine's management scope. Purchased plots and
IsPlotWorked are separate native observations: neither a geometric radius nor
sum(managed citizens) is substituted for population or free citizens.
"""
from civ_mcp.lua import public_reports
from civ_mcp.snapshot import pending_facts


def build_query(city_id, *, evidence=False):
    if type(city_id) is not int or city_id < 0:
        raise ValueError('city_id must be a nonnegative full city ID')
    header=public_reports.HEADER.replace('return tostring(v) end', "return string.format('%.17g',v) end")
    return header+f'\nlocal wanted={city_id}\nlocal evidence={str(evidence).lower()}\n'+r'''
local cityData=NULL;local plots=NULL;local scanComplete=false
local purchasedKnown=false;local purchasedIDs=A();local manageKeys=A()
local manageDiagnostics=NULL
local kinds={'FOOD','PRODUCTION','GOLD','SCIENCE','CULTURE','FAITH'}
local function integer(v)return type(v)=='number'and v==v and math.abs(v)<math.huge and v%1==0 end
local function number(label,fn)
 local v=read(label,fn);if type(v)~='number'then return missing(label)end;return v
end
local function boolean(label,fn)
 local v=read(label,fn);if type(v)~='boolean'then return missing(label)end;return v
end
local ok,err=pcall(function()
 local city=nil
 for _,candidate in p:GetCities():Members()do
  if candidate:GetID()==wanted then assert(city==nil,'Duplicate owned city ID');city=candidate end
 end
 assert(city and city:GetID()==wanted and city:GetOwner()==me,'Owned city ID not found or changed')
 cityData={city_id=wanted,owner=me,name=Locale.Lookup(city:GetName()),
  x=number('city.x',function()return city:GetX()end),y=number('city.y',function()return city:GetY()end),
  population=number('city.population',function()return city:GetPopulation()end),
  food_surplus=number('city.food_surplus',function()return city:GetGrowth():GetFoodSurplus()end),yield_preferences={}}
 local citizens=city:GetCitizens()
 for _,kind in ipairs(kinds)do
  local key='YIELD_'..kind
  cityData.yield_preferences[key]={
   favored=boolean('focus.'..key..'.favored',function()return citizens:IsFavoredYield(GameInfo.Yields[key].Index)end),
   ignored=boolean('focus.'..key..'.ignored',function()return citizens:IsDisfavoredYield(GameInfo.Yields[key].Index)end)}
 end
 local purchased=read('purchased_plots',function()return Map.GetCityPlots():GetPurchasedPlots(city)end)
 local purchasedSet={}
 if type(purchased)=='table'and purchased~=NULL then
  purchasedKnown=true
  for key,id in pairs(purchased)do
   if not integer(key)or not integer(id)or id<0 or purchasedSet[id]then purchasedKnown=false;break end
   purchasedSet[id]=true;purchasedIDs[#purchasedIDs+1]=id
  end
  if not purchasedKnown then missing('purchased_plots.invalid')else table.sort(purchasedIDs)end
 else missing('purchased_plots.type')
 end
 -- PlotInfo.lua:363-375 forwards PARAM_MANAGE_CITIZEN from the UI into this
 -- read-only target query. Explicit true requests citizen-management targets
 -- without selecting a city, reading interface-mode state or changing the UI.
 -- The native behavior of this explicit value requires live-game acceptance;
 -- isolated fixtures only verify the query/return contract and fail-closed path.
 local parameterKey=CityCommandTypes.PARAM_MANAGE_CITIZEN
 manageDiagnostics={parameter={name='PARAM_MANAGE_CITIZEN',key=parameterKey or NULL,
  key_type=type(parameterKey),value=true,value_type='boolean'},result_type='not_queried',
  top_level_keys=A(),columns={},missing_columns=A()}
 if type(parameterKey)~='number'and type(parameterKey)~='string'then
  missing('manage.parameter.PARAM_MANAGE_CITIZEN');error('Native MANAGE citizen parameter unavailable')
 end
 local parameters={[parameterKey]=true}
 local targets=CityManager.GetCommandTargets(city,CityCommandTypes.MANAGE,parameters)
 manageDiagnostics.result_type=type(targets)
 if type(targets)=='table'then
  for key,value in pairs(targets)do
   manageDiagnostics.top_level_keys[#manageDiagnostics.top_level_keys+1]={
    key=(type(key)=='number'or type(key)=='string')and key or NULL,key_type=type(key),value_type=type(value)}
  end
  table.sort(manageDiagnostics.top_level_keys,function(a,b)return a.key_type..tostring(a.key)<b.key_type..tostring(b.key)end)
 end
 local columns={};local complete=true
 for i,name in ipairs({'PLOTS','CITIZENS','MAX_CITIZENS','LOCKED_CITIZENS'})do
  local key=CityCommandResults[name]
  local value=nil;if type(targets)=='table'and key~=nil then value=targets[key]end
  local column={key=key or NULL,key_type=type(key),value_type=type(value)}
  manageDiagnostics.columns[name]=column
  if type(value)=='table'then
   columns[i]=value;column.entry_count=0;column.key_types={};column.value_types={}
   for entryKey,entry in pairs(value)do
    column.entry_count=column.entry_count+1
    column.key_types[type(entryKey)]=(column.key_types[type(entryKey)]or 0)+1
    column.value_types[type(entry)]=(column.value_types[type(entry)]or 0)+1
   end
  else
   complete=false;missing('manage.columns.'..name)
   manageDiagnostics.missing_columns[#manageDiagnostics.missing_columns+1]=name
  end
 end
 assert(type(targets)=='table','Native MANAGE result unavailable')
 assert(complete,'Native MANAGE arrays unavailable; see missing column labels and evidence diagnostics')
 local keys={};for key in pairs(columns[1])do assert(integer(key),'Native MANAGE key invalid');keys[#keys+1]=key end
 table.sort(keys)
 for i=2,4 do
  local count=0;for key in pairs(columns[i])do assert(columns[1][key]~=nil,'Native MANAGE keys differ');count=count+1 end
  assert(count==#keys,'Native MANAGE arrays unpaired')
 end
 local result=A();local seen={}
 for _,key in ipairs(keys)do
  local index,n,max,locked=columns[1][key],columns[2][key],columns[3][key],columns[4][key]
  assert(integer(index)and index>=0 and not seen[index],'Native MANAGE plot invalid or duplicated');seen[index]=true
  assert(integer(n)and n>=0 and integer(max)and max>=0 and integer(locked)and locked>=0,'Native MANAGE count invalid')
  local plot=Map.GetPlotByIndex(index);assert(plot and plot:GetIndex()==index,'Native MANAGE plot binding unavailable')
  local label='plot.'..index
  local x=number(label..'.x',function()return plot:GetX()end)
  local y=number(label..'.y',function()return plot:GetY()end)
  local owner=number(label..'.owner',function()return plot:GetOwner()end)
  local visible=boolean(label..'.visible',function()return vis:IsVisible(index)end)
  local observable=owner==me or visible==true
  local row={native_key=key,plot_index=index,x=x,y=y,citizens=n,max_citizens=max,locked_citizens=locked,
   owner_id=observable and owner or NULL,visible_now=visible,
   purchased_by_city=purchasedKnown and purchasedSet[index]==true or NULL,yields={}}
  if purchasedKnown and not purchasedSet[index]then row.purchased_by_city=false end
  if integer(x)and integer(y)then row.is_plot_worked=boolean(label..'.worked',function()return citizens:IsPlotWorked(x,y)end)
  else row.is_plot_worked=missing(label..'.worked_coordinates')end
  -- Preserve the native columns independently. In the T50 live baseline the
  -- city center reports citizens=1/max_citizens=0/locked_citizens=0; MAX_CITIZENS
  -- is not a universal upper bound on CITIZENS. No lock/count relation is assumed.
  for _,kind in ipairs(kinds)do
   local yield='YIELD_'..kind
   row.yields[yield]=observable and number(label..'.'..yield,function()return plot:GetYield(GameInfo.Yields[yield].Index)end)or missing(label..'.dynamic_yields_withheld')
  end
  result[#result+1]=row;manageKeys[#manageKeys+1]=key
 end
 plots=result;scanComplete=true
end)
if not scanComplete then missing('manage.targets')end
local knownIdentity=integer(me)and me>=0 and integer(turn)and turn>=0 and integer(seed)and type(civ)=='string'and civ:match('^CIVILIZATION_.+')~=nil
if not knownIdentity then missing('world.identity')end
local stable=knownIdentity and Game.GetLocalPlayer()==me and Game.GetCurrentGameTurn()==turn and GameConfiguration.GetValue('GAME_SYNC_RANDOM_SEED')==seed and PlayerConfigurations[me]:GetCivilizationTypeName()==civ
local valid=ok and stable and scanComplete
local result={schema_version=1,status=valid and(#unknown==0 and'ready'or'partial')or'unknown',
 identity={civilization=civ,player_id=me,seed=seed,turn=turn},city=stable and cityData or NULL,
 plots=valid and plots or NULL,scan_complete=valid,purchased_plot_scan_complete=stable and purchasedKnown,
 unknown_fields=unknown,scope='native MANAGE targets for one current owned city; not atomic; MAX_CITIZENS is not a universal citizen upper bound: the city center can report 1/0',
 yield_basis='current native plot yields, not marginal city net yields or specialist benefit'}
if evidence then
 result.purchased_plot_ids=stable and purchasedKnown and purchasedIDs or NULL
 result.native_manage_keys=valid and manageKeys or NULL
 result.manage_diagnostics=stable and manageDiagnostics or NULL
end
if not ok then result.error=tostring(err)elseif not stable then result.error='World identity incomplete or changed during read'end
print('PUBLIC_REPORT|'..encode(result))
'''+f'print({public_reports.SENTINEL!r})'


def brief(data):
    def number(value):return 'unknown'if value is None else str(value)
    def state(value):return 'unknown'if value is None else ('yes'if value else 'no')
    lines=[f"City workforce: {data['status']} | {data.get('identity')}"]
    c=data.get('city')
    if c:
        lines.append(f"{c['name']} [id:{c['city_id']}] pop {number(c['population'])}; food surplus {number(c['food_surplus'])}.")
        preferences=c['yield_preferences'];favored=[];ignored=[];unknown=[]
        for key,value in preferences.items():
            if value['favored']is True:favored.append(key)
            if value['ignored']is True:ignored.append(key)
            if value['favored']is None or value['ignored']is None:unknown.append(key)
        lines.append(f"  Focus: favored {','.join(favored)or'none'}; ignored {','.join(ignored)or'none'}; others neither; unknown {','.join(unknown)or'none'}.")
    rows=data.get('plots')or[]
    lines.append(f"Native MANAGE rows: {len(rows) if data.get('scan_complete')else'unknown'}; current plot yields, not marginal city net yields. MAX_CITIZENS is not a universal citizen upper bound; the city center can report 1/0.")
    for p in rows:
        facts=','.join(f"{key.removeprefix('YIELD_')}={number(value)}"for key,value in p['yields'].items()if value is None or value!=0)
        lines.append(f"  ({number(p['x'])},{number(p['y'])}) [plot:{p['plot_index']}] citizens {number(p['citizens'])}/{number(p['max_citizens'])}; locked {number(p['locked_citizens'])}; worked {state(p['is_plot_worked'])}; visible now {state(p['visible_now'])}; owner {number(p['owner_id'])}; assigned_here {state(p['purchased_by_city'])}; {facts or'all six yields 0'}; other yields 0.")
    if data.get('error'):lines.append(data['error'])
    if data.get('unknown_fields'):lines.append('Unknown: '+', '.join(data['unknown_fields']))
    lines.append('Unranked native observations; not an allocation recommendation. Query does not assign/lock citizens or change UI. --view structured/evidence restores all fields and zero yields.')
    return '\n'.join(lines)


async def read_context(game,city_id,view='brief'):
    if view not in ('brief','structured','evidence'):
        raise ValueError('view must be brief, structured or evidence')
    source=build_query(city_id,evidence=view=='evidence')
    pending=pending_facts(game)
    if any(pending.values()):
        return dict(schema_version=1,status='blocked',pending=pending,error='Confirm the original pending operation first.')
    data=public_reports.parse(await game.conn.execute_write(source,timeout=30.0))
    if view=='brief':return dict(schema_version=1,status=data['status'],report=brief(data))
    return data
