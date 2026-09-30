# GNU-Darwin Aqua — new-chat handoff

User asked to create this theme in a new chat. GNU-Darwin work in the original chat has stopped; its active designer was interrupted before it wrote design files. The new chat owns all further GNU-Darwin work.

## Authorized scope and direction

Create a complete reviewed staged Cinnamon theme named **GNU-Darwin Aqua**, ID **gnu-darwin-aqua**, using **GPT-6 Astra with subagents**, per the latest explicit user instruction. This supersedes the earlier Luna model assignment for the new GNU-Darwin chat. User explicitly selected **Mac OS X Panther/Tiger Aqua with GNU-Darwin branding**. Their reference is GNU-Darwin's GNU environment for PowerPC/x86 Darwin 7/8 and Mac OS X 10.3/10.4. This is an appearance theme for Linux Mint Cinnamon, not an OS installation. Prepare a concrete reviewed build; do not assume authorization for root installation or logout/reboot.

Use pale subtle pinstripes, restrained brushed-silver headers, blue glossy controls, left-side traffic-light buttons, full-width light top panel and a centered lower dock. Draw original blue flowing/gradient wallpaper with GNU-Darwin text branding; do not fabricate official logos. Preserve originals if the user supplies art. Preserve the actual current menu label (currently `le cinabon`), shell/music commands, Fastfetch original logo/modes and Local IP, all other preset state/receipts and unrelated app preferences.

## Real paths and starting state

Actual repository: `/home/bertmacklen/Documents/desktop-theme-studio`. The saved Codex project path `/home/bertmacklen/Documents/ChatGPT/MintCinnamonThemeStudio` is a different mostly empty checkout. Run all Studio work with explicit actual repository paths.

Read `AGENTS.md`, `docs/design-spec.md`, `CLAUDE.md`, `tools/README.md`. Already present for this new theme: README.md, verification/before.json (current appearance and this-session installed application inventory), verification/preflight-before.txt, and this handoff. There is NO design.json, BRIEF, scaffold, or built Aqua theme yet. Proposed designer measurements, not finalized: top34px, dock68px, top icons20px and dock icons44px; Liberation fonts and explicit prefer-light.

Containment marker: `state/markers/gnu-darwin-aqua-20260929T075637Z` (28 protected paths). Host currently selects **Carbon Typewriter**. A separate kept Carbon Typewriter switch occurred during the prior NT design; do not undo it. Relevant receipt: `refinements/cinnamon-current-collection/state/20260929-022837-bf93c5a17f55/receipt.json`. Prior change explanation in NT verification/concurrent-change.json. No GNUOS NT or Aqua live activation was performed.

## Team and gates

Designer -> design lint exit0 -> independent design review PASS -> scaffold preview/write -> containment mark -> bounded desktop/artwork, applications/terminal, boot/login subagents using GPT-6 Astra by inheritance. Boot requires wallpaper. Maximum three workers plus coordinator. Coordinator owns integration and all potential live actions. Independent technical and design/usability reviews, then one serialized private desktop preview and restoration rehearsal. Save coverage distinguishing built, configured, isolated visual, functional and real boot/login evidence. Preserve source rights and all68 coverage assignments. No shared live edits, sudo, Cinnamon replacement, broad process kills or parallel GUI tests.

## Reusable reviewed implementation and pitfalls

The prior **GNUOS NT** staged theme is complete at `presets/gnuos-nt`, with final reviews `reviews/2026-09-29/gnuos-nt-build-final.md` and actual private session `verification/isolated-20260929-025239/`. Its one42px bottom panel loaded10/10 applets and restoration passed. Do not edit NT or copy its verification/state/receipts as Aqua evidence.

Reuse its scoped applications adapter/runtime as code guidance: it removes inherited .bashrc changes, preserves Fastfetch source/wrappers and protected mixed music configs, and creates a deterministic per-slug GNOME Terminal UUID. Retarget ALL identities; regenerate for Aqua. Apps4 grouped fixtures +30 runtime tests, desktop menu12 and boot28 fixture tests passed for NT.

For Aqua top-level integration, the NT per-preset FULL COPY desktop_control.py has tested journaled panel relocation, removed-applet config restoration, dynamic calendar instance allocation, and strict optional `:orient` parsing. Do not edit shared lib/lineage or collection. Current IDs: menu17; grouped list16; tray/status/keyboard/notifications/network/sound/power3/4/8/20/19/11/21; remove old workspace36, island42:orient, c-eyes41/44; new calendar uses current next-applet-id (last observed47). Read current state before final planning. Put menu on panel1:left, status+clock panel1:right, grouped list panel2:center; positions panel1top/panel2bottom. The controller supports multiple panels, but per-panel icon sizes need a narrow extension if top and dock sizes differ. Extend source gate to hash canonical appearance inputs (typography/cursor/light preference) in addition to panel_layout/controller/lineage/isolate. The new preview must assert two exact panels and rehearse restoration.

Retarget the inherited isolate.py and controller fonts, cursor size, button order, color scheme and GTK light preference from design; inherited Tangerine code hardcodes Ubuntu/Hack/dark mode. Never assume parser success proves visual success. NT review found inherited dark-on-navy selection, wrong GTK titlebar labels, Metacity arc draw ops and low-contrast boot progress. Check effective CSS cascade and real render. NT's offscreen GTK helper is useful but derive all values from Aqua design, and actual CSD/Metacity must be checked in private Cinnamon.

Xvfb is not installed; Xephyr is available. Root coordinator used one bounded private HOME/XDG/D-Bus/Xephyr session via approved escalation, without changing host selection. Check machine load before GUI; it was initially busy/swapped but later settled. Offscreen Gtk.OffscreenWindow renders need display access; they do not map windows. Keep GUI work serialized. The root Plymouth config `/etc/plymouth/plymouthd.conf` is absent; a Tangerine-named backup alone is NOT an active override. Never restore unrelated receipts blindly.

## References

User direction record: `proposals/gnu-darwin-next.md`. Apple primary period reference: https://www.apple.com/newsroom/2005/04/12Apple-to-Ship-Mac-OS-X-Tiger-on-April-29/ . GNU-Darwin historical web availability may be limited; avoid spending excessive time on history when original assets and clear attribution suffice.
