# Shared desktop design specification

## Starting point

The first framework audit on 2026-09-19 America/Chicago found **Dusk-Ribbons**, not the previously saved Nocturne or Ocean Silk selection. The measurements below describe that preserved baseline. New work is tracked separately in `presets/pastel-leather/design.json` and its transaction receipts.

Use the current Dusk design as a documented baseline, not as a requirement that future themes share its colors. Each new theme must provide a unique exact visible name, source artwork references, a palette, typography, surface geometry, and its own coverage manifest before workers begin styling.

Canonical current palette source:
`/home/bertmacklen/Documents/eucalyptus-felt-cinnamon/presets/dusk-ribbons/palette.json`

| Role | Current Dusk baseline |
|---|---|
| Background | `#2d2c41` |
| Surface | `#373950` |
| Accent | `#bb8987` |
| Main text | `#f1e3df` |
| Border / slate | `#756172` |
| Muted / mist | `#d3adb0` |
| Elevated surface | `#48445b` |

Read final stylesheet overrides as well as palette JSON: the selected menu foreground still contains `#17201b`, outside the palette. This is an audit finding, not a new shared token.

Typography observed in the final Cinnamon overrides: Ubuntu 10pt panel; Ubuntu 12pt, weight 600 centered clock. Preserve the audited interface/document/monospace selections unless the new design explicitly changes them. Current panel allocation is 78px top, 64px visible section and 76px bottom dock. Observed radii include workspace 4px, applet 7px, notification 12px, popup 14px and dock 20px. These are starting measurements; verify physical scale on the actual 2880x1800 display.

Wallpaper: `/home/bertmacklen/Pictures/Wallspaper/alivedusk-ribbons.png` (1672x941 source). Preserve the supplied file and its aspect ratio. New edits go into the new preset's assets; never overwrite the source. Record source/output hashes and any cropping/scaling decision. Preserve alpha, lettering and geometry for supplied logos.

## Contract for every new theme

1. **Identity:** exact theme name across user theme, system login copy, shell and presets; author/source/license attribution for reused assets.
2. **Tokens:** every preset fills in the full `palette` contract below inside `design.json` (this matches `presets/_template/design.json` exactly; the lineage builders read these by name, e.g. `disabled` at `desktop/build.py` lines 17/34/437). Each token needs an explicit value; do not silently guess missing colors across apps. Terminal ANSI colors are defined separately as the 16-entry `terminal_ansi` array.

   | Token | Meaning | Contrast requirement |
   |---|---|---|
   | `desktop` | Wallpaper-adjacent/root canvas fill behind panels and icons | none fixed; keep it distinct from `background` |
   | `background` | Primary window/app canvas fill | baseline surface for the `foreground`/`muted` floor below |
   | `surface` | Raised panel/card fill | `foreground`/`muted` text on it needs ≥4.5:1 |
   | `elevated` | Dialog/popover/menu fill | `foreground`/`muted` text on it needs ≥4.5:1 |
   | `foreground` | Default body text | ≥4.5:1 on `background`, `surface` and `elevated` |
   | `muted` | Secondary/placeholder text | ≥4.5:1 on `background`, `surface` and `elevated` |
   | `disabled` | Inactive control fill/ink (switches, checkboxes, unfocused window chrome) | ≥3:1 on `background`, `surface` and `elevated` |
   | `accent` | Primary brand highlight | ≥4.5:1 only if used as text; otherwise fill-only and must be declared so in `semantic_rules` |
   | `secondary` | Secondary highlight/alternate accent | same text-contrast rule as `accent` |
   | `selection` | Selected-row/highlight fill | pairs with `selection_foreground` |
   | `selection_foreground` | Text drawn on `selection` | ≥4.5:1 on `selection` |
   | `focus` | Focus ring/outline | ≥3:1 on `background`, `surface` and `elevated` |
   | `border` | General decorative separator/outline | no fixed floor; must stay visually distinct from `surface`/`elevated` |
   | `control_border` | Interactive control edge (buttons, entries, checkboxes) | ≥3:1 on `background`, `surface` and `elevated` |
   | `success` / `warning` / `error` | Status colors | ≥4.5:1 only if used as text; otherwise fill-only per `semantic_rules` |
   | `terminal` / `terminal_foreground` | Terminal app background/foreground pair | `terminal_foreground` ≥4.5:1 on `terminal` |
   | `island_background` / `island_foreground` | Clock/status island fill and its text | `island_foreground` ≥4.5:1 on `island_background` |
   | `top_background` / `top_foreground` | Top panel fill and its text | `top_foreground` ≥4.5:1 on `top_background` |

   Record every measured ratio in the preset's own `semantic_rules`; a token that cannot clear its floor must be marked fill-only there rather than silently used as text.
3. **Typography and geometry:** UI/monospace family, scale, titlebar/clock sizes, spacing, radii, panel/dock dimensions, icon/cursor sizes.
   A new theme must have its own visual composition: panel group shapes, widget arrangement, application headers and control geometry. Matching means sharing palette and materials across these surfaces; recoloring the previous layout alone does not meet this user's requirement.
4. **States:** normal, hover, selected, focused, disabled, urgent; test text contrast against actual translucent backgrounds. Inspect maximized, inactive and modal windows.
5. **Motion and translucency:** record intended alpha and animation behavior; do not stack opacity mechanisms blindly. Current capture shows overlapping translucent windows/widgets, so readability needs review before extending this behavior.
6. **Coverage:** every row in `docs/coverage.md` is assigned to a worker or explicitly unsupported/not installed, with a reason. Add rows for newly installed applications. Toolkit coverage never proves every application is themed.
7. **Preservation:** keep custom music commands, exact menu name `cinabon`, existing unrelated application preferences and original artwork. Protected mixed MPV/Cava files require a narrow reviewed key-level adapter before their appearance may be changed.

## Verification record

For each surface track these independently: assets built; configuration selected; live screenshot path; interaction checked; login/reboot checked if applicable; unsupported reason; timestamp. A selected theme string is only configuration evidence. A passing file hash check is not a visual test.

Current visual evidence is described in `docs/visual-audit.md`. Menu expansion, notifications, OSD, app dialogs, lock/unlock, login and boot are still unverified.
