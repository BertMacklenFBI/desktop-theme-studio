# Plum Afterglow applications — staged handoff

Canonical colors and font are read from the preset's design.json. Current source uses opaque ink/plum surfaces, cream body text, rose interaction, gold music highlights, Hack11 terminal text and a compact prompt. No live settings, services, GUI, or playback were changed by this worker. Quiet Sage source and receipts are untouched.

The coordinator should apply fresh receipts in this order: applications/adapter.py, color-audit/terminals/adapter.py, color-audit/private-apps/kde_roles.py, color-audit/creative/adapter.py, desktop-owned widgets, then applications/receiver/refine.py. Restore in reverse order. Every adapter supports plan/apply/check/restore; apply and restore require --state with a fresh private receipt and --commit to write.

The fresh base includes GNOME Terminal opacity, native library artwork and the installed app.devsuite.Ptyxis palette path/profile selection. Separate terminal_opacity.py, library_artwork.py and refinement/ptyxis-fix receipts are unnecessary for a fresh base. Compatibility scripts remain available; the Ptyxis fix now renders directly from design tokens, without needing a previously installed palette. The receiver already incorporates the latest usability, so do not add its historical usability.py layer.

## Application scope

- GNOME Terminal active profile: opaque background, palette, cursor/selection colors, Hack11; profile UUID/name retained.
- Kitty: last include selects generated palette, Hack11, padding12 and opacity1.
- Konsole: active profile color-scheme selection; typography inherits its existing preference.
- Ptyxis: palette files for host and installed Flatpak app-ID; actual Flatpak keyfile active profile selected; app-local GTK4 token override via terminal color adapter.
- Xed: generated GtkSource color scheme; Nano: existing appearance and supported syntax overrides; Bash: appearance-only suffix with exact original bytes protected.
- Fastfetch: canonical menu-logo bytes copied to a distinct Plum path, pin and palette registry; module colors match the existing hue updater so later use does not drift. Existing interactive wrapper and music commands untouched.
- Native music library: theme JSON values changed from current Quiet Sage to Plum, final scoped CSS, actual header/fallback PNG and theme.logo path. Real album artwork remains chosen by the existing player. Existing direct on-demand launcher points to the coordinator's native_visibility.py.
- MPV: only OSD/analyzer colors. Raw Cava output remains unchanged; display bars get rose through library CSS.
- Tilda: existing profile text/cursor/background/ANSI colors and opacity only.
- KDE: base roles plus forty supplemental semantic/header roles. Existing old-theme color roles are replaced and restored, while unrelated preferences survive.
- GIMP: System interface preference only; application must be closed when writing. Krita: new scheme resource built and contrast checked, still staged only. No canvas or document color-management edits.

## On-demand tools

Music and Dashboard are 860px-wide horizontal compositions with 184px album art, one original backend listener, genuine player metadata, retained seek/transport handlers, and a direct Library button. Transport is disabled when no player exists; no synthetic VU or startup window. Remaining tools stay compact. All twelve compatibility windows anchor bottom-center with a -66px offset (66px inward from screen edge) (42px panel + 24px clearance), matching the desktop worker's geometry contract. Requested music/dashboard heights are 310/360px; actual allocation remains coordinator visual QA.

The launcher retains start-idle, show-dashboard, hide-all and toggle-any-open-tool behavior. Existing custom backend scripts are not installed or overwritten by the receiver adapter: it changes four frontend files and only necessary existing visibility fragments. Initial theme metadata says Plum Afterglow/PA. Base→receiver→actual watcher stylesheet bytes are identical, including the exact maintained marker.

## Fresh validation

Fixture tests passed: 109 differing base actions across 27 paths; apply/restore, unrelated preference preservation, conflicts, inherited resets, attempted-write recovery, private journals, and exact original Bash bytes. Separate terminal adapter tests cover existing/missing app-local CSS and reversible Tilda changes; KDE tests cover 40 roles; GIMP tests cover byte-preserving parsing/rollback; Ptyxis tests cover profile/palette identity and retained runtime geometry. Launcher fixture confirms no startup popup and coherent visibility. Receiver usability tests retain all four media handlers and three idle transport guards.

Actual cached Eww Grass and Yuck libraries parsed the staged sources; GTK3 parsed the nonempty compiled CSS with no charset directive. Installed Gio schema ranges, GtkSource XML, prompt syntax and generated font/padding values were checked. Eight content/selection/accent pairs are at least 6.46:1; main text on background is 13.41:1. validation.json records only this new source audit and current canonical logo hash. No prior theme screenshots or evidence were copied as verification.

Remaining: coordinator isolated/live rendering and interactions, application reload as appropriate, GTK4/Ptyxis actual rendering, browser/Electron content-specific colors, Blender UI, LibreOffice canvas, and Krita selection. Firefox native chrome inherits GTK; no browser profile edits. Existing shell sessions require a fresh shell or normal sourcing to see the new prompt. Login/reboot and native library lifecycle remain coordinator-owned.
