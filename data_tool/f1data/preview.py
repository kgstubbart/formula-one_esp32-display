"""Draw one moment of a race package as an SVG file, to check a package on the PC."""

from __future__ import annotations

from pathlib import Path

from . import race_format as rf


def write_svg(package_path: Path, svg_path: Path, fraction: float = 0.5) -> None:
    data = rf.read_package(package_path)
    frames = data["frames"]
    frame = frames[min(int(fraction * (len(frames) - 1)), len(frames) - 1)]
    width, height = int(data["map_w"]), int(data["map_h"])
    margin = 120
    points = " ".join(f"{x},{y}" for x, y in data["outline"].tolist())
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'viewBox="{-margin} {-margin} {width + 2 * margin} {height + 2 * margin}">',
             f'<rect x="{-margin}" y="{-margin}" width="100%" height="100%" fill="#101014"/>',
             f'<polygon points="{points}" fill="none" stroke="#777" stroke-width="28"/>']
    start_x, start_y = data["outline"][0].tolist()
    parts.append(f'<circle cx="{start_x}" cy="{start_y}" r="45" fill="#fff"/>')
    for driver, car in zip(data["drivers"], frame["cars"]):
        state = (int(car["xs"]) >> 12) & 3
        if state == rf.STATE_OUT:
            continue
        x, y = int(car["xs"]) & 0xFFF, int(car["y"]) & 0xFFF
        colour = "#%02x%02x%02x" % driver["rgb"]
        parts.append(f'<circle cx="{x}" cy="{y}" r="42" fill="{colour}"/>')
        parts.append(f'<text x="{x + 55}" y="{y + 20}" font-size="70" fill="#eee">'
                     f'{driver["code"]}</text>')
    parts.append("</svg>")
    Path(svg_path).write_text("\n".join(parts), encoding="utf-8")
