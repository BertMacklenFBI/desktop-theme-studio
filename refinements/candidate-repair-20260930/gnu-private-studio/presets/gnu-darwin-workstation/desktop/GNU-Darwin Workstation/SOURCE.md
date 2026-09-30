# GNU-Darwin Workstation — desktop theme sources

* Base: packaged Mint-Y-Dark snapshot (mint-themes, GPL-3+; see COPYRIGHT) kept pristine in `../base/`.
* Recolouring: every Mint-Y hex/rgba role-mapped to `design.json` palette tokens by `../build.py`, run for GNU-Darwin Workstation on 2026-09-29.
* GTK3/GTK4: all referenced PNG control assets replaced with flat state SVGs; GTK2 uses native Murrine rules with no pixmaps.
* Window controls: square minimize left and close right; white active glyphs and dark inactive glyphs.
* Cinnamon shell, lock-screen (.csstage), window-frame and classic bevel styling authored for this preset.
* Thumbnails: regenerated from palette (PIL).
* Icons (`GNU-Darwin Workstation icons`) use recovered modern Window Maker assets with full attribution and explicit functional aliases; cursors (`GNU-Darwin Workstation cursors`) inherit installed Bibata without copies.
