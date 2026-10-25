"""SVG 绘图小工具：CJK 宽度估算、换行、圆角卡片、折线箭头、图例。

坐标单位为 px，画布宽度默认 1120，输出可直接内联到 HTML。
"""

from __future__ import annotations

from html import escape

# ---------------------------------------------------------------- 调色板
INK = "#17222f"
MUTED = "#5b6a7d"
FAINT = "#8794a5"
LINE = "#d8e0ea"
BG = "#ffffff"
BG_SOFT = "#f6f8fb"

PALETTE = {
    "blue":   {"c": "#2a62a8", "soft": "#e9f1fb", "edge": "#b4cdea", "deep": "#1c4a86"},
    "teal":   {"c": "#177f68", "soft": "#e6f4ef", "edge": "#a5d6c6", "deep": "#0f6250"},
    "amber":  {"c": "#a9761c", "soft": "#fdf4e3", "edge": "#e6cb96", "deep": "#855a10"},
    "purple": {"c": "#674a9b", "soft": "#efeafa", "edge": "#cbb9e8", "deep": "#4f3780"},
    "slate":  {"c": "#54637a", "soft": "#eef1f6", "edge": "#c8d2e0", "deep": "#3b485c"},
    "clay":   {"c": "#9a5340", "soft": "#fbeeea", "edge": "#e0bdb2", "deep": "#7a3d2d"},
    "green":  {"c": "#2f7d4f", "soft": "#e8f5ec", "edge": "#aed3ba", "deep": "#205c38"},
}
MARKERS = ["blue", "teal", "amber", "purple", "slate", "clay", "green", "muted"]

FONT = "'Noto Sans SC','PingFang SC','Hiragino Sans GB','Microsoft YaHei',system-ui,sans-serif"
MONO = "'SF Mono',Menlo,Consolas,'Roboto Mono',monospace"


def tw(s: str, size: float, weight: int = 400) -> float:
    """估算文本像素宽度：CJK 全角按 1em，ASCII 按 0.56em。"""
    total = 0.0
    for ch in s:
        o = ord(ch)
        if o > 0x2E80 or ch in "，。、；：？！（）《》“”‘’【】":
            total += size * 1.0
        elif ch == " ":
            total += size * 0.32
        else:
            total += size * (0.60 if weight >= 600 else 0.56)
    return total


def wrap(s: str, size: float, max_w: float, weight: int = 400) -> list[str]:
    """按估算宽度换行；尊重显式换行符，尽量在空格/标点处断开。"""
    out: list[str] = []
    for para in s.split("\n"):
        line = ""
        for ch in para:
            candidate = line + ch
            if tw(candidate, size, weight) > max_w and line:
                # 尝试回退到最近的空格
                if " " in line[6:]:
                    cut = line.rfind(" ")
                    if tw(line[:cut], size, weight) > max_w * 0.55:
                        out.append(line[:cut])
                        line = line[cut + 1:] + ch
                        continue
                out.append(line)
                line = ch
            else:
                line = candidate
        out.append(line)
    return out


def esc(s: str) -> str:
    return escape(str(s), quote=True)


class Svg:
    """一张 SVG 画布。"""

    def __init__(self, width: int = 1120) -> None:
        self.width = width
        self.parts: list[str] = []
        self._height = 0

    # -- 原始元素 ---------------------------------------------------
    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x, y, s, size=14, color=INK, anchor="middle", weight=400, family=None,
             opacity=None, cls=None, letter=None) -> None:
        attrs = [f'x="{x:.1f}"', f'y="{y:.1f}"', f'font-size="{size}"', f'fill="{color}"',
                 f'text-anchor="{anchor}"']
        if weight != 400:
            attrs.append(f'font-weight="{weight}"')
        if family:
            attrs.append(f'font-family="{family}"')
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        if letter is not None:
            attrs.append(f'letter-spacing="{letter}"')
        if cls:
            attrs.append(f'class="{cls}"')
        self.add(f"<text {' '.join(attrs)}>{esc(s)}</text>")

    def lines(self, x, y, rows, size=13, color=MUTED, anchor="middle", lh=None, weight=400,
              family=None, cls=None) -> float:
        lh = lh or size * 1.5
        for i, row in enumerate(rows):
            self.text(x, y + i * lh, row, size, color, anchor, weight, family, cls=cls)
        return y + (len(rows) - 1) * lh

    def rect(self, x, y, w, h, fill=BG, stroke=None, rx=14, sw=1.4, dash=None, opacity=None,
             extra="") -> None:
        attrs = [f'x="{x:.1f}"', f'y="{y:.1f}"', f'width="{w:.1f}"', f'height="{h:.1f}"',
                 f'rx="{rx}"', f'fill="{fill}"']
        if stroke:
            attrs += [f'stroke="{stroke}"', f'stroke-width="{sw}"']
        else:
            attrs.append('stroke="none"')
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        if extra:
            attrs.append(extra)
        self.add(f"<rect {' '.join(attrs)}/>")

    def path(self, d, fill="none", stroke=FAINT, sw=2, dash=None, opacity=None, head=None,
             tail=None, extra="") -> None:
        attrs = [f'd="{d}"', f'fill="{fill}"', f'stroke="{stroke}"', f'stroke-width="{sw}"',
                 'stroke-linejoin="round"', 'stroke-linecap="round"']
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        if head:
            attrs.append(f'marker-end="url(#mk-{head})"')
        if tail:
            attrs.append(f'marker-start="url(#mk2-{tail})"')
        if extra:
            attrs.append(extra)
        self.add(f"<path {' '.join(attrs)}/>")

    def circle(self, cx, cy, r, fill, stroke=None, sw=1.5, opacity=None) -> None:
        attrs = [f'cx="{cx:.1f}"', f'cy="{cy:.1f}"', f'r="{r:.1f}"', f'fill="{fill}"']
        if stroke:
            attrs += [f'stroke="{stroke}"', f'stroke-width="{sw}"']
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        self.add(f"<circle {' '.join(attrs)}/>")

    # -- 组合元素 ---------------------------------------------------
    def card(self, x, y, w, h, title, subs=(), color="slate", tag=None, icon=None,
             fill=None, dash=None, title_size=16, sub_size=12.5, align="center",
             radius=13, hover=True, note=None, mono_sub=False) -> None:
        """标准卡片：标题 + 若干副文本行，左侧/顶部有主题色。"""
        p = PALETTE[color]
        fill = fill or p["soft"]
        cls = ' class="n"' if hover else ""
        self.add(f'<g{cls}>')
        self.rect(x, y, w, h, fill, p["edge"], radius, 1.4, dash)
        # 顶部主题条
        self.rect(x + 14, y, w - 28, 3.5, p["c"], None, 2, 1)
        cy = y + 26
        if icon:
            self.circle(x + 20, cy - 5, 11, p["c"], None)
            self.text(x + 20, cy - 0.5, icon, 12, "#ffffff", weight=700)
            tx = x + 38
        else:
            tx = x + w / 2
        anchor = "middle" if not icon else "start"
        if icon and align == "center":
            # 图标 + 居中标题：整体按视觉居中
            tx = x + w / 2
            self.circle(tx - tw(title, title_size, 600) / 2 - 16, cy - 5, 11, p["c"], None)
            self.text(tx - tw(title, title_size, 600) / 2 - 16, cy - 0.5, icon, 12, "#ffffff", weight=700)
            self.text(tx, cy, title, title_size, p["deep"], anchor, 600)
            ty = cy + 18
        else:
            self.text(tx, cy, title, title_size, p["deep"], anchor, 600)
            ty = cy + 18
        if subs:
            rows = []
            for s in subs:
                rows.extend(wrap(s, sub_size, w - 22))
            self.lines(tx, ty + sub_size, rows, sub_size, MUTED, anchor,
                       lh=sub_size * 1.52, family=MONO if mono_sub else None)
        if tag:
            tw_ = tw(tag, 10.5, 600) + 14
            self.rect(x + w - tw_ - 8, y + h - 20, tw_, 15, "#ffffff", p["edge"], 7.5, 1)
            self.text(x + w - tw_ / 2 - 8, y + h - 9, tag, 10.5, p["c"], weight=600)
        if note:
            self.text(x + 10, y - 7, note, 10.5, FAINT, "start", 500)
        self.add("</g>")

    def stage(self, x, y, w, h, no, title, color="slate", sub=None, radius=14) -> None:
        """编号阶段框（宽扁）。"""
        p = PALETTE[color]
        self.add('<g class="n">')
        self.rect(x, y, w, h, "#ffffff", p["edge"], radius, 1.4)
        self.circle(x + 24, y + h / 2, 13, p["soft"], p["edge"])
        self.text(x + 24, y + h / 2 + 4.5, no, 12.5, p["c"], weight=700)
        self.text(x + 46, y + h / 2 - 1, title, 14, p["deep"], "start", 600)
        if sub:
            self.text(x + 46, y + h / 2 + 15, sub, 10.5, MUTED, "start")
        self.add("</g>")

    def pill(self, x, y, s, color="slate", size=11.5, solid=False, anchor="start") -> float:
        """小胶囊标签，返回宽度。y 为胶囊顶部。"""
        p = PALETTE[color]
        w = tw(s, size, 600) + 18
        h = size + 11
        if anchor == "middle":
            x -= w / 2
        if anchor == "end":
            x -= w
        self.rect(x, y, w, h, p["c"] if solid else p["soft"], None if solid else p["edge"],
                  h / 2, 1.2)
        self.text(x + w / 2, y + h / 2 + size * 0.37, s, size,
                  "#ffffff" if solid else p["deep"], weight=600)
        return w

    def panel(self, x, y, w, h, title=None, color="slate", subtitle=None, dash="7 6",
              fill=None, label_color=None) -> None:
        p = PALETTE[color]
        self.rect(x, y, w, h, fill or BG_SOFT, p["edge"], 16, 1.3, dash)
        if title:
            w_ = tw(title, 12.5, 600) + 22
            self.rect(x + 14, y - 11, w_, 22, fill or BG_SOFT, p["edge"], 11, 1.2)
            self.text(x + 25, y + 4, title, 12.5, label_color or p["c"], "start", 600)
        if subtitle:
            self.text(x + w - 14, y + 20, subtitle, 10.5, FAINT, "end")

    def arrow(self, pts, color="slate", sw=1.9, dash=None, head=True, curve=10, opacity=None,
              head_size="std") -> None:
        cl = PALETTE.get(color, {"c": "#8794a5"})["c"] if color != "muted" else FAINT
        d = _rounded(pts, curve)
        self.path(d, stroke=cl, sw=sw, dash=dash, opacity=opacity,
                  head=color if head else None)

    def arrow_label(self, x, y, s, color="slate", size=11, anchor="middle", bg=None) -> None:
        p = PALETTE.get(color, {"c": MUTED, "soft": "#ffffff", "edge": LINE})
        if bg is False:
            self.text(x, y, s, size, p["c"] if color != "muted" else MUTED, anchor, 600)
            return
        w = tw(s, size, 600) + 12
        h = size + 9
        if anchor == "middle":
            bx = x - w / 2
        elif anchor == "end":
            bx = x - w
        else:
            bx = x
        self.rect(bx, y - h + 3, w, h, bg or "#ffffff", None, h / 2, 1)
        self.text(bx + w / 2, y - h / 2 + 6.5, s, size, p["c"] if color != "muted" else MUTED, weight=600)

    def legend(self, x, y, items, size=11.5, gap=26) -> None:
        """items: [(color, label)]，一行横向排列。"""
        cx = x
        for color, label in items:
            p = PALETTE[color]
            self.rect(cx, y - 9, 16, 10, p["soft"], p["edge"], 3, 1.2)
            self.text(cx + 22, y, label, size, MUTED, "start", 500)
            cx += tw(label, size, 500) + 22 + gap

    def note(self, x, y, s, size=11, color=FAINT, anchor="start") -> None:
        self.text(x, y, s, size, color, anchor)

    # -- 输出 -------------------------------------------------------
    def render(self, height=None, extra_defs: str = "") -> str:
        h = int(height or self._height)
        defs = _defs() + extra_defs
        body = "\n".join(self.parts)
        return (f'<svg viewBox="0 0 {self.width} {h}" width="100%" '
                f'preserveAspectRatio="xMidYMin meet" role="img" '
                f'xmlns="http://www.w3.org/2000/svg">{defs}\n{body}\n</svg>')


def _rounded(pts, r=10) -> str:
    """把折线点列转成带圆角的 SVG path。"""
    if len(pts) < 2:
        return ""
    d = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        # 两个方向的单位向量
        import math
        v1 = (x1 - x0, y1 - y0)
        v2 = (x2 - x1, y2 - y1)
        l1 = math.hypot(*v1) or 1
        l2 = math.hypot(*v2) or 1
        rr = min(r, l1 / 2, l2 / 2)
        p1 = (x1 - v1[0] / l1 * rr, y1 - v1[1] / l1 * rr)
        p2 = (x1 + v2[0] / l2 * rr, y1 + v2[1] / l2 * rr)
        d.append(f"L {p1[0]:.1f} {p1[1]:.1f}")
        d.append(f"Q {x1:.1f} {y1:.1f} {p2[0]:.1f} {p2[1]:.1f}")
    d.append(f"L {pts[-1][0]:.1f} {pts[-1][1]:.1f}")
    return " ".join(d)


def _defs() -> str:
    parts = []
    for name in MARKERS:
        color = PALETTE.get(name, {}).get("c", FAINT) if name != "muted" else FAINT
        parts.append(
            f'<marker id="mk-{name}" viewBox="0 0 10 10" refX="8.4" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M 0.8 1.4 L 8.6 5 L 0.8 8.6 z" fill="{color}"/></marker>')
    return "<defs>" + "".join(parts) + "</defs>"


# 常用版式常量
W = 1120
GUTTER = 28
