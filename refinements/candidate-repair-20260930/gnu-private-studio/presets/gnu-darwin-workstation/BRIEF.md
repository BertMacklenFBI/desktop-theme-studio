# GNU-Darwin Workstation

Exact identity **GNU-Darwin Workstation**, slug `gnu-darwin-workstation`. User chose the **gray scientific workstation with square dock icons**. This is a separate source-led reconstruction; GNU-Darwin Aqua remains preserved.

## Authoritative visual reference

[Original GNU-Darwin project today.png, archived 2002-10-16](https://web.archive.org/web/20021016083440id_/http://gnu-darwin.sourceforge.net/today.png), local `proposals/gnu-darwin-archive/today-2002.png`, 1280×1024, SHA-256 `db17eb8f3514c33378f4ede46482174d90ec9d1cb7681b1b7e5ac7ad7683510e`. Provenance and measured pixels are in `identity/source-samples.json`.

This screenshot is the chosen appearance target. It is not proven to be beta2.5's default: the inspected archive has an empty GNU package directory and conflicting WindowMaker version references. It cannot supply original theme files or wallpaper. See the separate package-research report. No sampled colour is guessed from the GNU-Darwin name.

## Measured direction

Utility canvas at (300,60): #BEBEBE; app chrome at (600,45): #B3B2B0; inactive title (100,12): #9EA0AC; active dark title (300,164)/(700,164): #21232B/#0E0F13; dark dock tile (1220,500): #333641. Uncovered desktop (400,980) is #000000. The right dock occupies x1216–1279, 64px wide; bottom tiles occupy y960–1023, 64px tall. Titles are about22px high. Square bevels and compact gray controls are the identity—not silver gloss, blue controls or decorative wallpaper.

Unlike Aqua's top bar/centered dock and GNUOS NT's bottom taskbar, this theme uses a right-edge square launcher column plus bottom application/status tiles, black empty workspace and white-on-black terminal. No top panel. UI/title Liberation Sans10 and terminal Liberation Mono10 are installed nearest practical compact fonts, not falsely claimed original bitmap fonts. Cursor24, square controls, radius0.

| Feature | Literal reference | Cinnamon adaptation / limit |
|---|---|---|
| Workspace | Black visible uncovered pixels | Solid black wallpaper; hidden pixels cannot be established; no invented branding |
| Window chrome | Gray canvases, slate inactive title, near-black active title, white active text | Modern GTK2/3/4 and Metacity styles; enforce contrast across actual app states |
| Buttons | Square bevels, minimize left / close right | Existing Cinnamon window actions; maximize remains accessible through title double-click/menu |
| Right dock |64px square launcher tiles| Native vertical panel, 48px installed app icons within64px pitch; preserve icon provenance |
| Bottom edge | Square running-app/status tiles, gaps of black | Horizontal menu with exact `le cinabon` label, grouped task tiles and status/calendar; menu label needs a wider horizontal affordance |
| App content | Scientific PyMOL/AbiWord/terminal session | Do not recreate documents, molecules, file names or playback; style installed application chrome only |
| Icons | Historical application artwork | Use exact recovered icons only with asset provenance/rights; otherwise existing installed application icons, documented substitution |
| Text | Compact bitmap-era appearance | Installed Liberation Sans/Mono substitutes; antialiasing and metrics may differ |
| Status colour | Legacy widget-specific colours | Accessible dark semantic colours on gray; white terminal text remains literal |

## Construction requirements

Both panels64 logical pixels; right launcher tiles64 square. Bottom menu remains horizontal and preserves exact label. Right/bottom corner is owned once, with no overlap. Status glyphs20, application art48 within tile. Modern bottom calendar is a compact integration adaptation, not a fabricated scientific dockapp. All panels/popups opaque unless an empty bottom segment needs transparent black workspace; no rounded enclosing dock body.

Active titles use white on sampled dark slate gradient. Inactive title uses #101010 on #9EA0AC. Primary controls remain gray/black with bevel, selected state slate/white. Disabled ink #595959, no extra opacity. Document content may remain white. No soft shadows, gradients beyond the sampled active title, texture, traffic lights, Apple logos or Aqua waves.

All68 coverage rows are assigned in design.json; unsupported rows retain reasons. Builders must retain separate built/configured/isolated-render/interaction/boot/login evidence. Carbon Typewriter host, Aqua/NT assets, original Fastfetch art and shell/music commands remain preserved.

## Lint and census

Lint exits0 with0 errors and1 hue warning: #333641 is13.7 degrees from Aqua's blue accent. This is accepted for fidelity to the sampled original dock slate; the layouts/materials are fundamentally different. All mandatory contrast pairs and installed fonts pass. Full linter output:

```text
design_lint: /home/bertmacklen/Documents/desktop-theme-studio/presets/gnu-darwin-workstation/design.json

Contrast (WCAG 2.x)
fg                    bg          fg hex   bg hex   ratio  min  status
--------------------  ----------  -------  -------  -----  ---  ------
foreground            background  #101010  #BEBEBE  10.24  4.5  ok
foreground            surface     #101010  #B3B2B0  8.98   4.5  ok
foreground            elevated    #101010  #D0D0D0  12.34  4.5  ok
muted                 background  #383838  #BEBEBE  6.31   4.5  ok
muted                 surface     #383838  #B3B2B0  5.53   4.5  ok
muted                 elevated    #383838  #D0D0D0  7.60   4.5  ok
selection_foreground  selection   #FFFFFF  #333641  12.04  4.5  ok
terminal_foreground   terminal    #FFFFFF  #000000  21.00  4.5  ok
control_border        background  #595959  #BEBEBE  3.77   3.0  ok
control_border        surface     #595959  #B3B2B0  3.31   3.0  ok
control_border        elevated    #595959  #D0D0D0  4.54   3.0  ok
focus                 background  #333641  #BEBEBE  6.48   3.0  ok
focus                 surface     #333641  #B3B2B0  5.68   3.0  ok
focus                 elevated    #333641  #D0D0D0  7.80   3.0  ok
disabled              background  #595959  #BEBEBE  3.77   3.0  ok
disabled              surface     #595959  #B3B2B0  3.31   3.0  ok
disabled              elevated    #595959  #D0D0D0  4.54   3.0  ok
accent                background  #333641  #BEBEBE  6.48   4.5  ok

ANSI on terminal: 14/14 slots >= 3.0:1 (slots 0 and 8 exempt)

Fonts: ui_family=Liberation Sans (ok), title_family=Liberation Sans (ok), clock_family=Liberation Mono (ok), monospace_family=Liberation Mono (ok)

Hue census: accent #333641 hue 227 deg
source                                                    accent   hue  dist   status
--------------------------------------------------------  -------  ---  -----  ----------------------------
presets/gnu-darwin-aqua                                   #4384D6  214  13.7   CLOSE
presets/indigo-lunchbox                                   #6157A3  248  20.8   ok
presets/macintosh-soft                                    #507D7D  180  47.1   ok
presets/amethyst-arcade                                   #CF7BF5  281  54.2   ok
themes/SILO/config/mint-dashboard/theme.json              #1FC9A6  168  59.5   ok
presets/moonstone-stereo                                  #8DB4A0  149  77.9   ok
presets/marzipan-crown                                    #AD2C80  321  93.8   ok
presets/peony-coronet                                     #EE60AA  329  101.6  ok
themes/CATPUCCINO MOCHA/config/mint-dashboard/theme.json  #A6E3A1  116  111.7  ok
presets/plum-afterglow                                    #D5A1AA  350  122.5  ok
presets/pastel-leather                                    #DE999E  356  128.5  ok
presets/quiet-sage                                        #A8B99A  93   134.2  ok
presets/red-panda-overtime                                #FF6257  4    136.8  ok
presets/dot-matrix-pea                                    #8C9958  72   155.1  ok
presets/tangerine-graphite                                #E0915A  25   157.5  ok
presets/tidal-observatory                                 #EFC58A  35   167.9  ok
presets/carbon-typewriter                                 #D6D3D0  30   -      achromatic; hue not compared
presets/gnuos-nt                                          #717980  208  -      achromatic; hue not compared

Findings
severity  check  path              message
--------  -----  ----------------  -------------------------------------------------------------------------
warning   hue    $.palette.accent  accent #333641 (hue 227) is 13.7 deg from presets/gnu-darwin-aqua #4384D6

Result: 0 error(s), 1 warning(s) -> exit 0
Wrote /home/bertmacklen/Documents/desktop-theme-studio/presets/gnu-darwin-workstation/identity/contrast.json
```

No user choice remains. Machine-readable panel_layout is embedded: panel1 right64 launchers, panel2 bottom64 horizontal menu/tasks/status/calendar. Integration must resolve launcherList from installed desktop IDs before build and support three independently journaled new applet instances. Ready for independent design review.

Asset amendment: use the licensed recovered WindowMaker package artwork in `proposals/gnu-darwin-archive/windowmaker-assets/extracted` with its provenance, mapping defaultterm/GNUterm/pdf/wilber/staroffice2/write/GNUstepGlow/clip where appropriate. Modern package assets are period-style substitutes, not proven identical historical screenshot bytes. Bottom panel owns the lower-right corner; right rail stops above it. Stock grouped tasks represent running windows rather than exclusively minimized/free-positioned WindowMaker miniwindows.

## Resource/network monitor amendment

User requested a period-appropriate resource monitor. Add one native `gnu-darwin-workstation-monitor@desktop-theme-studio` applet at bottom-right position0, preceding existing statuses and clock in their unchanged relative order. It comprises four64×64px tiles (256×64 total): **CPU, MEM, RX, TX**. Existing15 right-dock launchers, base palette, artwork and window geometry are preserved. This is a WMMon/WMNet-style descriptive adaptation, not identification or replication of a particular original applet.

Each square gray bezel encloses a black display with9px mono labels,10px numeric values and a separate54-column graph. Existing ANSI green #77CC77, amber #CCCC77 and cyan #77CCCC distinguish graph series; neutral #DDDDDD labels remain readable. White/black, green/black, amber/black and cyan/black contrasts are explicitly recorded in semantic_rules.resource_monitor. Labels and numbers carry meaning independently of colour. Rate strings fit typical six-character displays, with full binary units/interface in tooltip.

A single in-process1Hz timer reads host /proc counters; no polling subprocesses, network requests or service installation. CPU uses aggregate deltas; memory uses MemAvailable; network uses a single active default-route interface to avoid double-counting. Actual elapsed monotonic time determines RX/TX rate. CPU/MEM graphs use fixed percentages; network graphs share a rolling scale with its ceiling explained in tooltip.

Initial/reset/read-error/no-interface states display `--` and graph gaps. Valid zero displays zero. Counter decreases/interface changes rebaseline; long stalls discard old CPU/network deltas. CPU/MEM >=90% gains a visible `!` and existing ANSI warning ink without flashing. Network has no invented alarm threshold. Remove timeout when applet is removed. Exact implementation rules are in resource_monitor. All three new applet instances require independently journaled allocation and restoration.
