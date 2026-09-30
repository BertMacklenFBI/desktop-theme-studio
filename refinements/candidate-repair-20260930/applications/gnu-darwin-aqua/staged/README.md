# GNU-Darwin Aqua applications

Staged light appearance fragments derived only from `../design.json`: Liberation Mono 11, opaque light terminal and readable blue selection. All 16 ANSI slots retain their exact design values. The fill-only decorative accent is not used for terminal/editor ink; focus supplies the readable blue cursor and text accent. KDE selected text roles are white throughout.

`adapter.py generate` produces 11 fragments under `generated/`: Kitty palette/include, Konsole scheme/profile, Ptyxis palette, GtkSourceView scheme, Nano appearance keys, GNOME Terminal profile, KDE palette roles, Flatpak GTK_THEME key, and Fastfetch palette registry entry. All generated files are new for Aqua. No borrowed generated trees, receipts or screenshots are evidence for this build.

`plan-preview.json` is a read-only host plan: 113 actions across 18 file paths, 21 GSettings actions and one dconf action. GNOME Terminal uses deterministic new UUID `6c77fb8c-86e7-503f-b5fe-889ce56a9e73`. Existing terminal profiles and bell behavior remain unchanged. The plan must be regenerated before coordinator application. No settings or applications were activated by this builder.

Mixed INI/JSON changes target appearance fields. Shell startup, prompt, commands, MPV/Cava, widget configuration and music behavior are untouched. Fastfetch keeps the original logo source, short/long wrappers and Local IP; only appearance colors, registry entry and theme label are planned. The six text colors use readable design roles because pale decorative artwork bands are unsuitable as ink on this light terminal.

`coverage-rows.json` provides all 32 exact applications-terminal design assignment labels and each installed Flatpak for coordinator merge. GTK toolkit assets belong to the desktop builder. Qt/KDE palette coverage does not select a Qt plugin. Tilda, browser/mail chrome, Electron/custom renderers, games, office canvases, graphics app preferences and protected media/widgets have explicit unsupported or preserved boundaries. Generated GtkSourceView availability does not prove Builder selection. No universal application theming claim is made.

## Verification

Fresh design lint: exit 0; independent mode=design PASS reviewed before work. `test_adapter.py`: 5 grouped checks pass, including fixture apply/restore, unrelated preference preservation, restore conflict rejection, interruption recovery, exact ANSI slots, selected KDE text, 23 protected hashes and unchanged live GNOME Terminal default. `runtime/test_*.py`: 30 tests pass. Evidence: `adapter-tests.jsonl`, `runtime-tests.txt`; read-only sandbox dconf warnings retained in stderr logs. Fixture mutations occur only in temporary directories with a simulated settings backend. No GUI, playback, live restoration, login or boot validation was performed by this layer.

## Attribution

Application adapter mechanics adapt the repository's reviewed GNUOS NT scoped adapter; palette, typography and identity come solely from the GNU-Darwin Aqua design. Configuration text includes no copied Apple/GNU artwork or proprietary fonts. Existing application configurations, sources and logos remain user-owned and preserved.

Ready for independent reviewer mode=build.
