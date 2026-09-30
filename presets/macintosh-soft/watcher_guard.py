"""Journaled pause of this user's exact Eww theme watcher; no import-time actions."""
from pathlib import Path
import json
import os
import re
import signal
import time
import uuid

EWW = Path.home() / 'Documents/eww-graphite-brass'
WATCHER = str(EWW / 'config/scripts/theme-watch.py')
SYNC = str(EWW / 'config/scripts/theme-sync.py')
RELOAD = [str(EWW / 'bin/eww'), '-c', str(EWW / 'config'), 'reload']
PROC = Path('/proc')
WAIT_SECONDS = 15


def snapshot(pid):
    """Start ticks plus boot ID distinguish reused PIDs across boots as well."""
    try:
        folder = PROC / str(pid)
        fields = (folder / 'stat').read_text().rpartition(')')[2].split()
        return {'pid': int(pid), 'uid': folder.stat().st_uid,
                'start': fields[19], 'boot': (PROC / 'sys/kernel/random/boot_id').read_text().strip(),
                'state': fields[0], 'ppid': int(fields[1]),
                'argv': (folder / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0'),
                'exe': os.readlink(folder / 'exe')}
    except (FileNotFoundError, ProcessLookupError):
        return None


def processes():
    result = []
    for folder in PROC.iterdir():
        if not folder.name.isdecimal(): continue
        try:
            if folder.stat().st_uid != os.getuid(): continue
            item = snapshot(int(folder.name))
            if item: result.append(item)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return result


def python_script(item, script):
    return (item['argv'] == [item['argv'][0], script]
            and re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', Path(item['exe']).name) is not None)


def watcher(item):
    return item is not None and item['uid'] == os.getuid() and python_script(item, WATCHER)


def same(item, saved):
    return watcher(item) and all(item[key] == saved[key] for key in ('pid', 'uid', 'start', 'boot'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex)
    try:
        with temp.open('x') as stream:
            os.chmod(temp, 0o600)
            json.dump(data, stream, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        temp.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if temp.exists(): temp.unlink()


def load(path):
    path = Path(path)
    if not path.exists(): return {'version': 1, 'watcher': WATCHER, 'processes': [], 'status': 'new'}
    data = json.loads(path.read_text())
    if data.get('version') != 1 or data.get('watcher') != WATCHER:
        raise RuntimeError('Foreign watcher journal: ' + str(path))
    return data


def signal_matching(saved, number):
    # PID fd binds the signal target even if a PID is reused after verification.
    try: fd = os.pidfd_open(saved['pid'])
    except ProcessLookupError: return False
    try:
        if not same(snapshot(saved['pid']), saved): return False
        try: signal.pidfd_send_signal(fd, number)
        except ProcessLookupError: return False
        return True
    finally: os.close(fd)


def wait_quiet(rows):
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        pending = []
        parents = {}
        for row in rows:
            current = snapshot(row['pid'])
            if not same(current, row): continue
            parents[row['pid']] = row
            if current['state'] not in ('T', 't', 'Z'): pending.append(row['pid'])
        for item in processes():
            if item['ppid'] not in parents or item['state'] == 'Z': continue
            if not (python_script(item, SYNC) or item['argv'] == RELOAD):
                raise RuntimeError('Unexpected watcher child; left untouched: ' + str(item['pid']))
            pending.append(item['pid'])
        if not pending: return
        if time.monotonic() >= deadline:
            raise RuntimeError('Watcher child did not finish; left untouched: ' + repr(pending))
        time.sleep(.05)


def resume(path):
    """Resume only watchers this journal found running before its pause."""
    if not Path(path).exists(): return {'status': 'absent', 'processes': []}
    data = load(path)
    errors = []
    for row in data['processes']:
        if not row.get('resume_required'): continue
        try:
            row['resume_result'] = 'resumed' if signal_matching(row, signal.SIGCONT) else 'exited-or-replaced'
            row['resume_required'] = False
            save(path, data)
        except Exception as error:
            errors.append(str(error))
    data['status'] = 'resume-required' if errors else 'resumed'
    data['errors'] = errors
    save(path, data)
    if errors: raise RuntimeError('Watcher resume failed: ' + '; '.join(errors))
    return data


def pause(path):
    """Pause exact watchers, journal first, then drain already-running children.

    Re-entering with a crash journal retains its outstanding resume obligations.
    The coordinator must call resume in its finally and independent recovery path.
    """
    data = load(path)
    data['status'] = 'pausing'; data['errors'] = []
    save(path, data)
    try:
        for item in processes():
            if not watcher(item): continue
            row = next((r for r in data['processes'] if same(item, r)), None)
            if row is None:
                row = {key: item[key] for key in ('pid', 'uid', 'start', 'boot')}
                row.update(resume_required=False, initially_stopped=item['state'] in ('T', 't'))
                data['processes'].append(row)
            if not row.get('resume_required'):
                row['initially_stopped'] = item['state'] in ('T', 't')
                if row['initially_stopped']: continue
                row['resume_required'] = True
            # Durable intent precedes SIGSTOP, including the crash-between case.
            save(path, data)
            signal_matching(row, signal.SIGSTOP)
        wait_quiet(data['processes'])
        data['status'] = 'paused'; save(path, data)
        return data
    except Exception:
        resume(path)
        raise
