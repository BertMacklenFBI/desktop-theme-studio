"""Bounded authored Eww rendering in an already isolated display and bus."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tempfile
import time


def exercise(profile, generated, run, private_x, *, screen_size):
    from PIL import Image, ImageStat
    from Xlib.error import BadWindow
    contract = profile['authored_eww_bundle']
    bundle = generated / 'widgets/authored'
    launcher = bundle / contract['private_launcher']
    player_launcher = bundle / 'private/player/launch.py'
    private_root = tempfile.TemporaryDirectory(prefix='dts-candidate-receiver-')
    environment = {**os.environ, 'DTS_PRIVATE_ROOT': private_root.name,
                   'DTS_EWW_BINARY': str(Path('/home/bertmacklen/.local/bin/eww').resolve())}
    if environment['DISPLAY'] == environment['CURRENT_COLLECTION_HOST_DISPLAY'] or environment['DBUS_SESSION_BUS_ADDRESS'] == environment['CURRENT_COLLECTION_HOST_BUS']:
        raise RuntimeError('Authored receiver needs the owned private display and bus')
    def command(*args, player=False):
        argv = [sys.executable, '-B', str(player_launcher if player else launcher), *args]
        if args[0] == 'status':
            result = subprocess.run(argv,env=environment,capture_output=True,text=True,timeout=15)
        else:
            # The daemon/listeners inherit descriptors. A capture pipe would
            # wait for their EOF after the short-lived launcher has exited.
            with (run / 'receiver-cli.log').open('a') as log:
                result = subprocess.run(argv,env=environment,stdout=log,stderr=log,text=True,timeout=15)
        result.check_returncode()
        return (result.stdout or '').strip()
    def windows(opened_ids=()):
        found = []
        children = {child.id: child for child in private_x.screen().root.query_tree().children}
        for identity in opened_ids:
            children.setdefault(identity, private_x.create_resource_object('window', identity))
        for child in children.values():
            try:
                attributes = child.get_attributes()
                identity = child.get_wm_class() or ()
                if attributes.map_state != 2 or (child.id not in opened_ids
                        and not any('eww' in value.casefold() for value in identity)):
                    continue
                geometry = child.get_geometry()
                position = private_x.screen().root.translate_coords(child, 0, 0)
                found.append({'id': child.id, 'class': list(identity), 'title': child.get_wm_name(), 'mapped':True,
                              'x': position.x, 'y': position.y, 'width': geometry.width, 'height': geometry.height})
            except BadWindow:
                continue
            except Exception:
                if opened_ids:
                    raise
                continue
        return found
    result = {'scope': 'Real authored Eww surfaces on private X11 and D-Bus; system/media actions use owned mock fixture.',
              'host_controls': False, 'music_private_player_discovery_transport': 'unverified; mock transport is not player acceptance',
              'windows': {}}
    command('prepare')
    player_started = False
    try:
        if player_launcher.is_file():
            import json
            owned = Path(private_root.name)
            receipt = {'schema': 1, 'kind': 'owned-private-player',
                'private_root': str(owned), 'player_home': str(owned / 'player/home'),
                'display': environment['DISPLAY'], 'uid': os.getuid(),
                'mpv_executable': '/usr/bin/mpv', 'plugin_path': str(owned / 'player/mpris.so'),
                'plugin_sha256': '4a9622b06dbc784e91a1c5f911c4e503980732ea2d633683b8bca4eb430e9fab',
                'socket': str(owned / 'player/mpv.sock'), 'pid': None, 'birth': None}
            receipt['environment'] = {'HOME': receipt['player_home'],
                'DISPLAY': environment['DISPLAY'],
                'DBUS_SESSION_BUS_ADDRESS': environment['DBUS_SESSION_BUS_ADDRESS']}
            for key, part in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                              ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'),
                              ('XDG_RUNTIME_DIR', 'runtime')]:
                receipt['environment'][key] = str(owned / 'player/home' / part)
            (run / 'owned-private-player-root.json').write_text(json.dumps(receipt, indent=2) + '\n')
            command('start', player=True)
            player_started = True
            binding = json.loads((owned / 'player/binding.json').read_text())
            receipt.update(pid=binding['pid'], birth=binding['identity']['birth'])
            (run / 'owned-private-player-root.json').write_text(json.dumps(receipt, indent=2) + '\n')
            result['scope'] = 'Real authored Eww surfaces and owned mpv/MPRIS endpoint on private X11 and D-Bus; other system controls use owned mock fixtures.'
            # Preserve endpoint proof even if a subsequent UI paint check fails.
            command('verify', player=True)
        command('start')
        for name in contract['windows']:
            command('open', name)
            deadline = time.monotonic() + 6
            while True:
                active = command('status')
                mapped = windows()
                matched = [row for row in mapped if any(name in value.casefold() for value in [row['title'] or '', *row['class']])]
                if name in active and matched:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Authored receiver window did not map: ' + name + '; ' + repr(mapped))
                time.sleep(.15)
            for row in matched:
                if min(row['x'], row['y']) < 0 or row['x'] + row['width'] > screen_size[0] or row['y'] + row['height'] > screen_size[1]:
                    raise RuntimeError('Authored receiver window is outside actual display: ' + repr(row))
            destination = run / ('eww-' + name + '.png')
            row = matched[0]
            # A content ink check supplements variation; wallpaper variation
            # alone is insufficient to accept a transparent Eww window.
            ink = profile.get('authored_receiver_ink', {}).get(name, profile['palette']['foreground'])
            foreground = [int(ink[index:index+2],16) for index in (1,3,5)]
            paint_deadline = time.monotonic() + 10
            paint_attempts = []
            while True:
                time.sleep(.15)
                subprocess.run(['scrot', '--overwrite', str(destination)], check=True, timeout=8)
                with Image.open(destination) as shot:
                    if shot.size != tuple(screen_size):
                        raise RuntimeError('Receiver capture dimensions differ from private framebuffer')
                    crop = shot.crop((row['x'], row['y'], row['x'] + row['width'], row['y'] + row['height'])).convert('RGB')
                    deviation = max(ImageStat.Stat(crop).stddev)
                    foreground_pixels = sum(all(abs(value-channel)<=2 for value,channel in zip(pixel,foreground)) for pixel in crop.getdata())
                paint_attempts.append({'foreground_pixels': foreground_pixels, 'pixel_deviation': deviation})
                if deviation >= 2 and foreground_pixels >= 5:
                    break
                if time.monotonic() >= paint_deadline:
                    raise RuntimeError('Receiver screenshot is blank or unpainted after bounded wait: ' + name)
            command('close', name)
            if name in command('status'):
                raise RuntimeError('Authored receiver window failed to close: ' + name)
            close_deadline = time.monotonic() + 6
            opened_ids = {native['id'] for native in matched}
            while True:
                remaining = [native for native in windows(opened_ids) if native['id'] in opened_ids
                    or any(name in value.casefold() for value in [native['title'] or '', *native['class']])]
                if not remaining:
                    break
                if time.monotonic() >= close_deadline:
                    raise RuntimeError('Authored receiver native window remained after close: '
                                       + name + '; ' + repr(remaining))
                time.sleep(.15)
            result['windows'][name] = {'active': active, 'native_windows': matched, 'capture': destination.name,
                                      'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                                      'paint_attempts': paint_attempts,
                                      'pixel_deviation': deviation, 'launcher_open_close': 'passed',
                                      'native_close': {'status': 'passed', 'remaining': remaining,
                                                       'opened_ids': [native['id'] for native in matched]},
                                      'paint': {'foreground':foreground,'foreground_pixels':foreground_pixels,
                                                'crop_width':row['width'],'crop_height':row['height'],'pixel_deviation':deviation}}
        if player_started:
            import json
            proof = Path(private_root.name) / 'player/acceptance.json'
            value = json.loads(proof.read_text())
            if value.get('media_type') != 'actual' or value.get('status') != 'passed':
                raise RuntimeError('Actual private player transport did not pass')
            result['actual_private_player'] = {'status': 'passed', 'media_type': 'actual',
                'capture': 'receiver-evidence/player/acceptance.json',
                'sha256': hashlib.sha256(proof.read_bytes()).hexdigest(),
                'scope': value['scope'], 'physical_frontend_interactions': 'unverified'}
        result['status'] = 'passed'
        return result
    finally:
        try:
            command('stop')
        finally:
            try:
                if player_started:
                    command('stop', player=True)
            finally:
                import shutil
                evidence = run / 'receiver-evidence'
                evidence.mkdir(exist_ok=True)
                for name in ('private-receiver-contract.json','state.json','actions.jsonl'):
                    source = Path(private_root.name) / name
                    if source.is_file():shutil.copy2(source,evidence/name)
                player_evidence = Path(private_root.name) / 'player'
                if player_evidence.is_dir():
                    destination = evidence / 'player'
                    destination.mkdir(exist_ok=True)
                    for name in ('prepared.json','discovery.json','acceptance.json','cleanup.json','binding.json','transport-actions.jsonl','mpv.log'):
                        source = player_evidence / name
                        if source.is_file():shutil.copy2(source,destination/name)
                private_root.cleanup()
