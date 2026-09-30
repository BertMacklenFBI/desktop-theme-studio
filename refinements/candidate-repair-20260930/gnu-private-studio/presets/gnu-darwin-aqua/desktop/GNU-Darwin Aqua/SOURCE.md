# GNU-Darwin Aqua — desktop theme sources

* Base: packaged Mint-Y-Dark snapshot (mint-themes, GPL-3+; see COPYRIGHT) kept pristine in `../base/`.
* Recolouring: every Mint-Y hex/rgba role-mapped to `design.json` palette tokens by `../build.py`, adapted by the Desktop Theme Studio agent team on 2026-09-29.
* GTK3/GTK4: all referenced PNG control assets replaced with flat state SVGs; GTK2 uses native Murrine rules with no pixmaps.
* Window controls: three coloured discs (minimize = warning, maximize = success, close = error, white glyph ink) in GTK3, GTK4 and Metacity.
* Cinnamon shell, lock-screen (.csstage), window-frame and material (brushed silver) styling authored for this preset.
* Thumbnails: regenerated from palette (PIL).
* Icons (`GNU-Darwin Aqua icons`) and cursors (`GNU-Darwin Aqua cursors`) are built by a separate worker.
