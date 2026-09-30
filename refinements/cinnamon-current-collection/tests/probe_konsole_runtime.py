#!/usr/bin/python3
"""Coordinator-only GUI probe in an isolated Konsole configuration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from Xlib import X, display
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    run = ROOT / 'verification/konsole-runtime' / time.strftime('%Y%m%d-%H%M%S')
    run.mkdir(parents=True)
    fixture = run / 'test-home'
    config = fixture / '.config'
    data = fixture / '.local/share'
    schemes = data / 'konsole'
    config.mkdir(parents=True)
    schemes.mkdir(parents=True)
    before = 'CinnamonCurrentGraphiteBrass'
    after = 'CinnamonCurrentLavenderGlass'
    for slug, name in [('graphite-brass', before), ('lavender-glass', after)]:
        shutil.copy2(ROOT / 'generated' / slug / 'applications/konsole' / (name + '.colorscheme'), schemes)
    profile = schemes / 'ThemeProof.profile'
    profile.write_text('[General]\nName=ThemeProof\n[Appearance]\nColorScheme=' + before + '\n')
    original = profile.read_bytes()
    (config / 'konsolerc').write_text('[Desktop Entry]\nDefaultProfile=ThemeProof.profile\n')
    title = 'Studio Konsole color proof ' + run.name
    script = run / 'child.py'
    script.write_text(f'''import json, sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from konsole_runtime import refresh
run=Path({str(run)!r})
profile=Path({str(profile)!r})
original=profile.read_text()
print("\\033]0;{title}\\a",end="",flush=True)
print("Theme color verification: current tab only.\\nThe shell and its commands remain intact.\\n",flush=True)
def gate(stage):
    (run/(stage+'.ready')).write_text('ready')
    deadline=time.monotonic()+20
    while not (run/(stage+'.continue')).exists():
        if time.monotonic()>deadline: raise RuntimeError('Probe handshake expired')
        time.sleep(.1)
gate('before')
try:
    profile.write_text(original.replace({before!r},{after!r}))
    result=refresh(home={str(fixture)!r})
    (run/'apply.json').write_text(json.dumps(result))
    gate('after')
finally:
    profile.write_text(original)
    result=refresh(home={str(fixture)!r})
    (run/'restore.json').write_text(json.dumps(result))
gate('restored')
''')
    x = display.Display()
    screen = x.screen().root
    previous = screen.get_full_property(x.intern_atom('_NET_ACTIVE_WINDOW'), X.AnyPropertyType)
    process = None
    shots = {}
    def window():
        clients = screen.get_full_property(x.intern_atom('_NET_CLIENT_LIST'), X.AnyPropertyType)
        for ident in clients.value if clients else []:
            w = x.create_resource_object('window', int(ident))
            name = w.get_full_property(x.intern_atom('_NET_WM_NAME'), x.intern_atom('UTF8_STRING'))
            pid = w.get_full_property(x.intern_atom('_NET_WM_PID'), X.AnyPropertyType)
            if (pid and process and int(pid.value[0]) == process.pid) or (name and title in name.value.decode(errors='replace')):
                return int(ident)
        return None
    def capture(stage):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ident = window()
            if ident and (run / (stage + '.ready')).exists():
                break
            if process.poll() is not None:
                raise RuntimeError('Owned test terminal exited early')
            time.sleep(.1)
        else:
            raise RuntimeError('Owned test window did not become ready')
        subprocess.run(['wmctrl', '-ia', hex(ident)], check=True)
        time.sleep(.6)
        path = run / (stage + '.png')
        subprocess.run(['scrot', '-u', '--overwrite', str(path)], check=True)
        with Image.open(path) as image:
            pixels = image.convert('RGB')
            box = pixels.crop((pixels.width // 3, pixels.height // 2, pixels.width * 2 // 3, pixels.height * 3 // 4))
            counts = box.getcolors(box.width * box.height)
            shots[stage] = {'path': str(path), 'background': max(counts)[1]}
        (run / (stage + '.continue')).write_text('continue')
    try:
        with (run / 'konsole.log').open('w') as log:
            process = subprocess.Popen(['konsole', '--separate', '--profile', 'ThemeProof', '-e', '/usr/bin/python3', str(script)],
                env={**os.environ, 'XDG_CONFIG_HOME': str(config), 'XDG_DATA_HOME': str(data)}, stdout=log, stderr=log)
            for stage in ('before', 'after', 'restored'):
                capture(stage)
            process.wait(timeout=8)
        reports = {name: json.loads((run / (name + '.json')).read_text()) for name in ('apply', 'restore')}
        passed = process.returncode == 0 and all(r['status'] == 'updated' for r in reports.values())
        passed = passed and profile.read_bytes() == original and shots['before']['background'] != shots['after']['background']
        passed = passed and shots['before']['background'] == shots['restored']['background']
        result = {'status': 'passed' if passed else 'failed', 'run': str(run), 'runtime': reports, 'screenshots': shots,
                  'scope': 'Owned Konsole window and isolated XDG files only; existing terminals and user profiles untouched.'}
        (run / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
        return 0 if passed else 1
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=8)
        if previous and previous.value[0]:
            subprocess.run(['wmctrl', '-ia', hex(int(previous.value[0]))], check=False)
        x.close()


if __name__ == '__main__':
    raise SystemExit(main())
