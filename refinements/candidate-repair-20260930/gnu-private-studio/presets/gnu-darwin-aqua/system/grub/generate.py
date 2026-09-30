#!/usr/bin/env python3
"""GNU-Darwin Aqua -- GRUB (gfxmenu) theme generator.

Deterministic: reads ../../design.json (the single palette), draws every pixmap with
PIL + numpy, builds the PFF2 fonts with grub-mkfont, writes theme.txt.in (tokens) and
theme.txt (rendered), and renders mockup.png so the menu can be judged without a reboot.

Run:  python3 generate.py            (from anywhere; paths are resolved from this file)
Outputs land next to this script.  Nothing outside this directory is touched.

Everything is sized for the native 2880x1800 framebuffer (GRUB_GFXMODE=auto on the
MacBook), i.e. 2x of a 1440x900 design.
"""
import json
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
PRESET = HERE.parent.parent
ICONS = HERE / "icons"
DESIGN = json.loads((PRESET / "design.json").read_text())
PAL = DESIGN["palette"]
RAINBOW = DESIGN["rainbow"]
THEME_NAME = DESIGN["name"]
SLUG = DESIGN["id"]

SCREEN = (2880, 1800)
SS = 4                                   # supersampling factor for every pixmap

FONT_DIR = Path("/usr/share/fonts/truetype/liberation")
GRUB_MKFONT = "/usr/bin/grub-mkfont"
FONT_PX = 28
FONT_RANGES = "0x20-0x7F,0xA0-0x17F,0x2010-0x2027"
# pf2 file -> (ttf, -n name, extra grub-mkfont flags).  grub-mkfont appends "<style> <size>"
# to the -n name, so the names used inside theme.txt are read back from the .pf2 NAME section.
# Liberation Sans regular is emboldened by grub-mkfont for the bold PFF2 face.
LIBERATION = FONT_DIR / "LiberationSans-Regular.ttf"
LIBERATION_MONO = FONT_DIR / "LiberationMono-Regular.ttf"
FONTS = {
    "font.pf2": (LIBERATION, "GNU-Darwin Aqua", []),
    "font-bold.pf2": (LIBERATION, "GNU-Darwin Aqua", ["-b"]),
    "font-mono.pf2": (LIBERATION_MONO, "GNU-Darwin Aqua Mono", []),
}

# ---- layout (physical pixels at 2880x1800) -------------------------------------------
L = dict(
    logo=240,
    menu_w=1120, menu_h_pct=28,        # 5 rows; 34% left half the card empty
    item_height=72, item_padding=14, item_spacing=10,
    icon=64, icon_space=28,
    pill_radius=14, pill_corner=14,            # select_*.png corner pieces
    menu_radius=20, menu_border=2, menu_corner=22,   # menu_*.png corner pieces
    bar_w=600, bar_h=12, bar_radius=6, bar_corner=5,  # bar_*.png / hl_*.png
    scrollbar_w=12,
)


# ---- colour helpers ----------------------------------------------------------------------
def hex2rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def rgbf(h):
    return np.array(hex2rgb(h), dtype=np.float32) / 255.0


def mix(h, other, t):
    a, b = np.array(hex2rgb(h), float), np.array(hex2rgb(other), float)
    c = a * (1 - t) + b * t
    return "#%02X%02X%02X" % tuple(int(round(v)) for v in c)


def lighten(h, t):
    return mix(h, "#FFFFFF", t)


def darken(h, t):
    return mix(h, "#000000", t)


def rel_lum(h):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in hex2rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = rel_lum(fg), rel_lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ---- mask / compositing helpers ----------------------------------------------------------
def rounded_mask(w, h, radius, box=None, ss=SS):
    """Anti-aliased coverage (float32, h x w, 0..1) of a rounded rectangle.
    box may use float coordinates; everything is rasterised at ss x and box-filtered."""
    if box is None:
        box = (0, 0, w, h)
    im = Image.new("L", (w * ss, h * ss), 0)
    x0, y0, x1, y1 = (v * ss for v in box)
    ImageDraw.Draw(im).rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius=radius * ss, fill=255)
    return np.asarray(im.resize((w, h), Image.BOX), dtype=np.float32) / 255.0


def compose(w, h, layers, base=None):
    """Alpha-over compositing of (mask, hex colour, alpha) layers onto a transparent canvas
    (or onto `base`, an RGB float array). Returns an RGBA image (straight alpha)."""
    if base is None:
        rgb = np.zeros((h, w, 3), np.float32)
        a = np.zeros((h, w), np.float32)
    else:
        rgb = base.astype(np.float32).copy()
        a = np.ones((h, w), np.float32)
    for mask, col, alpha in layers:
        la = np.clip(mask * alpha, 0, 1)[..., None]
        rgb = rgbf(col)[None, None, :] * la + rgb * (1 - la)
        a = la[..., 0] + a * (1 - la[..., 0])
    out = np.zeros((h, w, 4), np.float32)
    nz = a > 1e-6
    out[..., :3][nz] = rgb[nz] / a[nz][:, None]
    out[..., 3] = a
    return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")


def slice9(img, prefix, corner, out_dir=HERE):
    """Cut a rendered box into GRUB's nine pixmaps: corners corner x corner (never scaled),
    n/s 1 px wide strips (scaled horizontally), e/w 1 px tall strips (scaled vertically),
    c a 1x1 fill."""
    w, h = img.size
    cx, cy = w // 2, h // 2
    pieces = {
        "nw": (0, 0, corner, corner), "n": (cx, 0, cx + 1, corner), "ne": (w - corner, 0, w, corner),
        "w": (0, cy, corner, cy + 1), "c": (cx, cy, cx + 1, cy + 1), "e": (w - corner, cy, w, cy + 1),
        "sw": (0, h - corner, corner, h), "s": (cx, h - corner, cx + 1, h), "se": (w - corner, h - corner, w, h),
    }
    for k, box in pieces.items():
        img.crop(box).save(out_dir / f"{prefix}_{k}.png")


# ---- the boxes ---------------------------------------------------------------------------
def pill_image(w, h, radius, fill, rim=0.28, shade=0.18, rim_px=1.25, shade_px=1.0):
    """A glossy Aqua pill: fill colour, a milky rim along the top edge and a
    slightly darker bottom edge, anti-aliased in its final colour."""
    layers = [
        (rounded_mask(w, h, radius), darken(fill, shade), 1.0),                      # bottom shade
        (rounded_mask(w, h, radius, (0, 0, w, h - shade_px)), lighten(fill, rim), 1.0),  # top rim
        (rounded_mask(w, h, radius, (0, rim_px, w, h - shade_px)), fill, 1.0),        # body
    ]
    return compose(w, h, layers)


def menu_card_image(w, h):
    """Opaque silver card: surface at full opacity with a 2 px control_border edge and a 1 px
    milky inner highlight along the top."""
    r, b = L["menu_radius"], L["menu_border"]
    outer = rounded_mask(w, h, r)
    inner = rounded_mask(w, h, r - b, (b, b, w - b, h - b))
    inner_down = rounded_mask(w, h, r - b, (b, b + 1, w - b, h - b + 1))
    ring = np.clip(outer - inner, 0, 1)
    hl = np.clip(inner - inner_down, 0, 1)
    layers = [
        (ring, PAL["control_border"], 0.95),
        (inner, PAL["surface"], 1.0),
        (hl, "#FFFFFF", 0.16),
    ]
    return compose(w, h, layers)


def bar_track_image(w, h):
    r = L["bar_radius"]
    outer = rounded_mask(w, h, r)
    inner = rounded_mask(w, h, r - 1, (1, 1, w - 1, h - 1))
    ring = np.clip(outer - inner, 0, 1)
    return compose(w, h, [(ring, PAL["control_border"], 0.9), (inner, PAL["elevated"], 1.0)])


def bar_highlight_image(w, h):
    return pill_image(w, h, L["bar_radius"], PAL["accent"], rim=0.30, shade=0.14, rim_px=1.0, shade_px=1.0)


# ---- background --------------------------------------------------------------------------
def ellipse_mask(W, H, cx, cy, rx, ry, blur):
    im = Image.new("L", (W, H), 0)
    ImageDraw.Draw(im).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
    im = im.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(im, dtype=np.float32) / 255.0


def background(W, H, seed=21):
    """Palette ground with translucent ellipses at the edges, a milky highlight on
    their upper rims, a faint blue glow low on the canvas and a soft vignette. The centre
    and the lower half are kept quiet so the menu stays legible."""
    rng = np.random.default_rng(seed)
    base = rgbf(PAL["desktop"])
    # low-frequency blotches + fine grain, both tiny
    blotch = rng.normal(0, 1, (H // 48 + 1, W // 48 + 1)).astype(np.float32)
    blotch = Image.fromarray(np.clip(blotch * 40 + 128, 0, 255).astype(np.uint8))
    blotch = blotch.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(W / 60))
    blotch = (np.asarray(blotch, dtype=np.float32) - 128) / 40
    grain = rng.normal(0, 1, (H, W)).astype(np.float32)
    lum = 1 + blotch * 0.06 + grain * 0.018
    rgb = base[None, None, :] * lum[:, :, None]

    # translucent palette ellipses (cx, cy, rx, ry) in screen fractions, kept off the centre
    ellipses = [
        (0.16, 0.10, 0.44, 0.34),
        (1.00, 0.40, 0.28, 0.58),
        (0.06, 1.02, 0.26, 0.22),
        (0.62, -0.18, 0.30, 0.30),
    ]
    layers = []
    for (cx, cy, rx, ry) in ellipses:
        m = ellipse_mask(W, H, cx * W, cy * H, rx * W, ry * H, blur=3)
        m_down = ellipse_mask(W, H, cx * W, cy * H + 7, rx * W, ry * H, blur=3)
        rim = np.clip(m - m_down, 0, 1)                       # milky upper rim
        inner_glow = np.asarray(Image.fromarray((rim * 255).astype(np.uint8))
                                .filter(ImageFilter.GaussianBlur(40)), dtype=np.float32) / 255.0
        layers.append((m, PAL["surface"], 0.42))
        layers.append((inner_glow, PAL["secondary"], 0.35))
        layers.append((rim, "#FFFFFF", 0.22))
    img = compose(W, H, layers, base=np.clip(rgb, 0, 1))
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0

    # faint blue glow low on the canvas (additive, quadratic falloff)
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - 0.5 * W) / (0.75 * W)) ** 2 + ((yy - 1.12 * H) / (0.55 * H)) ** 2)
    glow = 0.10 * np.clip(1 - d, 0, 1) ** 2
    arr = arr + glow[:, :, None] * rgbf(PAL["accent"])[None, None, :]

    # vignette
    dv = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    arr *= (1 - 0.28 * np.clip(dv - 0.45, 0, 1) ** 1.5)[:, :, None]
    return Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")


# ---- icons (64x64 RGBA, foreground/muted only) -------------------------------------------
def _icon_canvas(n=256):
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def _finish(im, size=64):
    return im.resize((size, size), Image.LANCZOS)


def rgba(h, a=1.0):
    return (*hex2rgb(h), int(round(a * 255)))


FG, MU, SF = PAL["foreground"], PAL["muted"], PAL["surface"]


def icon_ubuntu():
    im, d = _icon_canvas()
    d.ellipse([20, 20, 236, 236], outline=rgba(FG), width=26)
    d.ellipse([92, 92, 164, 164], fill=rgba(FG))
    return _finish(im)


def icon_gnu_linux():
    im, d = _icon_canvas()
    # a plain penguin silhouette: body, belly, head
    d.ellipse([48, 88, 208, 248], fill=rgba(FG))
    d.ellipse([84, 128, 172, 236], fill=rgba(SF))
    d.ellipse([80, 24, 176, 120], fill=rgba(FG))
    d.ellipse([104, 60, 122, 78], fill=rgba(SF))
    d.ellipse([134, 60, 152, 78], fill=rgba(SF))
    d.polygon([(116, 86), (140, 86), (128, 102)], fill=rgba(MU))
    return _finish(im)


def icon_efi():
    im, d = _icon_canvas()
    cx = cy = 128
    for i in range(8):
        a = i * math.pi / 4
        x, y = cx + 88 * math.cos(a), cy + 88 * math.sin(a)
        d.ellipse([x - 26, y - 26, x + 26, y + 26], fill=rgba(FG))
    d.ellipse([44, 44, 212, 212], fill=rgba(FG))
    d.ellipse([90, 90, 166, 166], fill=(0, 0, 0, 0))
    return _finish(im)


def icon_recovery():
    im, d = _icon_canvas()
    d.ellipse([20, 20, 236, 236], fill=rgba(FG))
    d.ellipse([84, 84, 172, 172], fill=(0, 0, 0, 0))
    for i in range(4):
        a0 = i * 90 + 25
        d.pieslice([20, 20, 236, 236], a0, a0 + 40, fill=rgba(MU))
    d.ellipse([84, 84, 172, 172], fill=(0, 0, 0, 0))
    return _finish(im)


def icon_windows():
    im, d = _icon_canvas()
    for (x, y) in ((28, 28), (134, 28), (28, 134), (134, 134)):
        d.rounded_rectangle([x, y, x + 94, y + 94], radius=14, fill=rgba(FG))
    return _finish(im)


def icon_macosx():
    im, d = _icon_canvas()
    d.rounded_rectangle([32, 40, 224, 216], radius=18, outline=rgba(FG), width=14)
    d.line([48, 172, 208, 172], fill=rgba(FG), width=12)
    d.ellipse([174, 187, 188, 201], fill=rgba(MU))
    return _finish(im)


ICON_FUNCS = {
    "ubuntu": icon_ubuntu, "gnu-linux": icon_gnu_linux, "efi": icon_efi,
    "recovery": icon_recovery, "windows": icon_windows, "macosx": icon_macosx,
}


# ---- logo (fallback emblem if artwork/grub-logo.png is absent) ---------------------------
def emblem(size=240):
    n = size * SS
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # silver palette puck
    d.ellipse([0, 0, n - 1, n - 1], fill=rgba(PAL["control_border"], 0.95))
    d.ellipse([3 * SS, 3 * SS, n - 1 - 3 * SS, n - 1 - 3 * SS], fill=rgba(PAL["surface"], 0.88))
    # six pale-to-deep blue bands, light to dark top to bottom, clipped to an inner disc
    disc = Image.new("L", (n, n), 0)
    ImageDraw.Draw(disc).ellipse([n * 0.22, n * 0.22, n * 0.78, n * 0.78], fill=255)
    bands = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bands)
    y0, y1 = n * 0.22, n * 0.78
    for i, col in enumerate(RAINBOW):
        ya = y0 + (y1 - y0) * i / len(RAINBOW)
        yb = y0 + (y1 - y0) * (i + 1) / len(RAINBOW)
        bdraw.rectangle([0, ya, n, yb], fill=rgba(col))
    im.paste(bands, mask=disc)
    # milky highlight crescent along the top of the puck
    hl = Image.new("L", (n, n), 0)
    hd = ImageDraw.Draw(hl)
    hd.ellipse([3 * SS, 3 * SS, n - 1 - 3 * SS, n - 1 - 3 * SS], fill=255)
    hd.ellipse([3 * SS, 3 * SS + 5 * SS, n - 1 - 3 * SS, n - 1 - 3 * SS + 5 * SS], fill=0)
    im.paste(rgba("#FFFFFF", 0.18), mask=hl)
    return im.resize((size, size), Image.LANCZOS)


# ---- fonts ---------------------------------------------------------------------------------
def pf2_name(path):
    data = path.read_bytes()
    pos = 0
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        ln = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        if tag == b"NAME":
            return data[pos + 8:pos + 8 + ln].rstrip(b"\0").decode()
        if tag == b"DATA":
            break
        pos += 8 + ln
    raise RuntimeError(f"no NAME section in {path}")


class PF2:
    """Minimal PFF2 reader: enough to draw strings exactly as GRUB does (1-bit glyphs,
    no anti-aliasing, no kerning) so the mockup shows real boot-menu type."""

    def __init__(self, path):
        data = path.read_bytes()
        sections, pos = {}, 0
        while pos + 8 <= len(data):
            tag = data[pos:pos + 4]
            ln = struct.unpack(">I", data[pos + 4:pos + 8])[0]
            if tag == b"DATA":
                break
            sections[tag] = data[pos + 8:pos + 8 + ln]
            pos += 8 + ln
        self.name = sections[b"NAME"].rstrip(b"\0").decode()
        self.ascent = struct.unpack(">H", sections[b"ASCE"])[0]
        self.descent = struct.unpack(">H", sections[b"DESC"])[0]
        self.glyphs = {}
        chix = sections[b"CHIX"]
        for i in range(0, len(chix), 9):
            cp, _flags, off = struct.unpack(">IBI", chix[i:i + 9])
            w, h, xo, yo, dw = struct.unpack(">HHhhh", data[off:off + 10])
            n = (w * h + 7) // 8
            bits = np.unpackbits(np.frombuffer(data[off + 10:off + 10 + n], dtype=np.uint8))[:w * h]
            self.glyphs[cp] = (w, h, xo, yo, dw, bits.reshape(h, w) * 255 if w and h else None)

    def height(self):
        return self.ascent + self.descent

    def width(self, text):
        return sum(self.glyphs.get(ord(c), self.glyphs[ord("?")])[4] for c in text)

    def draw(self, img, text, x, baseline, colour):
        for c in text:
            w, h, xo, yo, dw, bits = self.glyphs.get(ord(c), self.glyphs[ord("?")])
            if bits is not None:
                img.paste(colour, (x + xo, baseline - yo - h), Image.fromarray(bits.astype(np.uint8), "L"))
            x += dw


def build_fonts():
    names = {}
    for out, (ttf, name, flags) in FONTS.items():
        if not ttf.exists():
            raise SystemExit(f"missing font source {ttf}")
        subprocess.run([GRUB_MKFONT, "-s", str(FONT_PX), "-r", FONT_RANGES, "-n", name, *flags,
                        "-o", str(HERE / out), str(ttf)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        names[out] = pf2_name(HERE / out)
    if len(set(names.values())) != len(names):
        raise RuntimeError(f"font names collide (GRUB looks fonts up by name): {names}")
    return names


# ---- theme.txt -------------------------------------------------------------------------------
def theme_template(font_names):
    f_reg, f_bold, f_mono = font_names["font.pf2"], font_names["font-bold.pf2"], font_names["font-mono.pf2"]
    return f"""# @theme_name@ -- GRUB gfxmenu theme (generated by generate.py; do not hand-edit theme.txt)
# Sized for the native 2880x1800 framebuffer (GRUB_GFXMODE=auto): all numbers are 2x.
title-text: ""
desktop-image: "background.png"
desktop-image-scale-method: "stretch"
desktop-color: "@desktop@"
terminal-font: "{f_mono}"
terminal-left: "0"
terminal-top: "0"
terminal-width: "100%"
terminal-height: "100%"
terminal-border: "0"

# original GD monogram
+ image {{
  left = 50%-{L['logo'] // 2}
  top = 24%
  width = {L['logo']}
  height = {L['logo']}
  file = "logo.png"
}}

# opaque silver card with blue selection pill
+ boot_menu {{
  left = 50%-{L['menu_w'] // 2}
  top = 50%
  width = {L['menu_w']}
  height = {L['menu_h_pct']}%
  item_font = "{f_reg}"
  selected_item_font = "{f_bold}"
  item_color = "@foreground@"
  selected_item_color = "@selection_foreground@"
  icon_width = {L['icon']}
  icon_height = {L['icon']}
  item_icon_space = {L['icon_space']}
  item_height = {L['item_height']}
  item_padding = {L['item_padding']}
  item_spacing = {L['item_spacing']}
  menu_pixmap_style = "menu_*.png"
  selected_item_pixmap_style = "select_*.png"
  scrollbar = true
  scrollbar_width = {L['scrollbar_w']}
  scrollbar_slice = "east"
  scrollbar_right_pad = 5
  scrollbar_frame = "bar_*.png"
  scrollbar_thumb = "hl_*.png"
}}

# countdown bar
+ progress_bar {{
  id = "__timeout__"
  left = 50%-{L['bar_w'] // 2}
  top = 88%
  width = {L['bar_w']}
  height = {L['bar_h']}
  show_text = false
  text = ""
  bar_style = "bar_*.png"
  highlight_style = "hl_*.png"
  highlight_overlay = true
}}

# countdown label
+ label {{
  id = "__timeout__"
  left = 35%
  top = 90%+8
  width = 30%
  align = "center"
  font = "{f_reg}"
  color = "@muted@"
  text = "@theme_name@ · starting in %d seconds"
}}
"""


def render_theme(template):
    out = template
    for k, v in PAL.items():
        out = out.replace(f"@{k}@", v)
    out = out.replace("@theme_name@", THEME_NAME).replace("@slug@", SLUG)
    if "@" in out:
        raise RuntimeError("unrendered token in theme.txt")
    return out


# ---- mockup (emulates GRUB's gfxmenu layout, then halves to 1440x900) ------------------------
MOCK_ENTRIES = [
    ("Linux Mint 22.3 Cinnamon", "ubuntu"),
    ("Advanced options for Linux Mint 22.3 Cinnamon", None),
    ("UEFI Firmware Settings", None),
]


def mockup(bg, logo, icons, seconds=3, frac=0.6):
    W, H = SCREEN
    scene = bg.convert("RGBA")
    scene.alpha_composite(logo, (W // 2 - L["logo"] // 2, int(0.24 * H)))

    mx, my, mw, mh = W // 2 - L["menu_w"] // 2, H // 2, L["menu_w"], int(L["menu_h_pct"] / 100 * H)
    scene.alpha_composite(menu_card_image(mw, mh), (mx, my))

    pad = L["menu_corner"] + L["item_padding"]
    x0, y0 = mx + pad, my + pad
    cwidth = mw - 2 * pad
    sel_pad = L["pill_corner"]
    item_top = y0 + sel_pad                       # GRUB starts the list max_toppad down
    f_reg, f_bold = PF2(HERE / "font.pf2"), PF2(HERE / "font-bold.pf2")
    for i, (title, cls) in enumerate(MOCK_ENTRIES):
        sel = i == 0
        if sel:
            pill = pill_image(cwidth, L["item_height"] + 2 * sel_pad, L["pill_radius"], PAL["selection"])
            scene.alpha_composite(pill, (x0, item_top - sel_pad))
        if cls and cls in icons:
            scene.alpha_composite(icons[cls], (x0 + sel_pad, item_top + (L["item_height"] - L["icon"]) // 2))
        tx = x0 + sel_pad + L["icon"] + L["icon_space"]
        font = f_bold if sel else f_reg
        baseline = item_top + (L["item_height"] - font.height()) // 2 + font.ascent   # gui_list.c
        font.draw(scene, title, tx, baseline, rgba(PAL["selection_foreground"] if sel else PAL["foreground"]))
        item_top += L["item_height"] + L["item_spacing"]

    bx, by, bw, bh = W // 2 - L["bar_w"] // 2, int(0.88 * H), L["bar_w"], L["bar_h"]
    scene.alpha_composite(bar_track_image(bw, bh), (bx, by))
    hw = max(2 * L["bar_radius"], int(bw * frac))
    scene.alpha_composite(bar_highlight_image(hw, bh), (bx, by))
    text = f"{THEME_NAME} \u00b7 starting in {seconds} seconds"
    lx, lw = int(0.35 * W), int(0.30 * W)
    f_reg.draw(scene, text, lx + (lw - f_reg.width(text)) // 2, int(0.90 * H) + 8 + f_reg.ascent, rgba(PAL["muted"]))
    return scene.convert("RGB").resize((1440, 900), Image.LANCZOS)


# ---- main --------------------------------------------------------------------------------------
def main():
    ICONS.mkdir(exist_ok=True)
    print("fonts ...")
    names = build_fonts()
    for k, v in names.items():
        print(f"  {k}: {v!r}")

    print("pixmaps ...")
    slice9(pill_image(200, L["item_height"], L["pill_radius"], PAL["selection"]), "select", L["pill_corner"])
    slice9(menu_card_image(400, 200), "menu", L["menu_corner"])
    slice9(bar_track_image(200, L["bar_h"]), "bar", L["bar_corner"])
    slice9(bar_highlight_image(200, L["bar_h"]), "hl", L["bar_corner"])

    print("icons ...")
    icons = {}
    for name, fn in ICON_FUNCS.items():
        icons[name] = fn()
        icons[name].save(ICONS / f"{name}.png")
    icons["macosx"].save(ICONS / "osx.png")        # os-prober tags macOS entries --class osx

    print("logo ...")
    art_logo = PRESET / "artwork" / "grub-logo.png"
    if art_logo.exists():
        logo = Image.open(art_logo).convert("RGBA")
        if logo.size != (L["logo"], L["logo"]):
            logo = logo.resize((L["logo"], L["logo"]), Image.LANCZOS)
        logo.save(HERE / "logo.png")
        print(f"  reused {art_logo}")
    else:
        logo = emblem(L["logo"])
        logo.save(HERE / "logo.png")
        print("  artwork/grub-logo.png absent: drew the fallback emblem")

    print("background ...")
    bg = Image.open(PRESET / "artwork/wallpaper.png").convert("RGB").resize(SCREEN, Image.LANCZOS)
    # Opaque silver caption strip guarantees label contrast over the wallpaper.
    ImageDraw.Draw(bg).rounded_rectangle((860, 1605, 2020, 1700), radius=12, fill=PAL["surface"], outline=PAL["control_border"], width=2)
    bg.save(HERE / "background.png")

    print("theme.txt ...")
    template = theme_template(names)
    (HERE / "theme.txt.in").write_text(template)
    (HERE / "theme.txt").write_text(render_theme(template))

    print("mockup ...")
    mockup(bg, logo, icons).save(HERE / "mockup.png")

    card = PAL["surface"]
    print("contrast:")
    print(f"  item text {PAL['foreground']} on card {card}: {contrast(PAL['foreground'], card):.2f}:1")
    print(f"  selected text {PAL['selection_foreground']} on pill {PAL['selection']}: "
          f"{contrast(PAL['selection_foreground'], PAL['selection']):.2f}:1")
    print(f"  timeout label {PAL['muted']} on caption {PAL['surface']}: {contrast(PAL['muted'], PAL['surface']):.2f}:1")
    print("done")


if __name__ == "__main__":
    main()
