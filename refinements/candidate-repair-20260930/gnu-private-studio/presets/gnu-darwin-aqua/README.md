# GNU-Darwin Aqua

An original staged Cinnamon appearance preset inspired by Panther/Tiger Aqua: pale pinstripes, brushed-silver headers, glossy blue controls, left traffic-light buttons, a continuous 34px top bar and a compact centered 68px dock with 44px icons. The blue flowing wallpaper and GD monograms are original, not official GNU-Darwin or Apple artwork.

This is a Linux Mint appearance theme. It does not install Darwin. The active Carbon Typewriter theme and exact `le cinabon` menu label are preserved. Shell/music commands, Fastfetch source artwork and wrappers, mixed MPV/Cava settings and other application preferences are protected. No live activation, root installation, logout or reboot was performed.

## Build and evidence

- `design.json`, `BRIEF.md`, `identity/`: independently reviewed design, sampled references, contrast and installed font evidence.
- `desktop/`: GTK2/3/4, Cinnamon, Metacity, 394 icons, Bibata cursor tree, reversible menu adapter and validation reports.
- `artwork/`: original 2880×1800 wallpaper and GD marks, generator and hashes.
- `references/`: bonus Internet Archive index, small historical notes and Darwin article; reference-only, no OS images downloaded.
- `applications/`: 11 appearance fragments, narrow adapters, 5 fixture groups and 30 runtime tests; coverage of 32 assigned surfaces and 13 installed Flatpaks records limitations individually.
- `system/`: GRUB, Plymouth and Slick Greeter assets, 28 fixture tests and a read-only installation plan. GRUB/Plymouth images are simulated previews, not real engine captures.
- `coverage.json`: all 68 assigned appearance surfaces, with built assets, private evidence and live/boot verification distinguished.
- `verification/`: private Cinnamon captures and restoration reports, protected-path checks, host settings comparison and read-only plan.
- `../../reviews/2026-09-29/gnu-darwin-aqua-*.md`: independent design, technical and usability review records.

The fresh acceptance run `verification/isolated-20260929-222442` passed all 16 checks: top/dock geometry, 10/10 applet loading, stable visible menu/popups, maximized/modal windows and exact restoration. Its current-source gate passes. Independent screenshot inspection confirms the shutdown correction: the dark power glyph is legible on its light outlined button. See `../../reviews/2026-09-29/gnu-darwin-aqua-acceptance-final.md` and `verification/acceptance-session-20260929/after.json` for source freeze and preservation evidence. On 2026-09-29 the user changed the target to the actual archived GNU-Darwin appearance. This Aqua version is preserved separately; it is not the finished response to that revised request. Historical source research is in `../../proposals/gnu-darwin-archive/`. Host settings remain unchanged. GNUOS NT is concurrently being continued in its original chat; its later source/evidence changes are documented in `verification/concurrent-work.json` and are outside this preset's writes.

## Safe inspection

From the Studio root, `presets/gnu-darwin-aqua/theme.sh plan` is read-only. `python3 -B presets/gnu-darwin-aqua/desktop/validate.py` validates without writing; add `--write` to refresh its report. `python3 -B presets/gnu-darwin-aqua/system/system.py preview` builds a read-only system plan. Use `/usr/bin/python3` for GI.

`isolate.py` requires display access and runs a bounded private HOME/XDG/D-Bus/Xephyr session; serialize it with all other desktop previews. Its exact-source report gates any future live trial. Root installation requires a separate explicit commit and authorization. The preset is not registered in the shared theme selector.

## Remaining limits

Native 2880×1800 desktop activation and scaling, every installed application's custom chrome, GTK2 runtime, real GTK4 widgets, lock/unlock, greeter, boot/shutdown and disk-unlock behavior remain unverified. Libadwaita/custom-rendered apps, firmware and virtual-console appearance retain documented unsupported or preserved boundaries. The generic whole-host baseline check reports pre-existing drift and an unreadable GRUB configuration skip; this build is not a whole-host baseline repair.
