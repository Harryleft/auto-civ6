"""Pure, tri-state government predicates shared by reports and write preflight.

Lazy operands preserve the write path's native getter order and short circuits.
The report supplies already-read values; nil means unknown, never false.
"""

LUA_GOVERNMENT_CONDITIONS = r'''
local function governmentBool(value)
 if type(value)=="function" then value=value() end
 if type(value)=="boolean" then return value end
 return nil
end
local function governmentAnd(left,right)
 left=governmentBool(left)
 if left==false then return false end
 right=governmentBool(right)
 if right==false then return false end
 if left==true and right==true then return true end
 return nil
end
local function governmentNot(value)
 value=governmentBool(value)
 if value==nil then return nil end
 return not value
end
local function governmentTurnAllowed(active,strategic)
 return governmentAnd(active,strategic)
end
local function governmentFreeChangeWindow(current,civicCompleted)
 if type(current)~="number" or current~=math.floor(current) then return nil end
 if current==-1 then return true end
 return governmentBool(civicCompleted)
end
local function governmentNewChangeAllowed(canChange,made,window)
 return governmentAnd(canChange,function()
  return governmentAnd(governmentNot(made),window)
 end)
end
'''
