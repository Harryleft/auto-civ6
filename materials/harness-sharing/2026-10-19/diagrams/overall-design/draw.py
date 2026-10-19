from pathlib import Path
from html import escape
out=[]
def add(s): out.append(s)
def text(x,y,s,size=24,color='#263449',anchor='middle',weight=400):
 add(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="Noto Sans SC" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(s)}</text>')
def box(x,y,w,h,fill,stroke='#d8e2ec',rx=18):
 add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
def arrow(points,color='#7b899b',dash=False):
 add(f'<polyline points="'+ ' '.join(f'{x},{y}' for x,y in points)+f'" fill="none" stroke="{color}" stroke-width="2.6"'+(' stroke-dasharray="7 5"' if dash else '')+'/>')
 x,y=points[-1]; a,b=points[-2]; dx=x-a;dy=y-b
 if dx>0: v=[(x,y),(x-10,y-5),(x-10,y+5)]
 elif dx<0:v=[(x,y),(x+10,y-5),(x+10,y+5)]
 elif dy>0:v=[(x,y),(x-5,y-10),(x+5,y-10)]
 else:v=[(x,y),(x-5,y+10),(x+5,y+10)]
 add('<polygon points="'+' '.join(f'{a},{b}' for a,b in v)+f'" fill="{color}"/>')
def card(x,y,title,sub,fill,stroke):
 box(x,y,270,100,fill,stroke)
 text(x+135,y+41,title,24,weight=600)
 text(x+135,y+75,sub,18,'#566376')
add('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1175" viewBox="0 0 1200 1175">')
add('<rect width="1200" height="1175" fill="#ffffff"/>')
text(55,48,'总体设计：判断、执行、确认，持续循环',32,anchor='start',weight=650)
text(55,86,'模型选择值得做的事；Harness 让操作有依据、结果可确认。',21,'#657185',anchor='start')
box(55,112,1090,888,'#fafbfd','#edf0f5',24)
for x,title,sub,color in [(225,'模型 / Agent','决定做什么','#2e62ae'),(600,'Harness / 执行环境','查询、操作、核实','#258067'),(975,'文明 6 游戏引擎','规则与真实状态','#73539e')]:
 text(x,151,title,26,color,weight=600);text(x,179,sub,18,'#657185')
# Stable knowledge is consulted as needed, not reloaded each turn.
box(95,202,260,47,'#e6efff','#b7cdec',23);text(225,232,'规则知识：按需查询',19,'#2e62ae')
arrow([(225,249),(225,270)],'#7398c8')
card(90,270,'理解局面与规则','稳定规则 + 当前事实','#ffffff','#b7cdec')
card(465,270,'查询、整理、交回','把游戏事实交给模型','#ffffff','#abd5c5')
card(840,270,'当前局面','城市、单位、对手与交互','#ffffff','#d1c1e3')
arrow([(840,320),(735,320)],'#73539e');arrow([(465,320),(360,320)],'#258067')
arrow([(225,370),(225,450)],'#2e62ae');text(225,416,'形成可修正的判断',18,'#2e62ae')
card(90,450,'选择下一步','围绕胜利目标：行动或待命','#e6efff','#8eb2e4')
card(465,450,'核对后，只提交一次','本局 · 对象 · 当前资格','#e3f4eb','#91c9b3')
card(840,450,'按规则执行','决定能否执行，产生真实变化','#eee8f6','#bca4d4')
arrow([(360,500),(465,500)],'#2e62ae');text(412,479,'提出要求',17,'#2e62ae')
arrow([(735,500),(840,500)],'#258067');text(787,479,'提交操作',17,'#258067')
arrow([(600,550),(600,670)],'#258067');text(614,615,'记录原请求',18,'#258067',anchor='start')
arrow([(975,550),(975,690)],'#73539e');text(975,616,'等待处理后查证',18,'#73539e')
card(840,690,'提供实际结果','对象状态、项目进度等','#ffffff','#d1c1e3')
add('<polygon points="600,670 720,740 600,810 480,740" fill="#d7eee2" stroke="#72b799" stroke-width="2"/>')
text(600,747,'结果已确认？',22,'#21654f',weight=600)
arrow([(840,740),(720,740)],'#73539e');text(780,720,'返回事实',17,'#73539e')
card(90,690,'更新判断','用核实结果安排下一步','#e6efff','#8eb2e4')
arrow([(480,740),(360,740)],'#258067');text(419,719,'是：交回结果',17,'#258067')
arrow([(225,790),(225,835),(40,835),(40,500),(90,500)],'#2e62ae')
text(225,864,'进入下一轮决策',18,'#2e62ae')
arrow([(600,810),(600,875)],'#b58732');text(614,850,'尚未确认',18,'#966b25',anchor='start')
box(465,875,270,100,'#fff3d9','#ddbd79')
text(600,913,'保留原请求',24,'#805a1e',weight=600);text(600,947,'等待、查证或处理交互',18,'#805a1e')
arrow([(735,925),(1168,925),(1168,740),(1110,740)],'#b58732',True)
text(951,901,'继续查原动作，不重复提交',18,'#966b25')
# A separate footer describes recovery without suggesting that loading erases pending work.
box(55,1025,1090,120,'#f6f7fa','#d8dee8',16)
text(82,1064,'故障恢复',23,'#3f4a60',anchor='start',weight=600)
text(82,1098,'先辨别正常等待、交互阻塞与故障；需要恢复时，优先从主菜单加载存档。',20,'#536176',anchor='start')
text(82,1128,'恢复后核对世界身份和局面；未确认的旧请求仍需明确处理。',20,'#536176',anchor='start')
add('</svg>')
Path(__file__).with_name('diagram.svg').write_text('\n'.join(out))
