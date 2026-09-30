#!/usr/bin/env python3
"""Apply the Eww palette that corresponds to Cinnamon's active GTK theme."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
STATE = ROOT / "state"
PALETTES = CONFIG / "themes" / "palettes.json"
STYLESHEET = CONFIG / "eww.scss"
MUSIC_ARTWORK = CONFIG / "assets" / "music.svg"
THEME_STATE = STATE / "theme.json"
ALIASES = {"Eucalyptus Felt": "Eucalyptus-Glass"}
START = "// THEME_VARIABLES_START — maintained by scripts/theme-sync.py."
END = "// THEME_VARIABLES_END"


def active_theme():
    result = subprocess.run(
        ["gsettings", "get", "org.cinnamon.desktop.interface", "gtk-theme"],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    return result.stdout.strip().strip("'")


def palettes():
    return json.loads(PALETTES.read_text(encoding="utf-8"))


def resolve(requested, available):
    name = ALIASES.get(requested, requested)
    return name if name in available else "Graphite-Brass"


def render_variables(palette):
    lines = [START]
    for key in (
        "base", "surface0", "surface1", "border", "text", "muted", "accent",
        "secondary", "battery", "tile", "accent-hover", "indeterminate", "selection-text",
    ):
        value = selection_text(palette) if key == "selection-text" else palette[key]
        lines.append(f"${key}: {value};")
    lines.append(END)
    return "\n".join(lines)


def relative_luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722)))


def selection_text(palette):
    """Choose an existing profile color that stays readable on its accent."""
    def contrast(color):
        lighter, darker = sorted((relative_luminance(color), relative_luminance(palette['accent'])), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)
    return max((palette['base'], palette['text']), key=contrast)


def render_music_artwork(palette):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" '
        'viewBox="0 0 180 180">'
        f'<rect width="180" height="180" rx="22" fill="{palette["surface0"]}"/>'
        f'<circle cx="90" cy="90" r="67" fill="{palette["base"]}" '
        f'stroke="{palette["border"]}"/>'
        f'<circle cx="90" cy="90" r="51" fill="none" '
        f'stroke="{palette["surface1"]}" stroke-width="8"/>'
        f'<circle cx="90" cy="90" r="24" fill="{palette["accent"]}"/>'
        f'<circle cx="90" cy="90" r="6" fill="{palette["base"]}"/>'
        '</svg>\n'
    )


def write_if_changed(path, content):
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def apply(requested=None):
    available = palettes()
    cinnamon_theme = requested or active_theme()
    name = resolve(cinnamon_theme, available)
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    begin = stylesheet.index(START)
    finish = stylesheet.index(END, begin) + len(END)
    updated = stylesheet[:begin] + render_variables(available[name]) + stylesheet[finish:]
    write_if_changed(STYLESHEET, updated)
    write_if_changed(MUSIC_ARTWORK, render_music_artwork(available[name]))
    STATE.mkdir(parents=True, exist_ok=True)
    state = {"cinnamon_theme": cinnamon_theme, "palette": name, "selection_text": selection_text(available[name]), **available[name]}
    write_if_changed(THEME_STATE, json.dumps(state, indent=2) + "\n")
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", help="Apply this known Cinnamon theme name")
    parser.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args()
    state = apply(args.theme)
    if args.show:
        print(f"{state['palette']} ({state['label']})")


if __name__ == "__main__":
    main()
