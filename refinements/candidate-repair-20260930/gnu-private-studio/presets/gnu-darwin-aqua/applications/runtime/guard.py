#!/usr/bin/python3
"""Cooperative Studio mutation lock; no desktop calls or implicit recovery."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import threading

BUSY = {'applying', 'pending', 'restoring', 'recovery-required'}
_held = {}
_mutex = threading.RLock()


def read(path):
    return json.loads(Path(path).read_text())


def pending(studio):
    """Current transactions only. Archived history is not an activation registry."""
    studio = Path(studio).resolve()
    found = []
    for latest in sorted((studio / 'presets').glob('*/state/latest.json')):
        ident = read(latest)['id']
        if not isinstance(ident, str) or Path(ident).name != ident:
            raise RuntimeError('Invalid latest receipt identity: ' + str(latest))
        receipt = latest.parent / ident / 'desktop.json'
        state = read(receipt)
        recovery = receipt.with_name('recovery.json')
        if state.get('status') in BUSY or (recovery.exists() and read(recovery).get('status') == 'recovery-required'):
            found.append({'preset': latest.parents[1].name, 'receipt': str(receipt.resolve()), 'source': str(receipt)})
    for marker in sorted((studio / 'state/activation-trials').glob('*.json')):
        state = read(marker)
        if state.get('status') in BUSY:
            found.append({'preset': state['preset'], 'receipt': str(Path(state['receipt']).resolve()), 'source': str(marker), 'supplement': True})
    return found


@contextmanager
def locked(studio, wait=False):
    """Reentrant only in this thread/process; child processes never inherit the fd.

    Recovery waits instead of losing the one-shot timer on a busy lock. Normal
    interactive mutations fail promptly. No lock is held across the trial's
    human inspection interval, only while a controller command is executing.
    """
    key = str(Path(studio).resolve())
    with _mutex:
        current = _held.get(key)
        identity = (os.getpid(), threading.get_ident())
        if current:
            if current[0] != identity:
                raise RuntimeError('Activation guard already held by another thread')
            current[1] += 1
        else:
            state = Path(key) / 'state'
            state.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(state / 'activation.lock', os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except BlockingIOError:
                os.close(descriptor)
                raise RuntimeError('Another Studio appearance command is running; retry after it finishes') from None
            _held[key] = [identity, 1, descriptor]
    try:
        yield
    finally:
        with _mutex:
            current = _held[key]
            current[1] -= 1
            if not current[1]:
                os.close(current[2])
                del _held[key]


def assert_allowed(studio, preset, command, receipt=None):
    wanted = str(Path(receipt).resolve()) if receipt else None
    for item in pending(studio):
        same_owner = item['preset'] == preset and wanted == item['receipt']
        if command in {'apply', 'refine'} or not same_owner or (item.get('supplement') and command not in {'rollback', 'refine-keep', 'refresh', 'check'}):
            raise RuntimeError('Unresolved Studio trial belongs to ' + item['preset'] + ': ' + item['source'])


@contextmanager
def operation(studio, preset, command, receipt=None):
    with locked(studio, wait=command in {'recover', 'rollback'}):
        assert_allowed(studio, preset, command, receipt)
        yield
