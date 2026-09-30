# GNU-Darwin Aqua

Exact identity: **GNU-Darwin Aqua**, slug **gnu-darwin-aqua**. Draft design; no live activation.

A light Panther/Tiger Aqua desktop with original GNU-Darwin branding. Pale horizontal pinstripes sit beside restrained brushed-silver headers, a glossy blue action language and left-side close/minimize/maximize traffic lights. A continuous 34px top panel contains the preserved `le cinabon` label at left and status/clock at right. A transparent 68px bottom panel actor paints only its centered dock body; 44px launcher icons sit inside that compact silver tray. Top-panel icons are 20px. No floating central clock island.

This is a different composition from Tangerine Graphite's dark polycarbonate islands, GNUOS NT's bottom taskbar and Carbon Typewriter's current desktop. It uses light opaque text surfaces, centered dark window titles, circular window controls, a white content field and original blue flowing wallpaper. The source screenshots guide proportions and material cues, not copied platform assets. Cinnamon's top panel is not a global application-menu implementation.

## Source references and measured colours

- [Tiger screenshot library, 512 Pixels](https://512pixels.net/projects/aqua-screenshot-library/mac-os-x-10-4-tiger/) — `identity/reference-tiger.png`, downloaded from https://media.512pixels.net/downloads/macos-screenshots/10-4/10-4-Tiger-Finder-Menu-scaled.png . Pixel (55,55) is #EEF0F2; selected menu at (55,35) is #4384D6; wallpaper at (100,900) is #265098 and at (400,400) is #649BC8.
- [Aqua retrospective, 512 Pixels](https://512pixels.net/2014/04/aqua-past-future/) — `identity/reference-finder.png`, downloaded from https://i0.wp.com/512pixels.net/wp-content/uploads/S3/tiger.png . Brushed header samples are #C8C8C8 / #CCCCCC; the content pane is #FFFFFF and sidebar selection is #0176DF.
- [Apple period announcement](https://www.apple.com/newsroom/2005/04/12Apple-to-Ship-Mac-OS-X-Tiger-on-April-29/) — historical reference from handoff; no historical claims beyond the user's supplied direction are required to build the theme.

Reference images remain reference-only with original rights retained by their owners; they must not be installed or bundled as theme artwork. Exact sample coordinates and SHA-256 hashes are in `identity/source-samples.json`. The original screenshot content is Apple UI, reproduced here solely for local design reference. New wallpaper, textures and GD letter monogram must be original; no fabricated official GNU-Darwin or Apple mark.

The sampled #4384D6 blue is deliberately retained as a fill-only decorative accent. Darker #245FA8 selection provides white-label contrast. #265098 supplies text-safe blue links and the wallpaper base. Silver surfaces are lifted from the sampled brushed-metal range to #E2E4E7 for clearer light-theme hierarchy; text is dark #202328 and muted #535A64. Header texture stays subtle and text panels remain opaque/untextured. Full checked ratios, including status and panel pairs beyond the linter's core checks, are in `semantic_rules.contrast`.

Liberation Sans 11pt and Liberation Mono 11pt are installed substitutes; clock is 10pt. Prefer-light must propagate to Cinnamon, GTK, isolated preview and application fragments. All 68 coverage rows are assigned in `design.json`; unsupported rows retain their explicit reason. Builders must separately report assets, selected configuration, visual rendering, interaction and boot/login evidence. Host Carbon Typewriter, original Fastfetch artwork, shell/music commands and other preset receipts remain preserved.

## Acceptance and construction notes

Primary-button label area stays solid #245FA8, white text; glossy cap is only above the label area. Selected menus, GTK rows and terminal selection use the same pair. Hover uses darker #265098 with white text. Pinstripes never go behind document/editor content. Inactive titles use muted; disabled ink is not further opacity-reduced. Focus is a visible 2px blue ring. Traffic lights use white glyphs on their dark semantic fills (all exceed 3:1), visible on hover, and recognizable shapes as well as colour; the dark semantic red/amber/green are accessibility adaptations of the period chrome.

The bottom actor must remain transparent and the center-zone dock must be visibly narrower than the monitor in actual preview. The original blue wallpaper should have quiet central space and GNU-Darwin lettering, not a copied Tiger wave. This design does not promise pixel-identical third-party application chrome or a new operating system.

## Lint gate and hue census

The command `/usr/bin/python3 -B tools/design_lint.py gnu-darwin-aqua --write` exits **0**, with **0 errors and 0 warnings**. Hue census is clean under the installed preset census: no accent within its 20-degree warning threshold. This is a census result, not a claim that no other blue theme exists. Fonts and all non-exempt ANSI slots pass. Accent is measured but explicitly fill-only.

```text
design_lint: /home/bertmacklen/Documents/desktop-theme-studio/presets/gnu-darwin-aqua/design.json

Contrast (WCAG 2.x)
fg                    bg          fg hex   bg hex   ratio  min  status
--------------------  ----------  -------  -------  -----  ---  ------
foreground            background  #202328  #EEF0F2  13.80  4.5  ok
foreground            surface     #202328  #E2E4E7  12.37  4.5  ok
foreground            elevated    #202328  #F8F9FA  14.95  4.5  ok
muted                 background  #535A64  #EEF0F2  6.10   4.5  ok
muted                 surface     #535A64  #E2E4E7  5.47   4.5  ok
muted                 elevated    #535A64  #F8F9FA  6.61   4.5  ok
selection_foreground  selection   #FFFFFF  #245FA8  6.41   4.5  ok
terminal_foreground   terminal    #202328  #F8F9FA  14.95  4.5  ok
control_border        background  #747A83  #EEF0F2  3.79   3.0  ok
control_border        surface     #747A83  #E2E4E7  3.40   3.0  ok
control_border        elevated    #747A83  #F8F9FA  4.10   3.0  ok
focus                 background  #245FA8  #EEF0F2  5.61   3.0  ok
focus                 surface     #245FA8  #E2E4E7  5.03   3.0  ok
focus                 elevated    #245FA8  #F8F9FA  6.08   3.0  ok
disabled              background  #747A83  #EEF0F2  3.79   3.0  ok
disabled              surface     #747A83  #E2E4E7  3.40   3.0  ok
disabled              elevated    #747A83  #F8F9FA  4.10   3.0  ok
accent                background  #4384D6  #EEF0F2  3.34   4.5  info

ANSI on terminal: 14/14 slots >= 3.0:1 (slots 0 and 8 exempt)

Fonts: ui_family=Liberation Sans (ok), title_family=Liberation Sans (ok), clock_family=Liberation Sans (ok), monospace_family=Liberation Mono (ok)

Hue census: accent #4384D6 hue 214 deg
source                                                    accent   hue  dist   status
--------------------------------------------------------  -------  ---  -----  ----------------------------
presets/macintosh-soft                                    #507D7D  180  33.5   ok
presets/indigo-lunchbox                                   #6157A3  248  34.4   ok
themes/SILO/config/mint-dashboard/theme.json              #1FC9A6  168  45.8   ok
presets/moonstone-stereo                                  #8DB4A0  149  64.2   ok
presets/amethyst-arcade                                   #CF7BF5  281  67.8   ok
themes/CATPUCCINO MOCHA/config/mint-dashboard/theme.json  #A6E3A1  116  98.0   ok
presets/marzipan-crown                                    #AD2C80  321  107.5  ok
presets/peony-coronet                                     #EE60AA  329  115.3  ok
presets/quiet-sage                                        #A8B99A  93   120.6  ok
presets/plum-afterglow                                    #D5A1AA  350  136.1  ok
presets/dot-matrix-pea                                    #8C9958  72   141.5  ok
presets/pastel-leather                                    #DE999E  356  142.2  ok
presets/red-panda-overtime                                #FF6257  4    150.5  ok
presets/tangerine-graphite                                #E0915A  25   171.2  ok
presets/tidal-observatory                                 #EFC58A  35   178.4  ok
presets/carbon-typewriter                                 #D6D3D0  30   -      achromatic; hue not compared
presets/gnuos-nt                                          #717980  208  -      achromatic; hue not compared

Findings
severity  check     path              message
--------  --------  ----------------  -----------------------------------------------------
info      contrast  $.palette.accent  accent #4384D6 on background #EEF0F2 = 3.34:1 (< 4.5)

Result: 0 error(s), 0 warning(s) -> exit 0
Wrote /home/bertmacklen/Documents/desktop-theme-studio/presets/gnu-darwin-aqua/identity/contrast.json
```

Open user questions: none; selected direction is authorized. Ready for reviewer mode=design.

Machine-readable `panel_layout` declares both panels, exact applet placement, per-panel icon sizes under `icon_sizes_by_panel`, dynamic calendar allocation and journaled restoration. The integration controller must consume that per-panel map; `icon_sizes` retains the top-panel fallback only.
