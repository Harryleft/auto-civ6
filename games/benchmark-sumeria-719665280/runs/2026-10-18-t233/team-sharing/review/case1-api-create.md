# 创建画板节点

创建画板节点，支持批量创建、创建含父子关系的节点等。

## 请求

基本 | &nbsp;
---|---
HTTP URL | https://open.feishu.cn/open-apis/board/v1/whiteboards/:whiteboard_id/nodes
HTTP Method | POST
接口频率限制 | [50 次/秒](https://open.feishu.cn/document/ukTMukTMukTM/uUzN04SN3QjL1cDN)
支持的应用类型 | Custom App、Store App
权限要求<br>**调用该 API 所需的权限。开启其中任意一项权限即可调用** | 创建画板节点(board:whiteboard:node:create)
字段权限要求 | **注意事项**：该接口返回体中存在下列敏感字段，仅当开启对应的权限后才会返回；如果无需获取这些字段，则不建议申请<br>获取用户 user ID(contact:user.employee_id:readonly)

### 请求头

名称 | 类型 | 必填 | 描述
---|---|---|---
Authorization | string | 是 | `tenant_access_token`<br>或<br>`user_access_token`<br>**值格式**："Bearer `access_token`"<br>**示例值**："Bearer u-7f1bcd13fc57d46bac21793a18e560"<br>[了解更多：如何选择与获取 access token](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-choose-which-type-of-token-to-use)
Content-Type | string | 是 | **固定值**："application/json; charset=utf-8"

### 路径参数

名称 | 类型 | 描述
---|---|---
whiteboard_id | string | 画板唯一标识，可通过云文档下的文档接口 [获取文档所有块](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/document-docx/docx-v1/document-block/list) 获取，`block_type` 为 43 的 block 即为画板，对应的 <code>block.token</code> 就是画板的<code>whiteboard_id</code><br>**示例值**："Ru8nwrWFOhEmaFbEU2VbPRsHcxb"<br>**数据校验规则**：<br>- 长度范围：`22` ～ `27` 字符

### 查询参数

名称 | 类型 | 必填 | 描述
---|---|---|---
client_token | string | 否 | 操作的唯一标识，与接口返回值的 client_token 相对应，用于幂等的进行更新操作。此值为空表示将发起一次新的请求，此值非空表示幂等的进行更新操作<br>**示例值**：fe599b60-450f-46ff-b2ef-9f6675625b9<br>**数据校验规则**：<br>- 长度范围：`10` ～ `64` 字符
user_id_type | string | 否 | 用户 ID 类型<br>**示例值**：open_id<br>**可选值有**：<br>- open_id：标识一个用户在某个应用中的身份。同一个用户在不同应用中的 Open ID 不同。[了解更多：如何获取 Open ID](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-obtain-openid)<br>- union_id：标识一个用户在某个应用开发商下的身份。同一用户在同一开发商下的应用中的 Union ID 是相同的，在不同开发商下的应用中的 Union ID 是不同的。通过 Union ID，应用开发商可以把同个用户在多个应用中的身份关联起来。[了解更多：如何获取 Union ID？](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-obtain-union-id)<br>- user_id：标识一个用户在某个租户内的身份。同一个用户在租户 A 和租户 B 内的 User ID 是不同的。在同一个租户内，一个用户的 User ID 在所有应用（包括商店应用）中都保持一致。User ID 主要用于在不同的应用间打通用户数据。[了解更多：如何获取 User ID？](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-obtain-user-id)<br>**默认值**：`open_id`<br>**当值为 `user_id`，字段权限要求**：<br>获取用户 user ID(contact:user.employee_id:readonly)

### 请求体

名称 | 类型 | 必填 | 描述
---|---|---|---
nodes | whiteboard.node\[\] | 否 | 子节点数据，不允许传入空数组<br>**数据校验规则**：<br>- 长度范围：`1` ～ `3000`
id | string | 否 | 节点 id，用于唯一标识此节点，不能重复，在创建父子节点、关联其它节点做标记使用<br>**示例值**："o1:1"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
type | string | 是 | 节点图形类型<br>**示例值**："composite_shape"<br>**可选值有**：<br>- image：图片<br>- text_shape：文本<br>- group：组合<br>- composite_shape：基础图形<br>- svg：svg 图形<br>- connector：连线<br>- table：表格<br>- life_line：对象生命线<br>- activation：控制焦点<br>- section：分区<br>- table_uml：类图<br>- table_er：实体关系图<br>- sticky_note：便签<br>- mind_map：思维导图<br>- paint：画笔<br>- combined_fragment：组合片段
parent_id | string | 否 | 父节点 id，必须是已存在的节点或者在本次创建的节点列表内<br>**示例值**："n1:1"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
x | number(float) | 否 | 图形相对画布的 x 轴位置信息（存在父容器时为相对父容器的坐标，父容器为组合图形 group 时，坐标是穿透的），单位为 px<br>**示例值**：100<br>**默认值**：`0`<br>**数据校验规则**：<br>- 取值范围：`-132070` ～ `132070`
y | number(float) | 否 | 图形相对画布的 y 轴位置信息（存在父容器时为相对父容器的坐标，父容器为组合图形 group 时，坐标是穿透的），单位为 px<br>**示例值**：100<br>**默认值**：`0`<br>**数据校验规则**：<br>- 取值范围：`-132070` ～ `132070`
angle | number(float) | 否 | 图形旋转角度，单位度<br>**示例值**：100<br>**默认值**：`0`<br>**数据校验规则**：<br>- 取值范围：`-180` ～ `180`
height | number(float) | 否 | 图形高度，单位为 px<br>**示例值**：100<br>**数据校验规则**：<br>- 取值范围：`0` ～ `132070`
text | text | 否 | 图形内文字（节点类型为 text_shape 时，该字段不能为空）（节点类型为group、section，connector、paint、svg、activation时候，该字段必须为空)
text | string | 否 | 文字内容<br>**示例值**："文字内容"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1024` 字符
font_weight | string | 否 | 文字字重<br>**示例值**："regular"<br>**可选值有**：<br>- regular：常规<br>- bold：加粗
font_size | int | 否 | 文字大小，单位 px，默认为 14 px<br>**示例值**：14
horizontal_align | string | 否 | 水平对齐<br>**示例值**："center"<br>**可选值有**：<br>- left：向左对齐<br>- center：居中对齐<br>- right：向右对齐
vertical_align | string | 否 | 垂直对齐<br>**示例值**："mid"<br>**可选值有**：<br>- top：顶部对齐<br>- mid：垂直居中<br>- bottom：底部对齐
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
angle | int | 否 | 文字旋转角度，单位度<br>**示例值**：90<br>**可选值有**：<br>- 0：文字旋转角度0度<br>- 90：文字旋转角度90度<br>- 180：文字旋转角度180度<br>- 270：文字旋转角度270度<br>**数据校验规则**：<br>- 取值范围：`0` ～ `270`
theme_text_color_code | int | 否 | 文字颜色主题配色编码值<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
theme_text_background_color_code | int | 否 | 文字背景颜色主题配色编码值<br>**示例值**：-1<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
rich_text | rich_text | 否 | 富文本（富文本有值时候会覆盖上面的text信息）<br>如果整段文本只有一个样式，不推荐使用富文本
paragraphs | rich_text_paragraph\[\] | 否 | 段落列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
paragraph_type | int | 是 | 段落类别<br>**示例值**：0<br>**可选值有**：<br>- 0：普通段落<br>- 1：无序列表<br>- 2：有序列表
elements | rich_text_element\[\] | 否 | 段落的元素列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
element_type | int | 是 | 元素类别<br>**示例值**：0<br>**可选值有**：<br>- 0：文本<br>- 1：超链接<br>- 2：@用户<br>- 3：文档
text_element | rich_text_element_text | 否 | 文本元素信息（当前元素为文本类别时候需要填写当前字段）
text | string | 是 | 文字（文字中不能包含换行符）<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重(可选值有：regular：常规, bold：加粗)<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小，单位 px，默认为 14 px<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：false
italic | boolean | 否 | 是否斜体<br>**示例值**：true
link_element | rich_text_element_link | 否 | 超链接类别信息（当前元素为链接类别时候需要有当前字段）
herf | string | 是 | 链接<br>**示例值**："https://bytedance.larkoffice.com"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text | string | 否 | 超链接的文字（为空时会默认文字为超链接）<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重 （regular：常规，bold：加粗）<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小，单位 px，默认为 14 px<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_user_element | rich_text_element_mention_user | 否 | @用户类别信息（当前元素为@用户类别时候需要有当前字段）
user_id | string | 是 | 用户openID，可通过 [获取指定用户的 Open ID](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-obtain-openid) 获取 <br>**示例值**："ou_da5****************dfe"
text_style | rich_text_element_text_style | 否 | 文字属性
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小，单位 px，默认为 14 px<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_doc_element | rich_text_element_mention_doc | 否 | 文档类别信息（当前元素为文档类别时候需要有当前字段）
doc_url | string | 是 | 文档超链接<br>**示例值**："https://bytedance.larkoffice.com/wiki/xxxxx"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重（regular：常规，bold：加粗）<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
indent | int | 否 | 缩进（单位：字符）<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
list_begin_index | int | 否 | 有序列表开始序号(第一个有序列表的序号为list_begin_index+1)<br>例如：list_begin_index = 0， 则第一个有序列表的序号为1<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `10000`
quote | boolean | 否 | 引用<br>**示例值**：true
text_color_type | int | 否 | 文字颜色类型，0=系统颜色（取theme_text_color_code），1=自定义颜色（取text_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
text_background_color_type | int | 否 | 文字背景颜色类型，0=系统颜色（取theme_text_background_color_code），1=自定义颜色（取text_background_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
style | style | 否 | 图形样式(节点类型为group、paint时，该字段必须为空)
fill_color | string | 否 | 填充颜色，16 进制 rbg 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
fill_opacity | number(float) | 否 | 填充透明度，百分比<br>**示例值**：50<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
border_style | string | 否 | 边框样式<br>**示例值**："solid"<br>**可选值有**：<br>- solid：实线<br>- none：无边框<br>- dash：虚线<br>- dot：点状虚线
border_width | string | 否 | 边框宽度<br>**示例值**："narrow"<br>**可选值有**：<br>- extra_narrow：极细<br>- narrow：细<br>- medium：中<br>- bold：粗
border_opacity | number(float) | 否 | 边框透明度，百分比<br>**示例值**：50<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
h_flip | boolean | 否 | 水平翻折<br>**示例值**：false
v_flip | boolean | 否 | 垂直翻折<br>**示例值**：false
border_color | string | 否 | 边框颜色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
theme_fill_color_code | int | 否 | 填充颜色主题配色编码值<br>**示例值**：3<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
theme_border_color_code | int | 否 | 边框颜色主题配色编码值<br>**示例值**：4<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
fill_color_type | int | 否 | 填充颜色类型：0=系统颜色，取theme_fill_color_code，1=自定义颜色，取fill_color<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
border_color_type | int | 否 | 边框颜色类型：0=系统颜色，取theme_border_color_code，1=自定义颜色，取border_color<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
image | image | 否 | 图片
token | string | 是 | 图片 token，通过云文档下的素材上传接口 [上传素材](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/media/upload_all) 上传图片后返回的 token<br>**示例值**："EeSHb3qs9oSBXoxvw33bqtOsczb"<br>**数据校验规则**：<br>- 长度范围：`22` ～ `27` 字符
composite_shape | composite_shape | 否 | 基础图形属性(节点的 type 为 composite_shape 时不可为空)
type | string | 是 | 基础图形的具体类型<br>**示例值**："circle"<br>**可选值有**：<br>- round_rect2：全圆角矩形<br>- ellipse：圆形<br>- hexagon：六边形<br>- cylinder：圆柱体<br>- parallelogram：平行四边形<br>- trapezoid：梯形<br>- triangle：三角形<br>- round_rect：圆角矩形<br>- step：步骤<br>- diamond：菱形<br>- rect：基础矩形<br>- star：五角星<br>- bubble：气泡<br>- pentagon：五边形<br>- forward_arrow：单向箭头<br>- document_shape：文档图形<br>- condition_shape：组合片段<br>- cloud：云朵<br>- cross：十字形<br>- step2：步骤图形2<br>- predefined_process：预定义流程<br>- delay_shape：延迟图形<br>- off_page_connector：跨页引用<br>- note_shape：注释图形<br>- data_process：数据处理<br>- data_store：数据存储<br>- data_store2：数据存储2<br>- data_store3：数据存储3<br>- star2：爆炸星型<br>- star3：四角形<br>- star4：六角形<br>- actor：角色小人<br>- brace：花括号<br>- condition_shape2：组合片段2<br>- double_arrow：双向箭头<br>- data_flow_round_rect3：数据处理（正方圆角矩形）<br>- rect_bubble：矩形气泡<br>- manual_input：手动输入图形<br>- flow_chart_round_rect：流程图圆角矩形<br>- flow_chart_round_rect2：流程图全圆角矩形<br>- flow_chart_diamond：流程图判定<br>- flow_chart_parallelogram：流程图数据<br>- flow_chart_cylinder：流程图数据库<br>- flow_chart_trapezoid：流程图手动操作<br>- flow_chart_hexagon：流程图准备<br>- data_flow_round_rect：数据流外部实体<br>- data_flow_ellipse：数据流数据处理<br>- backward_arrow：反向箭头（左箭头）<br>- brace_reverse：反向花括号（左括号）<br>- flow_chart_mq：消息队列<br>- horiz_cylinder：水平方向圆柱体<br>- class_interface：类图，接口<br>- classifier：类图，类目<br>- circular_ring：圆环<br>- pie：扇形<br>- right_triangle：直角三角形<br>- octagon：八边形<br>- state_start：状态图，开始<br>- state_end：状态图，结束<br>- state_concurrence：状态图，并发<br>- component_shape：组件<br>- component_shape2：组件2<br>- component_interface：组件，接口<br>- component_required_interface：组件，需求接口<br>- component_assembly：组件，组装<br>- cube：立方体<br>- boundary：边界<br>- control：控制<br>- entity：实体<br>- data_base：数据库<br>- boundary：边界<br>- queue：队列<br>- collection：集合<br>- actor_lifeline：角色生命线<br>- object_lifeline：对象生命线<br>- mind_node_full_round_rect：思维导图全圆角矩形<br>- mind_node_round_rect：思维导图圆角矩形<br>- mind_node_text：思维导图文本图形
pie | pie | 否 | 饼图属性，type=pie时需要设置
start_radial_line_angle | number(float) | 是 | 开始径向边角度，水平向右x轴正方向为0度，顺时针方向角度值递增，单位度<br>**示例值**：30.0<br>**数据校验规则**：<br>- 取值范围：`0.0` ～ `360.0`
central_angle | number(float) | 是 | 圆心角角度，角度方向为始径向边逆时针方向，单位度<br>**示例值**：40.0<br>**数据校验规则**：<br>- 取值范围：`0.0` ～ `360.0`
radius | number(float) | 是 | 半径长度，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`1` ～ `10000000000`
sector_ratio | number(float) | 否 | 扇区占比，0为一个圆周线，1为一个圆盘<br>**示例值**：1<br>**默认值**：`1`<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1`
circular_ring | pie | 否 | 圆环属性，type=circular_ring时需要设置
start_radial_line_angle | number(float) | 是 | 开始径向边角度，水平向右x轴正方向为0度，顺时针方向角度值递增<br>**示例值**：30.0<br>**数据校验规则**：<br>- 取值范围：`0.0` ～ `360.0`
central_angle | number(float) | 是 | 圆心角角度，角度方向为始径向边逆时针方向<br>**示例值**：40.0<br>**数据校验规则**：<br>- 取值范围：`0.0` ～ `360.0`
radius | number(float) | 是 | 半径长度<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`1` ～ `10000000000`
sector_ratio | number(float) | 否 | 扇区占比，0为一个圆周线，1为一个圆盘<br>**示例值**：1<br>**默认值**：`1`<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1`
connector | connector | 否 | 连线属性(节点类型为connector时，该字段不可为空)
start | connector_info | 否 | 连线端点信息
attached_object | connector.attached_object | 否 | 连接图形信息，与position参数二选一，同时设置时attached_object生效
id | string | 否 | 连接图形的 id<br>**示例值**："o1:1"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
snap_to | string | 否 | 连接图形的方向<br>**示例值**："auto"<br>**可选值有**：<br>- auto：连接方向自动匹配<br>- top：连接图形顶部方向<br>- right：连接图形右边方向<br>- bottom：连接图形底部方向<br>- left：连接图形左边方向<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
position | point | 否 | 连接图形的相对坐标，0-1
x | number(float) | 否 | 点位置x坐标，单位百分比<br>**示例值**：0.5<br>**数据校验规则**：<br>- 取值范围：`-1000000000000000000` ～ `1000000000000000000`
y | number(float) | 否 | 点位置y坐标，单位百分比<br>**示例值**：0.5<br>**数据校验规则**：<br>- 取值范围：`-10000000000000000000` ～ `100000000000000000000`
position | point | 否 | 连线端点在画布内的坐标，position与attached_object二选一，position与attached_object 同时设置时 attched_object 生效
x | number(float) | 否 | 点位置x坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-1000000000000000000` ～ `1000000000000000000`
y | number(float) | 否 | 点位置y坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-10000000000000000000` ～ `100000000000000000000`
arrow_style | string | 否 | 连线端点箭头样式<br>**示例值**："line_arrow"<br>**可选值有**：<br>- none：无箭头样式<br>- line_arrow：线型箭头<br>- triangle_arrow：三角形箭头<br>- empty_triangle_arrow：空心三角形箭头<br>- circle_arrow：圆形箭头<br>- empty_circle_arrow：空心圆形箭头<br>- diamond_arrow：菱形箭头<br>- empty_diamond_arrow：空心菱形箭头<br>- single_arrow：单箭头<br>- multi_arrow：多箭头<br>- exact_single_arrow：精确单箭头<br>- zero_or_multi_arrow：零个或多个箭头<br>- zero_or_single_arrow：零个或单个箭头<br>- single_or_multi_arrow：单个或多个箭头<br>- x_arrow：x型箭头<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
end | connector_info | 否 | 连线端点信息
attached_object | connector.attached_object | 否 | 连接图形信息，与position参数二选一，同时设置时attached_object生效
id | string | 否 | 连接图形的 id<br>**示例值**："o1:1"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
snap_to | string | 否 | 连接图形的方向<br>**示例值**："auto"<br>**可选值有**：<br>- auto：连接方向自动匹配<br>- top：连接图形顶部方向<br>- right：连接图形右边方向<br>- bottom：连接图形底部方向<br>- left：连接图形左边方向<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
position | point | 否 | 连接图形的相对坐标，0-1
x | number(float) | 否 | 点位置x坐标，单位百分比<br>**示例值**：0.5<br>**数据校验规则**：<br>- 取值范围：`-1000000000000000000` ～ `1000000000000000000`
y | number(float) | 否 | 点位置y坐标，单位百分比<br>**示例值**：0.5<br>**数据校验规则**：<br>- 取值范围：`-10000000000000000000` ～ `100000000000000000000`
position | point | 否 | 连线端点在画布内的坐标，position与attached_object二选一，position与attached_object 同时设置时 attached_object 生效
x | number(float) | 否 | 点位置x坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-1000000000000000000` ～ `1000000000000000000`
y | number(float) | 否 | 点位置y坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-10000000000000000000` ～ `100000000000000000000`
arrow_style | string | 否 | 连线端点箭头样式<br>**示例值**："line_arrow"<br>**可选值有**：<br>- none：无箭头样式<br>- line_arrow：线型箭头<br>- triangle_arrow：三角形箭头<br>- empty_triangle_arrow：空心三角形箭头<br>- circle_arrow：圆形箭头<br>- empty_circle_arrow：空心圆形箭头<br>- diamond_arrow：菱形箭头<br>- empty_diamond_arrow：空心菱形箭头<br>- single_arrow：单箭头<br>- multi_arrow：多箭头<br>- exact_single_arrow：精确单箭头<br>- zero_or_multi_arrow：零个或多个箭头<br>- zero_or_single_arrow：零个或单个箭头<br>- single_or_multi_arrow：单个或多个箭头<br>- x_arrow：x型箭头<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
captions | connector.caption | 否 | 连线文本
data | text\[\] | 否 | 文本<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1024`
text | string | 否 | 文字内容<br>**示例值**："文字内容"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1024` 字符
font_weight | string | 否 | 文字字重<br>**示例值**："regular"<br>**可选值有**：<br>- regular：常规<br>- bold：加粗
font_size | int | 否 | 文字大小，单位 px，默认为 14 px<br>**示例值**：14
horizontal_align | string | 否 | 水平对齐<br>**示例值**："center"<br>**可选值有**：<br>- left：向左对齐<br>- center：居中对齐<br>- right：向右对齐
vertical_align | string | 否 | 垂直对齐<br>**示例值**："mid"<br>**可选值有**：<br>- top：顶部对齐<br>- mid：垂直居中<br>- bottom：底部对齐
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
angle | int | 否 | 文字旋转角度，单位度<br>**示例值**：90<br>**可选值有**：<br>- 0：文字旋转角度0度<br>- 90：文字旋转角度90度<br>- 180：文字旋转角度180度<br>- 270：文字旋转角度270度<br>**数据校验规则**：<br>- 取值范围：`0` ～ `270`
theme_text_color_code | int | 否 | 文字颜色主题配色编码值<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
theme_text_background_color_code | int | 否 | 文字背景颜色主题配色编码值<br>**示例值**：-1<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
rich_text | rich_text | 否 | 富文本
paragraphs | rich_text_paragraph\[\] | 否 | 段落列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
paragraph_type | int | 是 | 段落类别<br>**示例值**：0<br>**可选值有**：<br>- 0：普通段落<br>- 1：无序列表<br>- 2：有序列表
elements | rich_text_element\[\] | 否 | 元素列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
element_type | int | 是 | 元素类别<br>**示例值**：0<br>**可选值有**：<br>- 0：文本<br>- 1：链接<br>- 2：@用户<br>- 3：文档
text_element | rich_text_element_text | 否 | 文本类别信息
text | string | 是 | 文字<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
link_element | rich_text_element_link | 否 | 超链接类别信息
herf | string | 是 | 链接<br>**示例值**："https://bytedance.larkoffice.com"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text | string | 否 | 文字<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_user_element | rich_text_element_mention_user | 否 | @用户类别信息
user_id | string | 是 | 用户openID，可通过 [获取指定用户的 Open ID](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-obtain-openid) 获取 <br>**示例值**："ou_da5****************dfe"
text_style | rich_text_element_text_style | 否 | 文字属性
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_doc_element | rich_text_element_mention_doc | 否 | 文档类别信息
doc_url | string | 是 | 文档超链接<br>**示例值**："https://bytedance.larkoffice.com/wiki/xxxxx"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
indent | int | 否 | 缩进（单位：字符）<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
list_begin_index | int | 否 | 有序列表开始序号(第一个有序列表的序号为list_begin_index+1)<br>例如：list_begin_index = 0， 则第一个有序列表的序号为1<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `10000`
quote | boolean | 否 | 引用<br>**示例值**：true
text_color_type | int | 否 | 文字颜色类型，0=系统颜色（取theme_text_color_code），1=自定义颜色（取text_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
text_background_color_type | int | 否 | 文字背景颜色类型，0=系统颜色（取theme_text_background_color_code），1=自定义颜色（取text_background_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
shape | string | 否 | 连线类型<br>**示例值**："straight"<br>**可选值有**：<br>- straight：直线<br>- polyline：折线<br>- curve：曲线<br>- right_angled_polyline：直角折线<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
turning_points | point\[\] | 否 | 连线转向点<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000000`
x | number(float) | 否 | 点位置x坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-1000000000000000000` ～ `1000000000000000000`
y | number(float) | 否 | 点位置y坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-10000000000000000000` ～ `100000000000000000000`
caption_auto_direction | boolean | 否 | 连线上的文本方向是否自动跟随连线方向<br>**示例值**：true
caption_position | number(float) | 否 | 文本在连线上的相对位置，范围0-1，0表示在连线的起始点，1表示在连线的终点<br>**示例值**：0.5<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1`
width | number(float) | 否 | 图形宽度，单位为 px<br>**示例值**：100<br>**数据校验规则**：<br>- 取值范围：`0` ～ `132070`
section | section | 否 | 分区属性
title | string | 否 | 分区标题<br>**示例值**："分区"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1024` 字符
table | table | 否 | 表格属性(节点类型为table、table_er、table_uml、combined_fragment时，该字段不可为空)
meta | table.meta | 是 | 元信息
row_sizes | number(float)\[\] | 否 | 行高，单位 px<br>**示例值**：[10]<br>**数据校验规则**：<br>- 长度范围：`1` ～ `100`
col_sizes | number(float)\[\] | 否 | 列宽，单位 px<br>**示例值**：[10]<br>**数据校验规则**：<br>- 长度范围：`1` ～ `1000000`
style | style | 否 | 整个表格的样式
fill_color | string | 否 | 填充颜色，16 进制 rbg 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
fill_opacity | number(float) | 否 | 填充透明度，百分比<br>**示例值**：50<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
border_style | string | 否 | 边框样式<br>**示例值**："solid"<br>**可选值有**：<br>- solid：实线<br>- none：无边框<br>- dash：虚线<br>- dot：点状虚线
border_width | string | 否 | 边框宽度<br>**示例值**："narrow"<br>**可选值有**：<br>- extra_narrow：极细<br>- narrow：细<br>- medium：中<br>- bold：粗
border_opacity | number(float) | 否 | 边框透明度，百分比<br>**示例值**：50<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
h_flip | boolean | 否 | 水平翻折<br>**示例值**：false
v_flip | boolean | 否 | 垂直翻折<br>**示例值**：false
border_color | string | 否 | 边框颜色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
theme_fill_color_code | int | 否 | 填充颜色主题配色编码值<br>**示例值**：3<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
theme_border_color_code | int | 否 | 边框颜色主题配色编码值<br>**示例值**：4<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
fill_color_type | int | 否 | 填充颜色类型：0=系统颜色，取theme_fill_color_code，1=自定义颜色，取fill_color<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
border_color_type | int | 否 | 边框颜色类型：0=系统颜色，取theme_border_color_code，1=自定义颜色，取border_color<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
text | text | 否 | 整个表格的文字样式
text | string | 否 | 文字内容<br>**示例值**："文字内容"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1024` 字符
font_weight | string | 否 | 文字字重<br>**示例值**："regular"<br>**可选值有**：<br>- regular：常规<br>- bold：加粗
font_size | int | 否 | 文字大小，单位 px，默认为 14 px<br>**示例值**：14
horizontal_align | string | 否 | 水平对齐<br>**示例值**："center"<br>**可选值有**：<br>- left：向左对齐<br>- center：居中对齐<br>- right：向右对齐
vertical_align | string | 否 | 垂直对齐<br>**示例值**："mid"<br>**可选值有**：<br>- top：顶部对齐<br>- mid：垂直居中<br>- bottom：底部对齐
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
angle | int | 否 | 文字旋转角度，单位度<br>**示例值**：90<br>**可选值有**：<br>- 0：文字旋转角度0度<br>- 90：文字旋转角度90度<br>- 180：文字旋转角度180度<br>- 270：文字旋转角度270度<br>**数据校验规则**：<br>- 取值范围：`0` ～ `270`
theme_text_color_code | int | 否 | 文字颜色主题配色编码值<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
theme_text_background_color_code | int | 否 | 文字背景颜色主题配色编码值<br>**示例值**：-1<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
rich_text | rich_text | 否 | 富文本
paragraphs | rich_text_paragraph\[\] | 否 | 段落列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
paragraph_type | int | 是 | 段落类别<br>**示例值**：0<br>**可选值有**：<br>- 0：普通段落<br>- 1：无序列表<br>- 2：有序列表
elements | rich_text_element\[\] | 否 | 元素列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
element_type | int | 是 | 元素类别<br>**示例值**：0<br>**可选值有**：<br>- 0：文本<br>- 1：链接<br>- 2：@用户<br>- 3：文档
text_element | rich_text_element_text | 否 | 文本类别信息
text | string | 是 | 文字<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
link_element | rich_text_element_link | 否 | 超链接类别信息
herf | string | 是 | 链接<br>**示例值**："https://bytedance.larkoffice.com"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text | string | 否 | 文字<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_user_element | rich_text_element_mention_user | 否 | @用户类别信息
user_id | string | 是 | 用户openID，可通过 [获取指定用户的 Open ID](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-obtain-openid) 获取 <br>**示例值**："ou_da5****************dfe"
text_style | rich_text_element_text_style | 否 | 文字属性
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_doc_element | rich_text_element_mention_doc | 否 | 文档类别信息
doc_url | string | 是 | 文档超链接<br>**示例值**："https://bytedance.larkoffice.com/wiki/xxxxx"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
indent | int | 否 | 缩进（单位：字符）<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
list_begin_index | int | 否 | 有序列表开始序号(第一个有序列表的序号为list_begin_index+1)<br>例如：list_begin_index = 0， 则第一个有序列表的序号为1<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `10000`
quote | boolean | 否 | 引用<br>**示例值**：true
text_color_type | int | 否 | 文字颜色类型，0=系统颜色（取theme_text_color_code），1=自定义颜色（取text_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
text_background_color_type | int | 否 | 文字背景颜色类型，0=系统颜色（取theme_text_background_color_code），1=自定义颜色（取text_background_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
title | string | 否 | 标题<br>**示例值**："表格"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1024` 字符
cells | table.cell\[\] | 否 | 单元格列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000`
row_index | int | 是 | 行下标，从 1 开始<br>**示例值**：1<br>**数据校验规则**：<br>- 取值范围：`1` ～ `10000`
col_index | int | 是 | 列下标，从 1 开始<br>**示例值**：1<br>**数据校验规则**：<br>- 取值范围：`1` ～ `10000`
merge_info | table.cell.merge_info | 否 | 单元格合并信息
row_span | int | 是 | 从当前行索引起被合并的连续行数<br>**示例值**：2<br>**数据校验规则**：<br>- 取值范围：`1` ～ `10000`
col_span | int | 是 | 从当前列索引起被合并的连续列数<br>**示例值**：2<br>**数据校验规则**：<br>- 取值范围：`1` ～ `10000`
children | string\[\] | 否 | 单元格包含的子节点 id<br>**示例值**：["o1:1"]<br>**数据校验规则**：<br>- 长度范围：`0` ～ `3000`
text | text | 否 | 单元格内文字
text | string | 否 | 文字内容<br>**示例值**："文字内容"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1024` 字符
font_weight | string | 否 | 文字字重<br>**示例值**："regular"<br>**可选值有**：<br>- regular：常规<br>- bold：加粗
font_size | int | 否 | 文字大小，单位 px，默认为 14 px<br>**示例值**：14
horizontal_align | string | 否 | 水平对齐<br>**示例值**："center"<br>**可选值有**：<br>- left：向左对齐<br>- center：居中对齐<br>- right：向右对齐
vertical_align | string | 否 | 垂直对齐<br>**示例值**："mid"<br>**可选值有**：<br>- top：顶部对齐<br>- mid：垂直居中<br>- bottom：底部对齐
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
angle | int | 否 | 文字旋转角度，单位度<br>**示例值**：90<br>**可选值有**：<br>- 0：文字旋转角度0度<br>- 90：文字旋转角度90度<br>- 180：文字旋转角度180度<br>- 270：文字旋转角度270度<br>**数据校验规则**：<br>- 取值范围：`0` ～ `270`
theme_text_color_code | int | 否 | 文字颜色主题配色编码值<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
theme_text_background_color_code | int | 否 | 文字背景颜色主题配色编码值<br>**示例值**：-1<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
rich_text | rich_text | 否 | 富文本
paragraphs | rich_text_paragraph\[\] | 否 | 段落列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
paragraph_type | int | 是 | 段落类别<br>**示例值**：0<br>**可选值有**：<br>- 0：普通段落<br>- 1：无序列表<br>- 2：有序列表
elements | rich_text_element\[\] | 否 | 元素列表<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000`
element_type | int | 是 | 元素类别<br>**示例值**：0<br>**可选值有**：<br>- 0：文本<br>- 1：链接<br>- 2：@用户<br>- 3：文档
text_element | rich_text_element_text | 否 | 文本类别信息
text | string | 是 | 文字<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
link_element | rich_text_element_link | 否 | 超链接类别信息
herf | string | 是 | 链接<br>**示例值**："https://bytedance.larkoffice.com"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text | string | 否 | 文字<br>**示例值**："示例文案"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_user_element | rich_text_element_mention_user | 否 | @用户类别信息
user_id | string | 是 | 用户openID，可通过 [获取指定用户的 Open ID](https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-obtain-openid) 获取 <br>**示例值**："ou_da5****************dfe"
text_style | rich_text_element_text_style | 否 | 文字属性
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
mention_doc_element | rich_text_element_mention_doc | 否 | 文档类别信息
doc_url | string | 是 | 文档超链接<br>**示例值**："https://bytedance.larkoffice.com/wiki/xxxxx"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000` 字符
text_style | rich_text_element_text_style | 否 | 文字样式
font_weight | string | 否 | 文字字重<br>**示例值**："bold"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10` 字符
font_size | int | 否 | 文字大小<br>**示例值**：14<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000`
text_color | string | 否 | 文字颜色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
text_background_color | string | 否 | 文字背景色，16 进制 rgb 值<br>**示例值**："#000000"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
line_through | boolean | 否 | 是否存在删除线<br>**示例值**：true
underline | boolean | 否 | 是否存在下划线<br>**示例值**：true
italic | boolean | 否 | 是否斜体<br>**示例值**：true
indent | int | 否 | 缩进（单位：字符）<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
list_begin_index | int | 否 | 有序列表开始序号(第一个有序列表的序号为list_begin_index+1)<br>例如：list_begin_index = 0， 则第一个有序列表的序号为1<br>**示例值**：0<br>**数据校验规则**：<br>- 取值范围：`0` ～ `10000`
quote | boolean | 否 | 引用<br>**示例值**：true
text_color_type | int | 否 | 文字颜色类型，0=系统颜色（取theme_text_color_code），1=自定义颜色（取text_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
text_background_color_type | int | 否 | 文字背景颜色类型，0=系统颜色（取theme_text_background_color_code），1=自定义颜色（取text_background_color）<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
style | style | 否 | 单元格样式，设置后会覆盖表格样式
fill_color | string | 否 | 填充颜色，16 进制 rbg 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
fill_opacity | number(float) | 否 | 填充透明度，百分比<br>**示例值**：50<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
border_style | string | 否 | 边框样式<br>**示例值**："solid"<br>**可选值有**：<br>- solid：实线<br>- none：无边框<br>- dash：虚线<br>- dot：点状虚线
border_width | string | 否 | 边框宽度<br>**示例值**："narrow"<br>**可选值有**：<br>- extra_narrow：极细<br>- narrow：细<br>- medium：中<br>- bold：粗
border_opacity | number(float) | 否 | 边框透明度，百分比<br>**示例值**：50<br>**数据校验规则**：<br>- 取值范围：`0` ～ `100`
h_flip | boolean | 否 | 水平翻折<br>**示例值**：false
v_flip | boolean | 否 | 垂直翻折<br>**示例值**：false
border_color | string | 否 | 边框颜色，16 进制 rgb 值<br>**示例值**："#6db5a3"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `10` 字符
theme_fill_color_code | int | 否 | 填充颜色主题配色编码值<br>**示例值**：3<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
theme_border_color_code | int | 否 | 边框颜色主题配色编码值<br>**示例值**：4<br>**数据校验规则**：<br>- 取值范围：`-2` ～ `100`
fill_color_type | int | 否 | 填充颜色类型：0=系统颜色，取theme_fill_color_code，1=自定义颜色，取fill_color<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
border_color_type | int | 否 | 边框颜色类型：0=系统颜色，取theme_border_color_code，1=自定义颜色，取border_color<br>**示例值**：0<br>**可选值有**：<br>- 0：系统颜色<br>- 1：自定义颜色
locked | boolean | 否 | 图形是否锁定，锁定后无法编辑图形，默认false<br>**示例值**：true
z_index | int | 否 | 图形在兄弟节点中的层级，层级大的会覆盖层级小的<br>**示例值**：1<br>**数据校验规则**：<br>- 取值范围：`0` ～ `10000`
lifeline | lifeline | 否 | 生命对象属性(节点类型为life_line时，该字段不可为空)
size | number(float) | 否 | 生命线长度，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`0` ～ `1000000000`
type | string | 否 | 生命线类型<br>**示例值**："actor_lifeline"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `1000000` 字符
paint | paint | 否 | 画笔属性(节点类型为paint时，该字段不可为空)
type | string | 否 | 画笔类型<br>**示例值**："marker"<br>**可选值有**：<br>- marker：马克笔<br>- highlight：高亮笔<br>**数据校验规则**：<br>- 长度范围：`0` ～ `100` 字符
lines | point\[\] | 否 | 画板线段，由系列坐标点表示<br>**数据校验规则**：<br>- 长度范围：`1` ～ `100000000`
x | number(float) | 否 | 点位置x坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-1000000000000000000` ～ `1000000000000000000`
y | number(float) | 否 | 点位置y坐标，单位 px<br>**示例值**：10<br>**数据校验规则**：<br>- 取值范围：`-10000000000000000000` ～ `100000000000000000000`
width | int | 否 | 画笔粗细，单位px<br>**示例值**：7<br>**数据校验规则**：<br>- 取值范围：`1` ～ `23`
color | string | 否 | 画笔颜色<br>**示例值**："#ffffff"<br>**数据校验规则**：<br>- 长度范围：`7` ～ `7` 字符
svg | svg | 否 | svg图形属性(节点类型为svg时，该字段不可为空)
svg_code | string | 否 | svg 代码<br>**示例值**："<svg width="100" height="100"><circle cx="50" cy="50" r="40" stroke="black" stroke-width="2" fill="red" /></svg>"<br>**数据校验规则**：<br>- 长度范围：`1` ～ `10000000000000000` 字符
sticky_note | sticky_note | 否 | 便签图形属性
user_id | string | 否 | 用户id<br>**示例值**："12345678"
show_author_info | boolean | 否 | 是否展示用户信息<br>**示例值**：true
mind_map_node | mind_map_node | 否 | 思维导图节点属性
parent_id | string | 是 | 思维导图节点的父节点，必须为思维导图节点<br>**示例值**："z1:1"<br>**数据校验规则**：<br>- 长度范围：`0` ～ `10000000` 字符
type | string | 否 | 思维导图节点图形类型<br>**示例值**："mind_map_text"<br>**可选值有**：<br>- mind_map_text：思维导图文本节点类型<br>- mind_map_full_round_rect：思维导图全圆角矩形节点类型<br>- mind_map_round_rect：思维导图矩形节点类型
z_index | int | 否 | 思维导图节点在兄弟节点中的位置index<br>**示例值**：2<br>**数据校验规则**：<br>- 取值范围：`0` ～ `10000`
layout_position | string | 否 | 子节点相对根节点的方向（根节点下的子节点设置才生效）<br>**示例值**："left"<br>**可选值有**：<br>- left：思维导图节点在根节点左侧<br>- right：思维导图节点在根节点右侧<br>- up：思维导图节点在根节点上方<br>- down：思维导图节点在根节点下方
collapsed | boolean | 否 | 是否收起子节点<br>**示例值**：true
mind_map_root | mind_map_root | 否 | 思维导图根节点属性
layout | string | 否 | 思维导图布局方式<br>**示例值**："left_right"<br>**可选值有**：<br>- up_down：上下布局<br>- left_right：左右布局<br>- tree_left：左树布局<br>- tree_right：右树布局<br>- tree_balance：左右交替平衡树布局<br>- vertical_time_line：垂直时间线布局<br>- horizontal_time_line：水平时间线布局
type | string | 否 | 思维导图根节点图形类型<br>**示例值**："mind_map_round_rect"<br>**可选值有**：<br>- mind_map_text：思维导图文本节点类型<br>- mind_map_full_round_rect：思维导图全圆角矩形节点类型<br>- mind_map_round_rect：思维导图矩形节点类型
line_style | string | 否 | 思维导图图形连接线样式<br>**示例值**："round_angle"<br>**可选值有**：<br>- curve：曲线<br>- right_angle：直角折线<br>- round_angle：圆角折线

### 请求体示例
```json
{
    "nodes": [
        {
            "id": "o1:3",
            "type": "composite_shape",
            "parent_id": "n1:1",
            "x": 347.1015625,
            "y": 267.15234375,
            "angle": 0,
            "height": 61.796875,
            "text": {
                "text": "基础图形",
                "font_weight": "regular",
                "font_size": 14,
                "horizontal_align": "center",
                "vertical_align": "mid",
                "text_color": "#1f2329",
                "line_through": false,
                "underline": false,
                "italic": false,
                "angle": 0,
                "theme_text_color_code": -1,
                "theme_text_background_color_code": -1,
                "text_color_type": 0,
                "text_background_color_type": 0
            },
            "style": {
                "fill_color": "#e1eaff",
                "fill_opacity": 100,
                "border_width": "narrow",
                "border_color": "#4e83fd",
                "border_opacity": 100,
                "border_style": "solid",
                "theme_fill_color_code": -1,
                "theme_border_color_code": -1,
                "fill_color_type": 0,
                "border_color_type": 0
            },
            "composite_shape": {
                "type": "round_rect"
            },
            "width": 114,
            "z_index": 0
        },
        {
            "id": "a1:1",
            "type": "text_shape",
            "x": 596.00390625,
            "y": 287.914794921875,
            "angle": 0,
            "height": 20.27199935913086,
            "text": {
                "text": "文本图形",
                "font_weight": "regular",
                "font_size": 14,
                "horizontal_align": "left",
                "vertical_align": "top",
                "text_color": "#1f2329",
                "line_through": false,
                "underline": false,
                "italic": false,
                "angle": 0,
                "theme_text_color_code": -1,
                "theme_text_background_color_code": -1,
                "text_color_type": 0,
                "text_background_color_type": 0
            },
            "width": 56,
            "z_index": 0
        },
        {
            "id": "c1:1",
            "type": "connector",
            "x": 461.1015625,
            "y": 298.05078125,
            "angle": 0,
            "height": 0,
            "style": {
                "border_width": "narrow",
                "border_color": "#bbbfc4",
                "border_opacity": 100,
                "border_style": "solid",
                "theme_border_color_code": -1,
                "border_color_type": 0
            },
            "connector": {
                "start_object": {
                    "id": "o1:3",
                    "snap_to": "right",
                    "position": {
                        "x": 1,
                        "y": 0.5
                    }
                },
                "end_object": {
                    "id": "a1:1",
                    "snap_to": "left",
                    "position": {
                        "x": 0,
                        "y": 0.49999934434890747
                    }
                },
                "captions": {
                    "data": [
                        {
                            "text": "连线",
                            "font_weight": "regular",
                            "font_size": 14,
                            "horizontal_align": "center",
                            "vertical_align": "mid",
                            "text_color": "#1f2329",
                            "line_through": false,
                            "underline": false,
                            "italic": false,
                            "angle": 0,
                            "theme_text_color_code": -1,
                            "theme_text_background_color_code": -1,
                            "text_color_type": 0,
                            "text_background_color_type": 0
                        }
                    ]
                },
                "shape": "straight",
                "turning_points": [],
                "start": {
                    "attached_object": {
                        "id": "o1:3",
                        "snap_to": "right",
                        "position": {
                            "x": 1,
                            "y": 0.5
                        }
                    },
                    "arrow_style": "none"
                },
                "end": {
                    "attached_object": {
                        "id": "a1:1",
                        "snap_to": "left",
                        "position": {
                            "x": 0,
                            "y": 0.49999934434890747
                        }
                    },
                    "arrow_style": "line_arrow"
                },
                "caption_auto_direction": false,
                "caption_position": 0.5051159262657166,
                "specified_coordinate": true,
                "caption_position_type": 0
            },
            "width": 134.90234375,
            "z_index": 1
        },
        {
            "id": "t1:2",
            "type": "section",
            "x": 685.78125,
            "y": 196.0703125,
            "angle": 0,
            "height": 151.83984375,
            "style": {
                "fill_color": "#f5f6f7",
                "fill_opacity": 100,
                "border_width": "extra_narrow",
                "border_opacity": 100,
                "border_style": "solid",
                "theme_fill_color_code": -1,
                "theme_border_color_code": -1,
                "fill_color_type": 0,
                "border_color_type": 0
            },
            "width": 331.6875,
            "section": {
                "title": "分区"
            },
            "z_index": 2
        },
        {
            "id": "n1:1",
            "type": "group",
            "x": 344.1015625,
            "y": 159.76953125,
            "angle": 0,
            "height": 169.17999267578125,
            "width": 120,
            "z_index": 3
        },
        {
            "id": "o1:7",
            "type": "composite_shape",
            "parent_id": "n1:1",
            "x": 344.1015625,
            "y": 159.76953125,
            "angle": 0,
            "height": 80,
            "text": {
                "text": "椭圆",
                "font_weight": "regular",
                "font_size": 14,
                "horizontal_align": "center",
                "vertical_align": "mid",
                "text_color": "#1f2329",
                "line_through": false,
                "underline": false,
                "italic": false,
                "angle": 0,
                "theme_text_color_code": -1,
                "theme_text_background_color_code": -1,
                "text_color_type": 0,
                "text_background_color_type": 0
            },
            "style": {
                "fill_color": "#eff0f1",
                "fill_opacity": 100,
                "border_width": "narrow",
                "border_color": "#bbbfc4",
                "border_opacity": 100,
                "border_style": "solid",
                "theme_fill_color_code": -1,
                "theme_border_color_code": -1,
                "fill_color_type": 0,
                "border_color_type": 0
            },
            "composite_shape": {
                "type": "round_rect2"
            },
            "width": 120,
            "z_index": 1
        }
    ]
}
```

## 响应

### 响应体

名称 | 类型 | 描述
---|---|---
code | int | 错误码，非 0 表示失败
msg | string | 错误描述
data | \- | \-
ids | string\[\] | 所创建的节点 id 列表
client_token | string | 操作的唯一标识，更新请求中使用此值表示幂等的进行此次更新

### 响应体示例
```json
{
    "code": 0,
    "msg": "success",
    "data": {
        "ids": [
            "o2:27",
            "a2:4",
            "c2:10",
            "t2:8",
            "n2:4",
            "o2:28"
        ],
        "client_token": "fe599b60-450f-46ff-b2ef-9f6675625b97"
    }
}
```

### 错误码

HTTP状态码 | 错误码 | 描述 | 排查建议
---|---|---|---
400 | 2890001 | invalid format | 参数格式不正确。请检查传入的参数格式，如 json 字符串是否正确
400 | 2890002 | invalid arg | 参数无效。请检查传入的参数是否有效，如 whiteboard_id 是否正确，可通过云文档下的文档接口[获取文档所有块](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/document-docx/docx-v1/document-block/list)获取正确的`whiteboard_id`
400 | 2890003 | record missing | 找不到记录。whiteboard_id 不存在，请检查传入的whiteboard_id是否正确；或通过云文档下的文档接口[获取文档所有块](https://open.feishu.cn/document/ukTMukTMukTM/uUDN04SN0QjL1QDN/document-docx/docx-v1/document-block/list)确认该画板是否存在
401 | 2890004 | auth failed | 认证失败。请检查 Authorization 参数
403 | 2890005 | forbidden | 请求身份没有当前画板的阅读权限。请参考[云文档常见问题 3](https://open.feishu.cn/document/ukTMukTMukTM/uczNzUjL3czM14yN3MTN#78a03ee2)开通权限
429 | 2890006 | too many request | 请求超过接口频率限流值。请降低操作频率，稍后再试
500 | 2891001 | server internal error | 服务运行错误。请重试或联系技术支持（私有链接已移除）

