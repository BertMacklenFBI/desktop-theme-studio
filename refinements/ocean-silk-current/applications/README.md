# Ocean-Silk-Glass applications

This is a new scoped appearance transaction against the current live Evergreen state. It does not reuse an earlier receipt. Nothing is live-applied by building or staging it.

Supported coverage: GNOME Terminal, Kitty, Konsole active profile when present, host/Flatpak Ptyxis, Tilda, Xed/GtkSourceView, Nano chrome, KDE roles, Krita scheme, GIMP System theme, LibreOffice light Colibre SVG toolbar icons, native Music Library, Cava colors, gTile, and Fastfetch short/long identity. GTK/Cinnamon core selection and desktop artwork remain coordinator-owned.

The original Ocean-Silk-Glass terminal ANSI palette is retained. Fastfetch consumes `fastfetch_palette` if supplied, otherwise the six legacy `rainbow` fields. Branding PNG comes from `../artwork/menu-logo.png`. Its new filename avoids terminal image caching. Existing Fastfetch wrappers, mode generator, long module list and custom Local IP are protected. Bash behavior and custom music commands are unchanged. The existing prompt receives two exact color replacements only.

Native Music Library gets one replacement stylesheet link and the exact opaque WebKit background corresponding to its new surface. Existing show/hide/reopen, backend, music controls and search remain intact. Four mixed files expose `protected_edits` before/after hashes plus nonappearance invariants.

Commands, run from this directory using `/usr/bin/python3`:

```sh
/usr/bin/python3 adapter.py stage
/usr/bin/python3 adapter.py plan
/usr/bin/python3 adapter.py preflight
/usr/bin/python3 adapter.py apply --state /absolute/path/to/NEW-applications-receipt.json
/usr/bin/python3 adapter.py apply --state /absolute/path/to/NEW-applications-receipt.json --commit
/usr/bin/python3 adapter.py check --state /absolute/path/to/NEW-applications-receipt.json
/usr/bin/python3 adapter.py restore --state /absolute/path/to/NEW-applications-receipt.json --commit
```

`stage` writes only this source directory's plan/inventory/staged artifacts. `plan`, apply preview and preflight are read-only. A committed apply generates a fresh plan and refuses an existing receipt path. Apply and restore preflight every affected external preference-writing app before the first mutation, and check again at each relevant write. The coordinator must close such apps normally; this adapter never kills, restarts or launches them. Failure rollback refuses changed values rather than overwriting unrelated changes.

Tests:

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s . -p test_adapter.py -v
```

The tests use temporary copies of current configs and a fixture copy of Evergreen artwork; they do not prove final artwork, selected live appearance, session GSettings access or GUI behavior. Four tests cover full file apply/restore, mixed-file safeguards, Fastfetch runtime idempotence/long identity/custom IP, external-app blockers and token-based rendering. Existing running terminal and native Music Library windows require coordinator-controlled refresh for visual verification.
