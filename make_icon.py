"""生成 app.ico —— 纯标准库手写 ICO（苦力怕脸），无需 Pillow。

做法：以 4 倍超采样画出像素图再降采样，得到平滑边缘；
ICO 内部用经典 32bpp BMP(DIB) 条目，兼容性最好。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SS = 4                      # 超采样倍数

GREEN = (108, 178, 74)      # 苦力怕主色
GREEN_D = (84, 143, 56)     # 暗部
GREEN_L = (132, 199, 96)    # 亮部
FACE = (24, 32, 22)         # 面部（近黑带一点绿）
EDGE = (58, 96, 42)         # 描边


def _noise(x: int, y: int) -> float:
    """确定性伪随机，保证每次生成同一个图标。"""
    h = (x * 374761393 + y * 668265263) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


# 苦力怕脸的几何（相对坐标 0~1）：两只眼睛 + 嘴 + 两条腿
FACE_RECTS = [
    (0.19, 0.26, 0.40, 0.50),   # 左眼
    (0.60, 0.26, 0.81, 0.50),   # 右眼
    (0.42, 0.42, 0.58, 0.63),   # 嘴中
    (0.33, 0.63, 0.42, 0.82),   # 嘴左腿
    (0.58, 0.63, 0.67, 0.82),   # 嘴右腿
]


def _rounded(x: float, y: float, r: float) -> bool:
    """点 (x,y)（0~1）是否在圆角正方形内。"""
    cx = min(max(x, r), 1 - r)
    cy = min(max(y, r), 1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r + 1e-9 or (r <= x <= 1 - r) or (r <= y <= 1 - r)


def _sample(u: float, v: float) -> tuple[int, int, int, int]:
    """返回某点的 RGBA（超采样坐标系 0~1）。"""
    radius = 0.18
    if not _rounded(u, v, radius):
        return (0, 0, 0, 0)

    # 描边
    if not _rounded(u, v, radius + 0.012) or u < 0.02 or v < 0.02 or u > 0.98 or v > 0.98:
        return (*EDGE, 255)

    # 面部
    for x0, y0, x1, y1 in FACE_RECTS:
        if x0 <= u <= x1 and y0 <= v <= y1:
            return (*FACE, 255)

    # 绿色底 + 斑驳纹理
    n = _noise(int(u * 512), int(v * 512))
    if n > 0.86:
        color = GREEN_L
    elif n < 0.16:
        color = GREEN_D
    else:
        color = GREEN
    # 左上角稍亮，右下角稍暗，制造体积感
    shade = (0.5 - u) * 0.10 + (0.5 - v) * 0.08
    color = tuple(max(0, min(255, int(c * (1 + shade)))) for c in color)
    return (*color, 255)


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    """渲染 size x size 的 RGBA 像素（4x 超采样后降采样）。"""
    n = size * SS
    grid = [[_sample((x + 0.5) / n, (y + 0.5) / n) for x in range(n)] for y in range(n)]
    out: list[list[tuple[int, int, int, int]]] = []
    for oy in range(size):
        row = []
        for ox in range(size):
            r = g = b = a = 0
            for dy in range(SS):
                for dx in range(SS):
                    pr, pg, pb, pa = grid[oy * SS + dy][ox * SS + dx]
                    # 按 alpha 预乘，避免透明边缘出现黑边
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            cnt = SS * SS
            if a == 0:
                row.append((0, 0, 0, 0))
            else:
                row.append((round(r / a), round(g / a), round(b / a), round(a / cnt)))
        out.append(row)
    return out


def to_dib(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    """转成 ICO 里的 32bpp BMP(DIB) 数据：自下而上 BGRA + AND 掩码。"""
    size = len(pixels)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    body = bytearray()
    for y in range(size - 1, -1, -1):          # BMP 自下而上
        for x in range(size):
            r, g, b, a = pixels[y][x]
            body += bytes((b, g, r, a))
    # AND 掩码：32bpp 用 alpha 通道，这里全 0（不透明）即可，行按 4 字节对齐
    row_bytes = ((size + 31) // 32) * 4
    body += bytes(row_bytes * size)
    return header + bytes(body)


def build_ico(path: Path) -> Path:
    images = [(s, to_dib(render(s))) for s in SIZES]
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in images:
        out += struct.pack("<BBBBHHII",
                           size if size < 256 else 0,
                           size if size < 256 else 0,
                           0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _, data in images:
        out += data
    path.write_bytes(bytes(out))
    return path


# 顺便产出一张 PNG，方便在文档/浏览器里预览
def build_png(path: Path, size: int = 256) -> Path:
    pixels = render(size)
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    ico = build_ico(base / "app.ico")
    png = build_png(base / "docs" / "icon-preview.png")
    print(f"已生成 {ico}（{ico.stat().st_size / 1024:.1f} KB，"
          f"{len(SIZES)} 种尺寸：{', '.join(map(str, SIZES))}）")
    print(f"已生成 {png}")
    sys.exit(0)
