# Workspace runtime attribution and provenance

The adjacent `workspace-switcher@cinnamon.org/` tree is an exact three-file copy of the pre-existing user-local Cinnamon workspace switcher on this workstation. It is staged for a private acceptance fixture; copying it did not install an applet or change a live session. The copy is bound by `../workspace-runtime-source.json` and retains the native UUID, display name and settings schema.

Upstream source is Linux Mint Cinnamon: https://github.com/linuxmint/cinnamon . Installed package provenance is retained byte-exact in `Cinnamon-copyright`. Its default `Files: *` declaration identifies this JavaScript applet under GPL version 2 or later. The complete version 2 license text is retained byte-exact in `GPL-2.txt`. This folder does not claim authorship of the upstream applet. The complete installed copyright file includes notices for other Cinnamon files; this staged applet does not include those other source files.

The user-local JavaScript differs from the installed system applet by two narrowly scoped additions:

1. Connect `panel.actor` `style-changed` to `queueCreateButtons()` so a panel theme change refreshes native button allocation.
2. Override `_panelHeight` to subtract ancestor panel/zone padding and borders along the cross axis (vertical in horizontal panels, horizontal in vertical panels), stopping at the panel actor and clamping to at least one pixel. The switcher's own padding and borders are not subtracted.

The attribution of those pre-existing local additions is not known from the file. Their exact bytes are preserved without adding an invented author. Native click, workspace ordering, number labels, names/tooltips, settings schema, scroll behavior and removal/Expo controls are otherwise byte-identical to the installed upstream applet. `metadata.json` and `settings-schema.json` are byte-identical to the system originals.

The original tree remains the exact-host fixture for the fresh Jesta, Cryostat and GNU-Darwin Workstation standalone acceptance. Stock Cinnamon's base `_panelHeight` uses the raw panel height; its different behavior is not accepted by those host-matching receipts.

The adjacent `workspace-switcher-rounded/` tree is a separate staged derivative for the 21 changed collection controls. It retains the original native UUID, metadata and settings schema. The coordinator's added layout code centers the strip at its preferred cross-axis allocation and disables parent `St.BoxLayout` cross-axis child fill once a parent exists, repeating that packing when buttons rebuild after orientation changes. This prevents a floating panel zone's larger allocation from stretching the strip and numbered controls beyond the usable panel inset. If another applet’s preferred height makes a horizontal panel zone taller than the actual panel, the derivative uses start alignment within that zone so numbered controls remain inside the panel. Normal panel zones keep centered packing. The original exact-host tree is preserved. The derived tree is **not** claimed to match the installed host bytes. Its current source binding and narrow change record belong to `../rounded-runtime-change.json`, and native acceptance is recorded separately from this attribution.

The same retained upstream notices and GPL version 2 or later declaration apply to both copies. The new coordinator layout change is attributed to the Desktop Theme Studio workspace refinement of 2026-09-30; it does not replace or claim authorship of upstream or previously existing local work. An eventual GitHub source package must include the applicable reviewed runtime and these notices/provenance, document the dependency, and retain a separate guarded install/restore workflow. Private fixture acceptance does not install either tree for another workstation, prove that the revised tree is installed on this workstation, or prove portability to a different Cinnamon release.

SHA-256:

- User-local/staged applet.js: `3c0cef31583358eaccb177c0f5bb731815b611ee9d9f221e8f79142c700d49a7`
- Installed system applet.js: `7d9842b9a095be2d63ee95955d95a7984383f791699ed721c57eaa0ef85f92c6`
- metadata.json: `64796932806f52efc072403a78442aed820693eec4855d3de563d6bac85d77ab`
- settings-schema.json: `f2f274a45b9e623ad881149dd55efec89a4b0cef2b4d1539dddedbe56f190285`
