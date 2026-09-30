#!/usr/bin/env python3
"""GNU-Darwin Workstation Plymouth assets.

Deterministic PIL generator. Reads the palette from ../../design.json (the single
source of truth) and produces, next to this file:

  * the theme pixmaps at NATIVE 2880x1800 scale (entry, lock, bullet, caret,
    capslock, progress track/fill/caps/highlight, prompt and message 3-slices),
  * gnu-darwin-workstation.script rendered from gnu-darwin-workstation.script.in with the
    same @token_rgb@ / @theme_name@ / @slug@ substitution the installer performs,
  * mockups/*.png: five 1440x900 previews (rendered at 2880x1800, then halved)
    that approximate what the script draws for each surface.

No randomness, no timestamps; running it twice yields byte-identical files.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
PRESET = HERE.parent.parent
DESIGN = json.loads((PRESET / "design.json").read_text())
PAL = DESIGN["palette"]
RAINBOW = DESIGN["rainbow"]
NAME = DESIGN["name"]
SLUG = DESIGN["id"]
FONT_DIR = Path("/usr/share/fonts/truetype/liberation")
FONT_REGULAR = FONT_DIR / "LiberationSans-Regular.ttf"

SS = 4  # supersampling factor for anti-aliased glyphs


def rgb(key: str) -> tuple[int, int, int]:
    value = PAL[key] if key in PAL else key
    return tuple(int(value[n : n + 2], 16) for n in (1, 3, 5))


def rgba(key: str, alpha: int) -> tuple[int, int, int, int]:
    return rgb(key) + (alpha,)


def save(img: Image.Image, name: str) -> None:
    # PIL writes no timestamp chunk by default; keep default compression.
    img.save(HERE / name, "PNG", optimize=True)


def downsample(img: Image.Image, factor: int = SS) -> Image.Image:
    return img.resize((img.width // factor, img.height // factor), Image.LANCZOS)


def rounded_surface(w, h, radius, fill, border, border_px, highlight=0, depth=0):
    """Compatibility name: flat square surface with hard raised bevel."""
    im=Image.new("RGBA",(w,h),rgba(fill,255));d=ImageDraw.Draw(im)
    d.rectangle((0,0,w-1,h-1),outline=rgba(border,255),width=max(1,border_px))
    d.line((1,1,w-2,1),fill=(255,255,255,255),width=1)
    d.line((1,1,1,h-2),fill=(255,255,255,255),width=1)
    return im


def three_slice(w_total: int, h: int, radius: int, fill: str, border: str, border_px: int,
                highlight: int, depth: float, stem: str) -> None:
    """Emit <stem>.png (1 x h column), <stem>-cap-l.png and <stem>-cap-r.png (radius x h).

    The script scales the column horizontally and the caps uniformly by height, so
    the corner radius stays proportional to the band height at any resolution.
    """
    full = rounded_surface(w_total, h, radius, fill, border, border_px, highlight, depth)
    cap_w = radius
    save(full.crop((0, 0, cap_w, h)), f"{stem}-cap-l.png")
    save(full.crop((w_total - cap_w, 0, w_total, h)), f"{stem}-cap-r.png")
    save(full.crop((w_total // 2, 0, w_total // 2 + 1, h)), f"{stem}.png")


# --------------------------------------------------------------------------- pixmaps

def make_entry() -> None:
    # 640x72 square entry: surface fill and hard bevel.
    save(rounded_surface(640, 72, 36, "surface", "control_border", 2, highlight=40, depth=0.5), "entry.png")


def make_lock() -> None:
    S = 72 * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = rgba("muted", 255)
    cx = S / 2
    # Shackle: thick arc, open at the bottom into the body.
    outer, inner = 27 * SS, 17 * SS
    top = 8 * SS
    shackle_cy = top + outer
    d.pieslice((cx - outer, shackle_cy - outer, cx + outer, shackle_cy + outer), 180, 360, fill=col)
    d.pieslice((cx - inner, shackle_cy - inner, cx + inner, shackle_cy + inner), 180, 360, fill=(0, 0, 0, 0))
    d.rectangle((cx - outer, shackle_cy, cx + outer, shackle_cy + 8 * SS), fill=(0, 0, 0, 0))
    for sx in (cx - outer, cx + inner):
        d.rectangle((sx, shackle_cy - 1, sx + (outer - inner), shackle_cy + 9 * SS), fill=col)
    # Body.
    body_top = 32 * SS
    d.rounded_rectangle((11 * SS, body_top, S - 11 * SS, S - 5 * SS), 8 * SS, fill=col)
    # Keyhole cut-out.
    hole = Image.new("L", (S, S), 0)
    hd = ImageDraw.Draw(hole)
    kc = (cx, body_top + 15 * SS)
    hd.ellipse((kc[0] - 6 * SS, kc[1] - 6 * SS, kc[0] + 6 * SS, kc[1] + 6 * SS), fill=255)
    hd.rounded_rectangle((kc[0] - 3 * SS, kc[1], kc[0] + 3 * SS, kc[1] + 17 * SS), 2 * SS, fill=255)
    clear = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(clear, (0, 0), hole)
    save(downsample(img), "lock.png")


def make_bullet():
    save(Image.new("RGBA",(14,14),rgba("selection",255)), "bullet.png")


def make_caret() -> None:
    S = SS
    img = Image.new("RGBA", (4 * S, 40 * S), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, 4 * S - 1, 40 * S - 1), 2 * S, fill=rgba("foreground", 255))
    save(downsample(img), "caret.png")


def make_capslock() -> None:
    # 56x56 badge (surface fill, control_border) with a caps-lock arrow in warning.
    badge = rounded_surface(56, 56, 12, "surface", "control_border", 2, highlight=36, depth=0.45)
    S = 56 * SS
    glyph = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(glyph)
    col = rgba("warning", 255)
    cx = S / 2
    # Arrow head.
    d.polygon([(cx, 11 * SS), (cx + 15 * SS, 26 * SS), (cx - 15 * SS, 26 * SS)], fill=col)
    # Arrow stem.
    d.rounded_rectangle((cx - 6 * SS, 24 * SS, cx + 6 * SS, 36 * SS), 1.5 * SS, fill=col)
    # Lock bar under the arrow.
    d.rounded_rectangle((cx - 6 * SS, 40 * SS, cx + 6 * SS, 45 * SS), 1.5 * SS, fill=col)
    badge = Image.alpha_composite(badge, downsample(glyph))
    save(badge, "capslock.png")


def make_progress() -> None:
    # Track: 576x12 rectangle (20% of 2880 wide, aspect 48:1), elevated fill, 1 px border,
    # hard upper-left bevel. The script keeps bar_h = bar_w / 48 so scaling is uniform.
    save(rounded_surface(576, 12, 6, "elevated", "control_border", 1, highlight=60, depth=0.5), "progress-track.png")
    # Fill: 1x1 accent, scaled between the two caps.
    save(Image.new("RGBA", (1, 1), rgba("accent", 255)), "progress-fill.png")
    # Caps: 6x12 rectangular end pieces in accent (exact native size; scaled 2:1 at DeviceScale 2).
    S = SS
    pill = Image.new("RGBA", (12 * S, 12 * S), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rectangle((0, 0, 12 * S - 1, 12 * S - 1), fill=rgba("accent", 255))
    pill = downsample(pill)
    save(pill.crop((0, 0, 6, 12)), "progress-cap-l.png")
    save(pill.crop((6, 0, 12, 12)), "progress-cap-r.png")
    # Transparent compatibility sprite; no glow or floating highlight.
    hr, hg, hb = [int(c * 0.35 + 255 * 0.65) for c in rgb("secondary")]
    save(Image.new("RGBA", (1, 1), (hr, hg, hb, 0)), "progress-highlight.png")


def make_bands() -> None:
    # Prompt card: elevated fill, 2 px control_border, milky highlight. Column 1x240,
    # caps 24x240 (radius/height = 0.10, i.e. ~21 px radius on the 207 px native card).
    three_slice(400, 240, 24, "elevated", "control_border", 2, 44, 0.32, "prompt-surface")
    # Message band: surface fill, 2 px decorative border, fainter highlight.
    # Column 1x108, caps 24x108 (radius/height = 0.22, ~20 px on the 90 px native band).
    three_slice(400, 108, 24, "surface", "border", 2, 30, 0.45, "message-surface")


PIXMAPS = [make_entry, make_lock, make_bullet, make_caret, make_capslock, make_progress, make_bands]


# --------------------------------------------------------------------------- script render

def render_script() -> str:
    """Apply exactly the installer's substitutions to the template and write the .script."""
    values = {"theme_name": NAME, "slug": SLUG}
    for key, value in PAL.items():
        values[key + "_rgb"] = ", ".join(f"{c / 255:.6f}" for c in rgb(value))
    text = (HERE / f"{SLUG}.script.in").read_text()
    for key, value in values.items():
        text = text.replace("@" + key + "@", value)
    leftover = re.search(r"@[a-z][a-z_]*@", text)
    if leftover:
        raise SystemExit(f"Unresolved template token: {leftover.group(0)}")
    (HERE / f"{SLUG}.script").write_text(text)
    return text


# --------------------------------------------------------------------------- mockups
# The renderer mirrors the geometry in gnu-darwin-workstation.script.in at W=2880, H=1800.

class Layout:
    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        ui = H / 1800
        self.font_prompt = max(12, int(26 * ui))
        self.font_msg = max(11, int(22 * ui))
        self.font_title = max(11, int(22 * ui))
        self.bar_w = int(W * 0.20)
        self.bar_h = max(4, int(self.bar_w / 48))
        self.bar_x = (W - self.bar_w) // 2
        self.bar_y = int(H * 0.78 - self.bar_h / 2)
        self.field_h = int(H * 0.040)
        self.field_w = int(self.field_h * 640 / 72)
        self.lock_d = self.field_h
        self.caps_d = int(self.field_h * 56 / 72)
        self.bullet_d = int(self.field_h * 22 / 72)
        self.icon_gap = int(H * 0.012)
        self.pad_x = int(H * 0.020)
        self.pad_y = int(H * 0.014)
        self.gap = int(H * 0.010)
        self.row_h = int(self.font_prompt * 1.6)
        self.card_w = self.lock_d + self.icon_gap + self.field_w + self.icon_gap + self.caps_d + 2 * self.pad_x
        self.card_h = self.pad_y + self.row_h + self.gap + self.field_h + self.pad_y
        self.card_y = int(H * 0.785 - self.field_h / 2 - self.gap - self.row_h - self.pad_y)
        self.band_h = int(H * 0.05)
        self.band_y = int(H * 0.915)
        self.title_y = int(H * 0.615)


def font(pt: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_REGULAR), int(round(pt * 96 / 72)))


def text_image(text: str, colour: str, pt: int) -> Image.Image:
    f = font(pt)
    l, t, r, b = f.getbbox(text)
    asc, desc = f.getmetrics()
    img = Image.new("RGBA", (max(1, r - l + 2), asc + desc), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((-l + 1, 0), text, font=f, fill=rgba(colour, 255))
    return img


def paste(canvas: Image.Image, img: Image.Image, x: float, y: float) -> None:
    canvas.alpha_composite(img, (int(x), int(y)))


def scaled(name: str, w: int, h: int) -> Image.Image:
    return Image.open(HERE / name).convert("RGBA").resize((max(1, w), max(1, h)), Image.BILINEAR)


def draw_band(canvas: Image.Image, stem: str, x: int, y: int, w: int, h: int) -> None:
    cap = Image.open(HERE / f"{stem}-cap-l.png")
    cap_w = int(h * cap.width / cap.height)
    paste(canvas, scaled(f"{stem}-cap-l.png", cap_w, h), x, y)
    paste(canvas, scaled(f"{stem}.png", w - 2 * cap_w, h), x + cap_w, y)
    paste(canvas, scaled(f"{stem}-cap-r.png", cap_w, h), x + w - cap_w, y)


def base_canvas(L: Layout) -> Image.Image:
    canvas = Image.new("RGBA", (L.W, L.H), rgba("desktop", 255))
    wp = PRESET / "artwork" / "wallpaper.png"
    if wp.is_file():
        art = Image.open(wp).convert("RGBA")
        s = min(L.W / art.width, L.H / art.height)
        art = art.resize((int(art.width * s), int(art.height * s)), Image.LANCZOS)
        paste(canvas, art, (L.W - art.width) / 2, (L.H - art.height) / 2)
    else:  # palette gradient stand-in
        top, bottom = rgb("background"), rgb("desktop")
        px = canvas.load()
        for y in range(L.H):
            t = y / L.H
            c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
            for x in range(L.W):
                px[x, y] = c + (255,)
    title = text_image(NAME, "foreground", L.font_title)
    ImageDraw.Draw(canvas).rounded_rectangle(((L.W-title.width)/2-24, L.title_y-12, (L.W+title.width)/2+24, L.title_y+title.height+12), radius=0, fill=rgba("surface",255), outline=rgba("control_border",255), width=2)
    paste(canvas, title, (L.W - title.width) / 2, L.title_y)
    return canvas


def draw_progress(canvas: Image.Image, L: Layout, progress: float) -> None:
    paste(canvas, scaled("progress-highlight.png", L.bar_w - L.bar_h, max(1, int(2 * L.H / 1800))),
          L.bar_x + L.bar_h / 2, L.bar_y - max(1, int(2 * L.H / 1800)))
    paste(canvas, scaled("progress-track.png", L.bar_w, L.bar_h), L.bar_x, L.bar_y)
    cap_w = max(1, L.bar_h // 2)
    w = max(2 * cap_w, int(L.bar_w * progress))
    paste(canvas, scaled("progress-cap-l.png", cap_w, L.bar_h), L.bar_x, L.bar_y)
    paste(canvas, scaled("progress-fill.png", w - 2 * cap_w, L.bar_h), L.bar_x + cap_w, L.bar_y)
    paste(canvas, scaled("progress-cap-r.png", cap_w, L.bar_h), L.bar_x + w - cap_w, L.bar_y)


def draw_dialog(canvas: Image.Image, L: Layout, prompt: str, bullets: int = 0,
                entry: str | None = None, caps: bool = False, caret: bool = True) -> None:
    text = text_image(prompt, "foreground", L.font_prompt)
    card_w = max(L.card_w, text.width + 2 * L.pad_x)
    card_x = (L.W - card_w) // 2
    draw_band(canvas, "prompt-surface", card_x, L.card_y, card_w, L.card_h)
    row_y = L.card_y + L.pad_y
    paste(canvas, text, (L.W - text.width) / 2, row_y + (L.row_h - text.height) / 2)
    field_y = row_y + L.row_h + L.gap
    inner_x = card_x + (card_w - (L.lock_d + L.icon_gap + L.field_w + L.icon_gap + L.caps_d)) // 2
    paste(canvas, scaled("lock.png", L.lock_d, L.lock_d), inner_x, field_y)
    field_x = inner_x + L.lock_d + L.icon_gap
    paste(canvas, scaled("entry.png", L.field_w, L.field_h), field_x, field_y)
    if caps:
        paste(canvas, scaled("capslock.png", L.caps_d, L.caps_d),
              field_x + L.field_w + L.icon_gap, field_y + (L.field_h - L.caps_d) / 2)
    field_pad = int(L.field_h * 0.40)
    inner_w = L.field_w - 2 * field_pad
    if entry is None:
        pitch = int(L.bullet_d * 1.5)
        shown = min(bullets, inner_w // pitch)
        dot = scaled("bullet.png", L.bullet_d, L.bullet_d)
        for i in range(shown):
            paste(canvas, dot, field_x + field_pad + i * pitch, field_y + (L.field_h - L.bullet_d) / 2)
    else:
        caret_h = int(L.field_h * 40 / 72)
        caret_w = max(1, int(L.field_h * 4 / 72))
        img = text_image(entry, "foreground", L.font_prompt) if entry else None
        x = field_x + field_pad
        if img is not None:
            limit = inner_w - caret_w - int(L.field_h * 0.1)
            if img.width > limit:
                img = img.resize((limit, int(img.height * limit / img.width)), Image.LANCZOS)
            paste(canvas, img, x, field_y + (L.field_h - img.height) / 2)
            x += img.width + int(L.field_h * 0.06)
        if caret:
            paste(canvas, scaled("caret.png", caret_w, caret_h), x, field_y + (L.field_h - caret_h) / 2)


def draw_message(canvas: Image.Image, L: Layout, text: str) -> None:
    img = text_image(text, "muted", L.font_msg)
    band_w = max(int(L.W * 0.24), img.width + 2 * L.pad_x)
    band_x = (L.W - band_w) // 2
    draw_band(canvas, "message-surface", band_x, L.band_y, band_w, L.band_h)
    paste(canvas, img, (L.W - img.width) / 2, L.band_y + (L.band_h - img.height) / 2)


def mockups() -> None:
    out = HERE / "mockups"
    out.mkdir(exist_ok=True)
    L = Layout(2880, 1800)

    def emit(name: str, canvas: Image.Image) -> None:
        canvas.convert("RGB").resize((1440, 900), Image.LANCZOS).save(out / name, "PNG", optimize=True)

    c = base_canvas(L)
    draw_progress(c, L, 0.62)
    emit("boot-progress.png", c)

    c = base_canvas(L)
    draw_dialog(c, L, "Please unlock disk sda3_crypt:", bullets=9, caps=True)
    emit("password.png", c)

    c = base_canvas(L)
    draw_dialog(c, L, "Question surface", entry="typed answer")
    emit("question.png", c)

    c = base_canvas(L)
    draw_progress(c, L, 0.62)
    draw_message(c, L, "Checking disk 1 of 2 (48% complete)")
    emit("message.png", c)

    c = base_canvas(L)
    draw_message(c, L, "Shutting down")
    emit("shutdown.png", c)


def main(argv: list[str]) -> None:
    save(rounded_surface(500, 80, 12, "surface", "control_border", 2, highlight=30, depth=0.1), "title-surface.png")
    for maker in PIXMAPS:
        maker()
    render_script()
    if "--no-mockups" not in argv:
        mockups()
    print("ok")


if __name__ == "__main__":
    main(sys.argv[1:])
