#!/usr/bin/python3
"""One-shot Nothing Island telemetry/actions. Snapshot never invokes Collector or controls."""
import concurrent.futures
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

EWW = Path.home() / 'Documents/eww-graphite-brass'
MEDIA_SOURCE = EWW / 'config/scripts/backend.py'
PROC = Path('/proc')
SYS = Path('/sys')
PROBE_TIMEOUT = .8
MEDIA_TIMEOUT = 2.5
ENV = dict(os.environ, LC_ALL='C', LANG='C')


class ProbeError(RuntimeError):
    pass


def run(argv, timeout=PROBE_TIMEOUT):
    """No shell interpretation, finite lifetime, human-readable failures."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=ENV)
    except subprocess.TimeoutExpired:
        raise ProbeError(f'{argv[0]} timed out') from None
    except OSError as exc:
        raise ProbeError(f'{argv[0]} unavailable: {exc.strerror}') from None
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().replace('\n', ' ')[:220]
        raise ProbeError(f'{argv[0]} failed' + (': ' + detail if detail else f' ({result.returncode})'))
    return result.stdout.strip()


def percentage(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ProbeError('Percentage must be a number between 0 and 100') from None
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ProbeError('Percentage must be between 0 and 100')
    return number


def cpu_sample(cache=None, proc=PROC, now=None):
    values = [int(v) for v in (proc / 'stat').read_text().splitlines()[0].split()[1:9]]
    if len(values) < 5:
        raise ProbeError('CPU counters unavailable')
    current = {'total': sum(values), 'idle': values[3] + values[4],
               'boot': (proc / 'sys/kernel/random/boot_id').read_text().strip(),
               'at': time.monotonic() if now is None else now}
    if cache is None:
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
        if not runtime.is_dir():
            runtime = Path(tempfile.gettempdir())
            cache = runtime / f'nothing-island-{os.getuid()}'
        else:
            cache = runtime / 'nothing-island'
    cache = Path(cache)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    if cache.is_symlink() or cache.stat().st_uid != os.getuid():
        raise ProbeError('CPU cache directory ownership is unsafe')
    with (cache / 'cpu.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        previous = None
        try:
            previous = json.loads((cache / 'cpu.json').read_text())
        except (OSError, ValueError):
            pass
        fd, name = tempfile.mkstemp(prefix='.cpu-', dir=cache)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(current, stream)
            os.replace(name, cache / 'cpu.json')
        finally:
            Path(name).unlink(missing_ok=True)
        if not isinstance(previous, dict):
            return None
        try:
            elapsed = current['at'] - previous['at']
            total = current['total'] - previous['total']
            idle = current['idle'] - previous['idle']
            if current['boot'] != previous['boot'] or not 0 < elapsed < 60 or total <= 0 or not 0 <= idle <= total:
                return None
            return round(100 * (total - idle) / total, 1)
        except (KeyError, TypeError):
            return None


def memory_sample(proc=PROC):
    values = {key: int(value.split()[0]) for key, value in
              (line.split(':', 1) for line in (proc / 'meminfo').read_text().splitlines())}
    total, available = values['MemTotal'], values['MemAvailable']
    if total <= 0 or not 0 <= available <= total:
        raise ProbeError('Memory counters unavailable')
    used = total - available
    return round(100 * used / total, 1), f'{used / 1048576:.1f} / {total / 1048576:.1f} GiB'


def temperature_sample(sysroot=SYS):
    files = []
    for zone in sorted((sysroot / 'class/thermal').glob('thermal_zone*')):
        try:
            label = (zone / 'type').read_text().strip().lower()
            if any(word in label for word in ('cpu', 'x86_pkg', 'soc')):
                files.append(zone / 'temp')
        except OSError:
            continue
    for hwmon in sorted((sysroot / 'class/hwmon').glob('hwmon*')):
        try:
            if (hwmon / 'name').read_text().strip() in ('coretemp', 'k10temp', 'zenpower', 'cpu_thermal'):
                files.extend(sorted(hwmon.glob('temp*_input')))
        except OSError:
            continue
    samples = []
    for path in files:
        try:
            value = float(path.read_text()) / 1000
            if math.isfinite(value) and -20 <= value <= 150:
                samples.append(value)
        except (OSError, ValueError):
            pass
    return round(max(samples), 1) if samples else None


def battery_sample(sysroot=SYS):
    for battery in sorted((sysroot / 'class/power_supply').glob('*')):
        try:
            if (battery / 'type').read_text().strip() != 'Battery':
                continue
            return percentage((battery / 'capacity').read_text()), (battery / 'status').read_text().strip()
        except OSError:
            continue
    return None, 'No battery'


def audio_probe():
    volume = run(['pactl', 'get-sink-volume', '@DEFAULT_SINK@'])
    levels = re.findall(r'\b(\d+)%', volume)
    if not levels:
        raise ProbeError('Audio volume unavailable')
    # Display the loudest channel, capped at the widget's existing 100% limit.
    value = min(100, max(int(v) for v in levels))
    muted = run(['pactl', 'get-sink-mute', '@DEFAULT_SINK@'])
    if not re.fullmatch(r'Mute:\s*(yes|no)', muted):
        raise ProbeError('Audio mute state unavailable')
    return {'volume': value, 'muted': muted.endswith('yes'), 'audio': True}


def network_probe():
    devices = run(['nmcli', '-t', '--escape', 'no', '-f', 'TYPE,STATE,CONNECTION', 'device', 'status'])
    rows = [line.split(':', 2) for line in devices.splitlines()]
    wifi_present = any(len(row) == 3 and row[0] == 'wifi' for row in rows)
    active = [row[2] for row in rows if len(row) == 3 and row[1] == 'connected' and row[0] in ('wifi', 'ethernet')]
    enabled = None
    if wifi_present:
        state = run(['nmcli', 'radio', 'wifi'])
        if state not in ('enabled', 'disabled'):
            raise ProbeError('Wi-Fi radio state unavailable')
        enabled = state == 'enabled'
    return {'network': active[0] if active else 'Disconnected', 'wifi': enabled, 'wifi_available': wifi_present}


def bluetooth_probe():
    response = run(['bluetoothctl', 'show'])
    powered = re.search(r'^\s*Powered:\s*(yes|no)\s*$', response, re.M)
    if not powered:
        if 'No default controller available' in response:
            return {'bluetooth': None, 'bluetooth_available': False}
        raise ProbeError('Bluetooth controller state unavailable')
    return {'bluetooth': powered.group(1) == 'yes', 'bluetooth_available': True}


def weather_sample(paths=None, now=None):
    paths = paths or [EWW / 'state/weather.json', Path.home() / '.local/share/mint-dashboard/weather-cache.json']
    now = time.time() if now is None else now
    candidates = []
    for path in paths:
        try:
            data = json.loads(path.read_text())
            fetched = float(data['fetched_at']);temp = float(data['temperature'])
            if not math.isfinite(fetched) or not math.isfinite(temp) or fetched > now + 60:
                continue
            candidates.append((fetched, data))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if not candidates:
        return 'Weather unavailable', 'No cached forecast'
    fetched, data = max(candidates, key=lambda pair: pair[0])
    location = str(data.get('location') or 'Cached location')
    age = now - fetched
    if age >= 21600:
        return 'Weather unavailable', location + ' · outdated cache'
    return f"{data['temperature']}{data.get('unit', '')} · {data.get('label', '')}", location + (' · cached' if age > 3600 else '')


def load_media():
    # Import is read-only: suppress .pyc, never instantiate Collector/weather/timer.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('nothing_island_media', MEDIA_SOURCE)
    if spec is None or spec.loader is None:
        raise ProbeError('Existing media backend unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def media_probe():
    data = json.loads(run(['/usr/bin/python3', str(Path(__file__).resolve()), '_media-snapshot'], timeout=MEDIA_TIMEOUT))
    if not isinstance(data, dict):
        raise ProbeError('Media backend returned invalid data')
    return normalize_media(data)


def empty_media():
    return {'title': 'Nothing playing', 'artist': '', 'cover': '', 'playing': False,
            'progress': 0, 'duration': 0, 'elapsed': '0:00', 'total': '0:00', 'player': ''}


def normalize_media(data):
    out = empty_media()
    for key in ('title', 'artist', 'cover', 'elapsed', 'total', 'player'):
        out[key] = str(data.get(key) or out[key])
    out['playing'] = bool(data.get('playing', False))
    for key in ('progress', 'duration'):
        try:
            value = float(data.get(key, 0))
            out[key] = max(0, min(100, value)) if key == 'progress' and math.isfinite(value) else max(0, value) if math.isfinite(value) else 0
        except (ValueError, TypeError):
            out[key] = 0
    return out


def snapshot():
    result = {'cpu': None, 'memory': None, 'memory_text': 'Unavailable', 'temperature': None,
              'battery': None, 'battery_status': 'Unknown', 'network': 'Unavailable', 'wifi': None,
              'bluetooth': None, 'volume': None, 'muted': False, 'weather': 'Weather unavailable',
              'weather_place': 'No cached forecast', 'media': empty_media(),
              'capabilities': {'audio': False, 'wifi': False, 'bluetooth': False}, 'errors': []}
    # Independent bounded probes run concurrently; no service start or network fetch.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {key: pool.submit(fn) for key, fn in [('audio', audio_probe), ('network', network_probe), ('bluetooth', bluetooth_probe), ('media', media_probe)]}
        for key, fn in [('cpu', cpu_sample), ('memory', memory_sample), ('temperature', temperature_sample), ('battery', battery_sample), ('weather', weather_sample)]:
            try:
                value = fn()
                if key == 'memory':result['memory'], result['memory_text'] = value
                elif key == 'battery':result['battery'], result['battery_status'] = value
                elif key == 'weather':result['weather'], result['weather_place'] = value
                else:result[key] = value
            except Exception as exc:
                result['errors'].append(key + ': ' + str(exc)[:220])
        for key, future in pending.items():
            try:
                value = future.result()
                if key == 'audio':
                    result.update(volume=value['volume'], muted=value['muted']);result['capabilities']['audio'] = value['audio']
                elif key == 'network':
                    result.update(network=value['network'], wifi=value['wifi']);result['capabilities']['wifi'] = value['wifi_available']
                elif key == 'bluetooth':
                    result['bluetooth'] = value['bluetooth'];result['capabilities']['bluetooth'] = value['bluetooth_available']
                else:result['media'] = value
            except Exception as exc:
                result['errors'].append(key + ': ' + str(exc)[:220])
    return result


def media_action(argv):
    backend = load_media()
    state = backend.media()
    player = state.get('player', '')
    if not player:
        raise ProbeError('No controllable music player is available')
    verb = argv[0]
    if player == 'mpv-ipc':
        command = {'toggle': ['cycle', 'pause'], 'previous': ['playlist-prev'], 'next': ['playlist-next']}.get(verb)
        if verb == 'seek':
            if not state.get('duration'):
                raise ProbeError('This track cannot be sought')
            command = ['seek', percentage(argv[1]), 'absolute-percent']
        # Existing ipc() cannot distinguish successful null replies from errors.
        # Use the same socket/protocol with explicit response checking.
        mpv_command(backend.SOCK, command)
    elif player.startswith('org.mpris.MediaPlayer2.'):
        if not backend.direct_mpris_action(state, verb, argv[1] if verb == 'seek' else None):
            raise ProbeError('The selected music player rejected the action')
    else:
        if verb == 'seek':
            if not state.get('duration'):
                raise ProbeError('This track cannot be sought')
            run(['playerctl', '-p', player, 'position', str(state['duration'] * percentage(argv[1]) / 100)], timeout=1.5)
        else:
            run(['playerctl', '-p', player, 'play-pause' if verb == 'toggle' else verb], timeout=1.5)


def mpv_command(path, command):
    import socket
    if not command:
        raise ProbeError('Unsupported media action')
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(.8)
        connection.connect(str(path))
        connection.sendall((json.dumps({'command': command, 'request_id': 7319}) + '\n').encode())
        raw = b'';deadline = time.monotonic() + 1.2
        while len(raw) < 65536 and time.monotonic() < deadline:
            chunk = connection.recv(4096)
            if not chunk:break
            raw += chunk
            while b'\n' in raw:
                line, raw = raw.split(b'\n', 1)
                reply = json.loads(line)
                if reply.get('request_id') == 7319:
                    if reply.get('error') != 'success':raise ProbeError('MPV rejected action: ' + str(reply.get('error')))
                    return
    raise ProbeError('MPV did not acknowledge the action')


def validate_action(argv):
    if not argv:
        raise ProbeError('An action is required')
    name = argv[0]
    if name == 'volume' and len(argv) == 2:
        percentage(argv[1]);return
    if name == 'media' and (len(argv) == 2 and argv[1] in ('toggle', 'previous', 'next') or len(argv) == 3 and argv[1] == 'seek'):
        if argv[1] == 'seek':percentage(argv[2])
        return
    if len(argv) == 1 and name in ('mute', 'wifi', 'bluetooth', 'network-settings', 'bluetooth-settings'):
        return
    raise ProbeError('Unsupported action or arguments')


def launch_settings(argv):
    if not shutil.which(argv[0]):raise ProbeError(argv[0] + ' is not installed')
    # Only this explicit user action starts an app; no inherited pipes keep the UI waiting.
    with tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=errors, start_new_session=True, env=ENV)
        try:
            code = child.wait(timeout=.25)
        except subprocess.TimeoutExpired:
            return
        if code:
            errors.seek(0)
            detail = errors.read(220).decode(errors='replace').strip()
            raise ProbeError('Settings could not start' + (': ' + detail if detail else f' ({code})'))


def action(argv):
    validate_action(argv)
    name = argv[0]
    if name == 'volume':run(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{percentage(argv[1]):g}%'], timeout=1.5)
    elif name == 'mute':run(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', 'toggle'], timeout=1.5)
    elif name == 'wifi':
        state = network_probe()
        if not state['wifi_available']:raise ProbeError('Wi-Fi hardware unavailable')
        run(['nmcli', 'radio', 'wifi', 'off' if state['wifi'] else 'on'], timeout=1.5)
    elif name == 'bluetooth':
        state = bluetooth_probe()
        if not state['bluetooth_available']:raise ProbeError('Bluetooth controller unavailable')
        response = run(['bluetoothctl', 'power', 'off' if state['bluetooth'] else 'on'], timeout=1.5)
        if re.search(r'failed|not available|not permitted', response, re.I):raise ProbeError(response[:220])
    elif name == 'network-settings':launch_settings(['cinnamon-settings', 'network'])
    elif name == 'bluetooth-settings':
        manager = next((name for name in ('blueman-manager', 'blueberry') if shutil.which(name)), None)
        if manager is None:raise ProbeError('Bluetooth settings are not installed')
        launch_settings([manager])
    elif name == 'media':
        raw = run(['/usr/bin/python3', str(Path(__file__).resolve()), '_media-action', *argv[1:]], timeout=4)
        reply = json.loads(raw)
        if not reply.get('ok'):raise ProbeError(reply.get('error', 'Media action failed'))
    return {'ok': True, 'action': name}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ['snapshot']:output = snapshot()
        elif argv == ['_media-snapshot']:output = load_media().media()
        elif argv[:1] == ['_media-action']:
            validate_action(['media', *argv[1:]]);media_action(argv[1:]);output = {'ok': True}
        elif argv[:1] == ['action']:output = action(argv[1:])
        else:raise ProbeError('Usage: backend.py snapshot | action <name> [value]')
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:300]}, ensure_ascii=False))
        return 1

if __name__ == '__main__':
    sys.exit(main())
