# GNU-Darwin Workstation staged desktop

Build from the Studio root with `/usr/bin/python3 -B presets/gnu-darwin-workstation/desktop/build.py` and `build_assets.py` at the same path. These write only the new preset staging area. Layout uses64px right launcher tiles and bottom task/status tiles,48px application art, square gray controls and dark compact titles with minimize left/close right.

`validate.py` prints structural GTK2 rc and GTK3/4 CSS checks without writes by default. `--write` explicitly saves validation.json; `--output PATH` saves an explicit report. Parser success is not visual proof. Menu safety fixtures run via `/usr/bin/python3 -B -m unittest discover -s presets/gnu-darwin-workstation/desktop -p 'test_*.py'`. `preview_static.py` generates an explicitly labeled illustrative composition only. Only the coordinator may run GUI or apply live changes.

Recovered modern Window Maker icons retain source shapes/colors and complete copyright. `assets-report.json` documents each functional alias: GNUstepGlow→settings/menu, WPrefs→appearance settings, Mozilla→Firefox, StarOffice→LibreOffice Start Center, write→Writer/text editor, wilber→GIMP, pdf→Xreader, xv→Xviewer, Drawer→Nemo. Unknown historical applications retain installed modern identities, including Blender/LyX/Warpinator/Kubrick. No legacy executables are installed and no exact2002 asset identity is claimed. All other icons fall back to installed Adwaita/hicolor; cursors inherit installed Bibata Modern Classic as a disclosed modern substitute.

Full Cinnamon rendering, right/bottom corner geometry, actual CSD/frame states, interaction, lock/login and boot remain separately verified by the coordinator. No universal application pixel identity is claimed.
