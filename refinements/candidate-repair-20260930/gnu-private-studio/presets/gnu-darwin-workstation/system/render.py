#!/usr/bin/python3
"""Render review copies of this package's templated assets; never install or change settings.

Writes only system/rendered/{grub/theme.txt, plymouth/<slug>.script, plymouth/<slug>.plymouth},
system/slick-greeter.preview.conf and system/rendered-manifest.json. The GRUB and Plymouth
asset directories belong to the asset agents and are never written here. Missing inputs are
reported (exit 1) instead of raising.
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('workstation_system', HERE / 'system.py')
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)
OUT = HERE / 'rendered'


def main():
    design = system.design_spec()
    wallpaper = system.PRESET / 'artwork/wallpaper.png'
    missing = system.missing_templates()
    if not wallpaper.is_file(): missing.append(str(wallpaper))
    if not (HERE / 'plymouth/plymouthd.conf').is_file(): missing.append(str(HERE / 'plymouth/plymouthd.conf'))
    for part in (system.NAME, system.NAME + ' icons', system.NAME + ' cursors'):
        if not (system.PRESET / 'desktop' / part / 'index.theme').is_file():
            missing.append(str(system.PRESET / 'desktop' / part / 'index.theme'))
    rendered = {}
    if not system.missing_templates():
        try: rendered = system.render_assets(design)
        except ValueError as exc: missing.append('render error: ' + str(exc))
    for relative, payload in rendered.items():
        target = OUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    # Representative appearance-only greeter rendering, not an installed-state snapshot.
    (HERE / 'slick-greeter.preview.conf').write_bytes(system.greeter_patch('[Greeter]\n', design))
    statics = system.static_assets()
    report = {'name': system.NAME, 'id': system.SLUG,
              'status': 'staged-not-installed' if not missing else 'incomplete-inputs',
              'design_sha256': system.sha((system.PRESET / 'design.json').read_bytes()),
              'rendered': {relative: system.sha(payload) for relative, payload in sorted(rendered.items())},
              'rendered_dir': str(OUT),
              'static_assets': {relative: system.sha(path.read_bytes()) for relative, path in sorted(statics.items())},
              'plymouthd_conf_source': str(HERE / 'plymouth/plymouthd.conf'),
              'plymouthd_conf_present': (HERE / 'plymouth/plymouthd.conf').is_file(),
              'wallpaper_source': str(wallpaper),
              'wallpaper_present': wallpaper.is_file(),
              'wallpaper_sha256': system.sha(wallpaper.read_bytes()) if wallpaper.is_file() else None,
              'grub_background': 'bespoke grub/background.png' if 'grub/background.png' in statics else 'wallpaper.png fallback',
              'grub_font': 'bundled grub/font.pf2' if 'grub/font.pf2' in statics else 'installed unicode.pf2 fallback',
              'missing_inputs': missing,
              'visual_verification': 'No live boot, greeter, lock or special prompt tested'}
    (HERE / 'rendered-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'rendered': len(rendered), 'static_assets': len(statics),
                      'wallpaper_present': report['wallpaper_present'], 'missing_inputs': missing}, indent=2))
    return 1 if missing else 0


if __name__ == '__main__': sys.exit(main())
