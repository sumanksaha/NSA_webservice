"""Generate PWA icon assets for issue #70.

Creates 192px, 512px, maskable, favicon, and Apple touch icons from Pillow.
Run:  python scripts/generate_pwa_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
ICONS_DIR = STATIC_DIR / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)

# Brand colors (from base.html / theme.css)
BG_BLUE = (11, 61, 145)       # #0b3d91
WHITE = (255, 255, 255)
TEXT_BLUE = (15, 80, 168)     # #0f50a8


def _draw_icon(size: int, maskable: bool = False) -> Image.Image:
    """Draw a shield icon with 'NSA' text, sized to *size* x *size*."""
    img = Image.new("RGBA", (size, size), BG_BLUE)
    draw = ImageDraw.Draw(img)

    if maskable:
        # For maskable icons, keep the meaningful content within the center
        # 40 % safe-zone (per the WAI minimum safe area spec).  We draw a
        # background circle that fills the safe zone.
        safe = int(size * 0.40)
        cx = cy = size // 2
        draw.ellipse(
            [cx - safe, cy - safe, cx + safe, cy + safe],
            fill=TEXT_BLUE,
        )

    # Shield shape (a rounded-top rectangle with a pointed bottom)
    margin = int(size * 0.18)
    sx, sy = margin, margin
    sw, sh = size - 2 * margin, size - 2 * margin
    # Rounded rectangle with extra rounding at top, pointed bottom
    draw.rounded_rectangle(
        [sx, sy, sx + sw, sy + sh],
        radius=int(sw * 0.22),
        fill=WHITE,
    )
    # Point at bottom of shield
    tip_y = sy + sh
    left_x = sx + int(sw * 0.15)
    right_x = sx + int(sw * 0.85)
    draw.polygon(
        [(left_x, tip_y - int(sh * 0.08)),
         (right_x, tip_y - int(sh * 0.08)),
         (sx + sw // 2, tip_y + int(sh * 0.06))],
        fill=WHITE,
    )

    # "NSA" text — use a system font, fall back to default
    text = "NSA"
    font_size = int(size * 0.34)
    try:
        font = ImageFont.truetype(
            str(_find_font()), size=font_size
        )
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2
    ty = (size - th) // 2 - int(size * 0.02)
    draw.text((tx, ty), text, fill=BG_BLUE, font=font)
    return img


def _find_font():
    """Find a usable TTF font on the system."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        from pathlib import Path as P
        if P(p).exists():
            return P(p)
    # Last resort: return a tiny default that PIL can always load
    return None


def _save(size: int, maskable: bool = False, suffix: str = "") -> Path:
    img = _draw_icon(size, maskable=maskable)
    name = f"pwa-{size}{suffix}.png"
    path = ICONS_DIR / name
    img.save(path, "PNG")
    print(f"  [OK] {path.name}  ({img.size[0]}x{img.size[1]})")
    return path


def generate_all() -> None:
    print("Generating PWA icons...")
    _save(192)
    _save(512)
    _save(192, maskable=True, suffix="-maskable")
    _save(512, maskable=True, suffix="-maskable")
    _save(32, suffix="-favicon")
    _save(16, suffix="-favicon")
    _save(180, suffix="-apple-touch")
    print("Done.")


if __name__ == "__main__":
    generate_all()
