# GNU-Darwin Workstation applications

Staged appearance based solely on the reviewed design.json: compact Liberation Mono 10, white-on-black terminals, gray application chrome, white editor canvas and slate selections. All16 ANSI slots map exactly. White terminal cursors, neutral readable Fastfetch text bands and brighter Nano syntax adapt correctly to the black terminal; GUI editor syntax uses dark semantic roles on white rather than bright terminal ANSI.

11 generated fragments: Kitty palette/include; Konsole scheme/profile; Ptyxis palette; GtkSourceView scheme; Nano keys; GNOME Terminal profile; KDE roles; Flatpak override; Fastfetch palette registry entry. Regenerated from this preset, with no old generated assets, verification or receipts copied. Terminal UUID is78279a9d-5524-58d6-bbbe-79dd9077c4d7.

The fresh read-only plan contains113 actions across18 file paths,21 GSettings actions and1 dconf action. Regenerate before coordinator application. Mixed files receive only appearance fields; no .bashrc, prompt, command, MPV/Cava, music/widget or unrelated preference mutations. Fastfetch artwork source, modes, wrappers and Local IP stay untouched. Existing terminal profiles and bell behavior remain intact.

## Coverage

coverage-rows.json has32 exact assigned surfaces and13 currently installed Flatpaks, with assets, selected state, visuals and interaction separated. Desktop builder owns GTK toolkit themes. KDE roles do not select a Qt plugin. Tilda, browser/mail chrome, Electron/custom renderers, games, office/graphics settings and protected media/widget files have explicit unsupported or preserved boundaries. GtkSourceView scheme availability does not prove Builder selection. No per-app GUI or universal styling claim.

## Evidence

Design lint exit0 (one accepted source-fidelity hue warning), coordinator-confirmed independent design PASS. adapter-tests.jsonl:5 grouped fixtures pass, covering temporary apply/restore, conflicts, interruption rollback, unrelated keys,23 protected hashes unchanged, existing terminal default unchanged, all16 ANSI slots, selected KDE text and readable terminal colors. No live mutations or GUI launches.

Initial runtime-tests.txt recorded29/30 while the controller was being built. After integration, the coordinator reran all30 tests successfully; final evidence is `../verification/app-runtime-final.txt`. The initial dependency is resolved.

Sandbox read-only dconf warnings are retained in stderr logs. Live configuration, app rendering, playback, real restoration, login and boot remain unverified by this layer. Source code uses reviewed repository adapter mechanics, retargeted to this independent preset; all palette/art configuration is new from the reviewed design. No new third-party artwork or fonts in this layer.

Ready for technical review; fresh GIMP visual acceptance remains required.

## GIMP readiness repair

Two new fragments under generated/gimp/ select System theme and Legacy icons, then include the installed desktop gtkrc from GIMP's personal gtkrc. Existing gimprc top-level appearance expressions are patched individually; nested preferences, comments and other fields survive. Duplicate/malformed or ambiguous expressions fail closed. Existing personal gtkrc is appended to, not replaced. New GIMP profiles are not created if the2.10 config directory is absent. No GTK2_RC_FILES launcher override is installed. Existing transaction actions provide restoration/conflict checks without a new action kind.

Six test_gimp_adapter.py fixtures pass, including nested/comment preservation, malformed/duplicate refusal, no-op, apply/restore retaining concurrent unrelated preference edits, conflict refusal, and personal rc include restoration. Existing five grouped adapter fixtures and30 runtime tests also pass after repair. Fresh plan/test logs replace earlier static evidence. Two added fragments bring total to13. GIMP icon visuals remain coordinator verification pending.

Icon investigation boundary: the failing private run's generated themerc already includes /usr/share/gimp/2.0/themes/System/gtkrc and /etc/gimp/2.0/gtkrc. The installed Legacy16px/22px icon tree exists. These facts do not establish that GTK2_RC_FILES bypassed GIMP defaults; the personal-gtkrc load route needs actual A/B rendering. Do not report the icon root cause or fix as visually confirmed yet.

### GIMP directory containment and fresh preview

Nine GIMP fixtures now pass. Planning rejects symlinked GIMP profile ancestors (including dangling links), symlink leaves and nonregular preference files. Every GIMP receipt action carries gimp_path_guard; current-value checks and each write repeat the lexical lstat ancestor checks. A profile swapped to a symlink after planning therefore blocks apply/check/restore. A missing ordinary profile remains a generated-only disposition. This is scoped path containment, not a claim of immunity to adversarial filesystem races during a syscall sequence.

Coordinator's normal-startup personal-gtkrc preview at software-compatibility/app-run-20260929-054524/gimp.png visibly restores Legacy icons and readable white/slate layer selection. The icon cause is not isolated because both the startup flags and rc load route changed; no claim that GTK2_RC_FILES alone caused the failure. Final actual-adapter-action render remains coordinator-owned.

GIMP can rewrite gimprc on ordinary shutdown. Apply/restore should occur while GIMP is closed so its pending preference save does not race the transaction. The adapter preserves unrelated fields, but fails closed if GIMP or the user changes/reformats a tracked literal incompatibly; it never replaces the complete existing preference file to force restoration. Real graceful-shutdown restoration is a separate coordinator check; current fixture results establish key-level behavior only.

### GIMP newline preservation

Ten GIMP fixtures pass after the CRLF repair. Flagged GIMP reads use newline-preserving UTF-8 I/O during planning, conflict checks and writes; unrelated CRLF comments/preferences are no longer normalized to LF. The new fixture exercises a multiline CRLF theme expression, a concurrent unrelated CRLF preference, and byte-exact restoration of both gimprc and personal gtkrc. Existing transaction composition decodes bytes directly and does not normalize line endings.
