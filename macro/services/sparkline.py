"""軽量SVGスパークラインジェネレータ。

JS ライブラリに頼らず、サーバ側で SVG 文字列を生成する。
"""

from typing import Iterable, List, Optional


def generate_sparkline_svg(
    values: Iterable[float],
    width: int = 120,
    height: int = 28,
    stroke: str = "#38BDF8",
    stroke_width: float = 1.5,
    fill: Optional[str] = "rgba(56, 189, 248, 0.18)",
) -> str:
    """値の系列から SVG sparkline を返す。空または1点だけなら空文字。"""
    arr: List[float] = [float(v) for v in values if v is not None]
    if len(arr) < 2:
        return ""

    n = len(arr)
    vmin = min(arr)
    vmax = max(arr)

    points: List[str] = []
    if vmax == vmin:
        y = height / 2.0
        for i in range(n):
            x = (i / (n - 1)) * width
            points.append(f"{x:.1f},{y:.1f}")
    else:
        for i, v in enumerate(arr):
            x = (i / (n - 1)) * width
            y = height - (v - vmin) / (vmax - vmin) * height
            points.append(f"{x:.1f},{y:.1f}")

    points_str = " ".join(points)

    fill_path = ""
    if fill:
        fill_path = (
            f'<polygon fill="{fill}" stroke="none" points="'
            f'0,{height} {points_str} {width},{height}"/>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'xmlns="http://www.w3.org/2000/svg" class="macro-sparkline" '
        f'aria-hidden="true">'
        f'{fill_path}'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="{stroke_width}" '
        f'stroke-linejoin="round" stroke-linecap="round" '
        f'points="{points_str}"/>'
        f'</svg>'
    )
