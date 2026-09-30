# GNU-Darwin Aqua artwork

`generate.py` creates original deterministic blue flowing fields and GNU-Darwin lettering from the reviewed `../design.json` palette. The GD letter monogram is an original design, not an official GNU-Darwin or Apple logo. Original generated artwork is offered under CC0-1.0; Liberation Sans is used for lettering. Reference screenshots in `../identity/` retain their owners’ rights and are not included in installed artwork.

| File | Content and intended consumer |
| --- | --- |
| wallpaper.png | 2880×1800 RGB original sweeping blue bands; desktop and staged system backgrounds |
| preview.png | 1440×900 downscaled wallpaper preview |
| menu-logo.png | 256×256 RGBA original GD monogram; optional Cinnamon menu icon |
| plymouth-logo.png | 720×720 RGBA GD monogram; staged Plymouth artwork |
| grub-logo.png | 240×240 RGBA GD monogram; staged GRUB artwork |
| manifest.json | Output SHA-256 hashes, dimensions, source attribution and design hash |

Run `/usr/bin/python3 -B presets/gnu-darwin-aqua/artwork/generate.py` from the Studio root to rebuild these staged assets. No live settings are changed. The generator does not read or modify supplied artwork, original Fastfetch logos, palettes, wrappers, shell or music commands. There is no Fastfetch logo replacement or palette installation in this artwork lane. The menu adapter preserves the exact existing label `le cinabon`.
