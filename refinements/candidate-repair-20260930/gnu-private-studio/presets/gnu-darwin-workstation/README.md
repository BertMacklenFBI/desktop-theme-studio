# GNU-Darwin Workstation

A reviewed Cinnamon recreation of the **gray scientific GNU-Darwin workstation screenshot** explicitly selected by the user: gray square beveled controls, dark focused titles, pale inactive titles, black desktop and terminals,15 square64px launchers on the right and64px application/status tiles along the bottom. No top panel.

The original reference is preserved at [today-2002.png](../../proposals/gnu-darwin-archive/today-2002.png), recovered from [the original project screenshot in Wayback](https://web.archive.org/web/20021016083440id_/http://gnu-darwin.sourceforge.net/today.png). Its SHA-256 is `db17eb8f3514c33378f4ede46482174d90ec9d1cb7681b1b7e5ac7ad7683510e`. The older Aqua variant remains separately staged at `../gnu-darwin-aqua`.

## Result and evidence

- [Final private terminal/desktop capture](verification/isolated-20260929-055004/terminal.png), plus desktop, menu, calendar, notifications, sound, maximized and modal captures in that run.
- [Current independent review](../../reviews/2026-09-29/gnu-darwin-workstation-readiness.md): technical and visual PASS; [earlier base-theme review](../../reviews/2026-09-29/gnu-darwin-workstation-build-final.md) retained as history.
- [Final summary](verification/final-summary.json), [coverage of all68 assigned surfaces](coverage.json), [release manifest](release-manifest.json).
- [Historical source research and bonus material](../../proposals/gnu-darwin-archive/README.md), including read-only archive package findings.

The original readiness run passed 21 checks; the picker/logo update passed 23 in `isolated-20260929-131950`. The original run included including15 tiles exactly64×64,12 applets, stable open popups, actual GNOME Terminal, maximized/modal windows and full journaled restoration. All three new applet settings files were removed on restoration; the monitor timer was disposed and stopped advancing. Tests: five application fixture groups,30 runtime tests,12 menu tests,28 system tests, panel fixtures and Plymouth integrity pass. GTK2 syntax and GTK3/4 CSS validation pass.

## Resource and network monitor

The lower-right row includes four native Cinnamon CPU/MEM/RX/TX meters with square gray bezels, black displays and green/amber/cyan traces. Each tile is 64×64, for a combined 256×64 footprint. Labels and values remain separate from the graphs.

One in-process timer reads local counters once per second. CPU uses aggregate deltas; memory uses MemAvailable; RX/TX use one default-route interface and actual elapsed time. Missing data displays `--`, counter/interface changes reset the baseline, and removing the applet cancels its timer. No polling subprocesses or background service is required. This is a newly authored period-style adaptation rather than original GNU-Darwin widget code.

See [monitor build and fixture evidence](desktop/monitor-report.json), [independent monitor review](../../reviews/2026-09-29/gnu-darwin-workstation-monitor-build.md) and [fresh host preservation comparison](verification/readiness-host-after.json).

## Preserved state

Carbon Typewriter is currently selected following a successful full restore of GNU-Darwin. The earlier readiness comparison below describes the pre-activation baseline; the later live picker/logo check verified the exact `le cinabon` label and all 28 protected shell/music paths. Fastfetch original artwork, modes and Local IP are preserved by the scoped application adapters. The final readiness host and containment checks are clean: see [host comparison](verification/readiness-host-after.json) and [readiness report](verification/readiness-final.json). The earlier base-theme report retains its historical dconf timestamp finding separately.

GNU-Darwin and its picker/logo enhancement were applied, verified and kept, then subsequently restored. No root installation, logout or reboot was performed. This preset is not registered or published in the shared `theme` selector.

## Faithfulness and limits

[BRIEF.md](BRIEF.md) distinguishes source measurements from adaptations. Genuine WindowMaker artwork is reused with complete attribution in the icon tree; recovered modern package versions are not asserted byte-identical to2002 assets. Existing installed apps replace obsolete scientific/workstation programs, with [15 verified functional launchers](verification/launcher-mapping.json). Existing cursor and font families are modern substitutes. Cinnamon task tiles include running windows rather than exclusively minimized WindowMaker miniwindows. Scientific document contents are not reproduced.

The black desktop is an unobtrusive reconstruction of visible source pixels; hidden original wallpaper is unknown. The private preview used feh on a private X root, so it does not verify native Cinnamon wallpaper behavior. Native 2880×1800 dock/picker rendering was verified during activation. Exhaustive scaling behavior, GTK4 real widgets, every application's custom chrome, lock/unlock, greeter, real boot/shutdown and disk unlock remain unverified. GRUB/Plymouth images are simulated. Unsupported or preserved surfaces are recorded individually in coverage.json.

## Inspection

From the Studio root, `./presets/gnu-darwin-workstation/theme.sh plan` is read-only. Use `/usr/bin/python3 -B presets/gnu-darwin-workstation/desktop/validate.py` for read-only validation and `/usr/bin/python3 -B presets/gnu-darwin-workstation/system/system.py preview` for the system plan. The source gate requires a fresh private preview after appearance changes. All GUI tests must be serialized. Future live application and root installation are separate guarded steps.

## Ready for guarded activation

The team repaired GIMP icon visibility and GTK2 selection contrast. The final canonical run used the actual application adapter and normal GIMP startup, opened its chooser, closed gracefully and restored preferences exactly while preserving an unrelated edit. [GIMP preview](verification/isolated-20260929-055004/gimp.png), [chooser](verification/isolated-20260929-055004/gimp-interaction.png) and [ten adapter tests](applications/gimp-tests.txt) support the result. Independent technical and visual reviews PASS; [user preflight](verification/readiness-preflight.json) has zero blockers.

From the Studio root:

```sh
./presets/gnu-darwin-workstation/theme.sh plan
./presets/gnu-darwin-workstation/theme.sh apply
# Inspect the desktop, then keep it within the 180-second trial:
./presets/gnu-darwin-workstation/theme.sh keep
# Restore when needed:
./presets/gnu-darwin-workstation/theme.sh restore
```

The complete wrapper includes application and menu stages. The tested theme is saved for reuse, with Carbon Typewriter currently active. The shared selector is not published and root installation remains separate. [Archive compatibility results](../../proposals/gnu-darwin-archive/software-compatibility/RESULTS.md) distinguish tested Linux counterparts from untested archived software.

## Numbered workspace keys and approved Apple logo

When GNU-Darwin is active, click **1, 2, 3 or 4** in the bottom dock's center to switch workspaces. The active key is dark slate with white text. Keyboard shortcuts, workspace count and names remain unchanged.

The approved earlier-style striped lm Apple is included in the Fastfetch stage. Its original source remains untouched; normal short/long modes and Local IP are preserved. The existing hue helper agrees with the final color layer.

See [picker/logo review](../../reviews/2026-09-29/gnu-darwin-workstation-picker-logo.md) and [verification and current state](verification/picker-logo-final.json). `theme.sh apply`, `check`, `plan` and `restore` include the supplemental workspace and logo stages. These additions were verified live, then removed by a later full theme restore; they are saved here for the next application.
