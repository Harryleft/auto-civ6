"""On-demand native growth facts, with the same units as the installed city UI.

Overall growth is an engine observation. Do not reconstruct it by multiplying
every component: happiness is a percent, other growth is an additive fraction,
and occupation/housing/loyalty do not form a universal multiplicative formula.
"""
from civ_mcp.lua import public_reports
from civ_mcp.snapshot import pending_facts


def build_query(city_id=None, *, evidence=False):
    if city_id is not None and (type(city_id) is not int or city_id < 0):
        raise ValueError('city_id must be a nonnegative full city ID')
    # Reuse the existing JSON wire encoding; 17 digits preserve engine doubles.
    header = public_reports.HEADER.replace('return tostring(v) end', "return string.format('%.17g',v) end")
    return header + f'\nlocal wanted={city_id if city_id is not None else -1}\nlocal evidence={str(evidence).lower()}\n' + r'''
local cities=A();local found=false
local ok,err=pcall(function()
 for _,city in p:GetCities():Members() do
  local id=city:GetID()
  if wanted<0 or id==wanted then
   assert(city:GetOwner()==me,'OWN_CITY_SCOPE_MISMATCH');found=true
   local g=city:GetGrowth();local c={city_id=id,name=Locale.Lookup(city:GetName()),owner=me}
   local function n(key,fn)
    local value=read('city.'..id..'.'..key,fn)
    if type(value)~='number' then value=missing('city.'..id..'.'..key) end
    c[key]=value;return value
   end
   n('population',function()return city:GetPopulation()end)
   n('food_yield',function()return city:GetYield(GameInfo.Yields.YIELD_FOOD.Index)end)
   n('food_surplus',function()return g:GetFoodSurplus()end)
   n('food_stored',function()return g:GetFood()end)
   n('growth_threshold',function()return g:GetGrowthThreshold()end)
   n('turns_until_growth',function()return g:GetTurnsUntilGrowth()end)
   n('turns_until_starvation',function()return g:GetTurnsUntilStarvation()end)
   n('housing',function()return g:GetHousing()end)
   n('amenities',function()return g:GetAmenities()end)
   n('amenities_needed',function()return g:GetAmenitiesNeeded()end)
   n('happiness_growth_percent',function()return g:GetHappinessGrowthModifier()end)
   n('happiness_nonfood_yield_percent',function()return g:GetHappinessNonFoodYieldModifier()end)
   n('housing_growth_multiplier',function()return g:GetHousingGrowthModifier()end)
   n('occupation_growth_multiplier',function()return g:GetOccupationGrowthModifier()end)
   n('loyalty_growth_multiplier',function()return g:GetLoyaltyGrowthModifier()end)
   n('other_growth_fraction',function()return g:GetOtherGrowthModifier()end)
   n('overall_growth_multiplier',function()return g:GetOverallGrowthModifier()end)
   c.occupied=read('city.'..id..'.occupied',function()return city:IsOccupied()end)
   if type(c.occupied)~='boolean'then c.occupied=missing('city.'..id..'.occupied')end
   local function difference(a,b)if type(a)=='number'and type(b)=='number'then return a-b end;return NULL end
   c.derived={food_consumption=difference(c.food_yield,c.food_surplus),
    food_remaining=difference(c.growth_threshold,c.food_stored),
    housing_headroom=difference(c.housing,c.population),amenities_balance=difference(c.amenities,c.amenities_needed)}
   -- CitySupport uses this product for the next-turn progress bar when growing.
   -- In a deficit the city UI explicitly does not apply growth multipliers.
   c.derived.growth_food_per_turn=NULL
   if type(c.turns_until_growth)=='number'and c.turns_until_growth>=0 and type(c.food_surplus)=='number'and c.food_surplus>0 and type(c.overall_growth_multiplier)=='number'then
    c.derived.growth_food_per_turn=c.food_surplus*c.overall_growth_multiplier
   elseif type(c.food_surplus)=='number'and c.food_surplus<=0 then c.derived.growth_food_per_turn=c.food_surplus end
   if evidence then
    c.yield_tooltips={}
    for _,kind in ipairs({'FOOD','PRODUCTION','GOLD','SCIENCE','CULTURE','FAITH'})do
     c.yield_tooltips[kind]=read('city.'..id..'.tooltip.'..kind,function()return city:GetYieldToolTip(GameInfo.Yields['YIELD_'..kind].Index)end)
    end
    c.housing_advice=read('city.'..id..'.housing_advice',function()return city:GetHousingAdvice()end)
    c.amenity_advice=read('city.'..id..'.amenity_advice',function()return city:GetAmenityAdvice()end)
   end
   cities[#cities+1]=c
  end
 end
end)
table.sort(cities,function(a,b)return a.city_id<b.city_id end)
local knownIdentity=type(me)=='number'and me>=0 and type(turn)=='number'and turn>=0 and type(seed)=='number'and seed==seed and math.abs(seed)<math.huge and type(civ)=='string'and civ:match('^CIVILIZATION_.+')~=nil
if not knownIdentity then missing('world.identity')end
local stable=knownIdentity and Game.GetLocalPlayer()==me and Game.GetCurrentGameTurn()==turn and GameConfiguration.GetValue('GAME_SYNC_RANDOM_SEED')==seed and PlayerConfigurations[me]:GetCivilizationTypeName()==civ
local valid=ok and stable and (wanted<0 or found)
local result={schema_version=1,status=valid and (#unknown==0 and 'ready'or 'partial')or 'unknown',
 identity={player_id=me,turn=turn,seed=seed,civilization=civ},cities=valid and cities or NULL,
 unknown_fields=unknown,scope='current owned cities; one InGame read, not atomic'}
if not ok then result.error=tostring(err)elseif not stable then result.error='World identity incomplete or changed during read'elseif wanted>=0 and not found then result.error='Owned city ID not found'end
print('PUBLIC_REPORT|'..encode(result))
''' + f'print({public_reports.SENTINEL!r})'


def brief(data):
    def num(v):
        return 'unknown' if v is None else str(v)
    lines=[f"City growth: {data['status']} | {data.get('identity')}"]
    for c in data.get('cities') or []:
        d=c['derived']
        lines += [f"{c['name']} [id:{c['city_id']}] pop {num(c['population'])}; native growth {num(c['turns_until_growth'])}t, starvation {num(c['turns_until_starvation'])}t (-1 = not currently predicted; null = unknown).",
            f"  Food: yield {num(c['food_yield'])}, consumption {num(d['food_consumption'])} (yield-surplus), surplus {num(c['food_surplus'])}; stored {num(c['food_stored'])}/{num(c['growth_threshold'])}; effective growth food/t {num(d['growth_food_per_turn'])} (UI progress calculation; native turns take precedence).",
            f"  Housing {num(c['housing'])}, headroom {num(d['housing_headroom'])}; amenities {num(c['amenities'])}, needed {num(c['amenities_needed'])}, balance {num(d['amenities_balance'])}.",
            f"  Native growth: overall x{num(c['overall_growth_multiplier'])}; housing x{num(c['housing_growth_multiplier'])}; loyalty x{num(c['loyalty_growth_multiplier'])}; occupation x{num(c['occupation_growth_multiplier'])}, occupied {c['occupied']}; happiness {num(c['happiness_growth_percent'])}%; other fraction {num(c['other_growth_fraction'])}.",
            f"  Native happiness non-food yield adjustment: {num(c['happiness_nonfood_yield_percent'])}% (not the entire city yield modifier)."]
    if data.get('error'):lines.append(data['error'])
    if data.get('unknown_fields'):lines.append('Unknown: '+', '.join(data['unknown_fields']))
    lines.append('Components are not a universal product; no action recommendation. --view structured retains fields; --view evidence adds six native yield tooltips and advice.')
    return '\n'.join(lines)


async def read_context(game, city_id=None, view='brief'):
    if view not in ('brief','structured','evidence'):
        raise ValueError('view must be brief, structured or evidence')
    source=build_query(city_id,evidence=view=='evidence')
    pending=pending_facts(game)
    if any(pending.values()):
        return dict(schema_version=1,status='blocked',pending=pending,error='Confirm the original pending operation first.')
    data=public_reports.parse(await game.conn.execute_write(source,timeout=30.0))
    if view=='brief':
        return dict(schema_version=1,status=data['status'],report=brief(data))
    return data
