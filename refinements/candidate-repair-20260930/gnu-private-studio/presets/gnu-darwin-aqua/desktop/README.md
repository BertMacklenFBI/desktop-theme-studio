# GNU-Darwin Aqua desktop build

From the Studio root, build the staged toolkit/shell/frame theme using `/usr/bin/python3 -B presets/gnu-darwin-aqua/desktop/build.py`, then icons and cursors using `build_assets.py` at the same location. These scripts write only their preset staging outputs.

`/usr/bin/python3 -B presets/gnu-darwin-aqua/desktop/validate.py` prints structural results without writing files. Add `--write` to refresh `desktop/validation.json`, or `--output PATH` to save an explicit report. CSS parsing is not visual verification; display-free runs may log toolkit icon-theme warnings. Run `test_menu_adapter.py` with unittest for temporary-HOME preservation fixtures. Only the coordinator runs serialized graphical previews and any authorized live actions.

The lock-screen clock has an opaque palette surface and dark foreground, including its smaller labels; no wallpaper-dependent text contrast or text shadow is used.
