#!/usr/bin/python3
"""Terminal theme selection with coordinated desktop and boot/login recovery."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

# Published authorities use the versioned user-local runtime helper. Fixture
# tests pre-load this module explicitly; there is no production env override.
if 'nim_transaction_support' not in sys.modules:
    import importlib.util as _support_import
    _support_path = Path.home()/'.local/share/desktop-theme-studio/nim/helpers/nim_transaction_support.py'
    _support_spec = _support_import.spec_from_file_location('nim_transaction_support', _support_path)
    _support_module = _support_import.module_from_spec(_support_spec)
    sys.modules['nim_transaction_support'] = _support_module
    _support_spec.loader.exec_module(_support_module)
import nim_transaction_support as transaction_support

# Resolve local helpers even when invoked through ~/.local/bin/theme.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import konsole_runtime

ROOT = Path(__file__).resolve().parent
STORE = ROOT / 'state/shortcuts'
LEGACY_STORE = Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'ocean-silk/snapshots'
SYSTEM = ROOT / 'runtime-quality/system-stage/system.py'
PROFILES = json.loads((ROOT / 'profiles.json').read_text())['profiles']
FINISHED = {'active', 'restored', 'rolled-back'}
CANDIDATE_RELEASE_SLUGS = {
    'dusk-ribbons', 'macintosh-soft', 'macintosh-soft-evergreen',
    'moonstone-stereo', 'ocean-silk', 'plum-afterglow', 'quiet-sage',
}
ID_PATTERN = r'\d{8}-\d{6}-[0-9a-f]{6,32}'


class ShortcutError(RuntimeError):
    pass


def normalize_name(text):
    return re.sub(r'[^a-z0-9]', '', text.casefold().replace('&', 'and'))


def resolve_profile(text, profiles=PROFILES):
    wanted = normalize_name(text)
    matches = {slug for slug, row in profiles.items()
               if wanted in {normalize_name(slug), normalize_name(row['name']),
                             normalize_name(row['theme']),
                             *(normalize_name(alias) for alias in row.get('cli_aliases', []))}}
    if len(matches) != 1:
        raise ShortcutError('Unknown or ambiguous theme. Run: theme list')
    return matches.pop()


def stamp():
    return datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:12]


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def valid_id(ident):
    if not isinstance(ident, str) or not re.fullmatch(ID_PATTERN, ident):
        raise ShortcutError('Invalid shortcut receipt identity')
    return ident


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


# Hangup evidence that a finished restore/rollback moves into `history`.
HANGUP_EVIDENCE = ('terminal_hangup', 'output_closed', 'root_unavailable', 'console_log')


def archive_hangup_evidence(data):
    """Keep earlier hangup evidence of a restored receipt only in its history."""
    entry = {key: data.pop(key) for key in HANGUP_EVIDENCE if key in data}
    if entry:
        entry.update(archived_on=data.get('status'), archived_at=data.get('updated_at'))
        data.setdefault('history', []).append(entry)


class TerminalConsole:
    """Keep a closed terminal or pipe from breaking a started switch or its recovery.

    Installed by main() only (library callers and tests keep their streams).
    It stays fully transparent until bind() arms it, right before a
    transaction's first receipt save (switch: after the id is stamped;
    restore: right before recovery starts). Until then every write, flush and
    failure behaves exactly as without it, so a closed pipe or a hangup before
    any change aborts exactly as before (e.g. `theme: [Errno 32] Broken pipe`),
    and nothing is written. Once armed, output still reaches the terminal or
    pipe unchanged (same bytes, buffering, ordering) until a write fails or a
    SIGHUP arrives. Two events then divert it:
    - terminal hangup: the first deferred SIGHUP, or a failed console write
      other than EPIPE (EIO once the pty is closed). fds 0-2 become /dev/null,
      so children and interpreter shutdown can neither block nor fail on the
      dead terminal; all output continues in state/shortcuts/<id>/console.log,
      and recovery checks that root is reachable before the root stage.
    - output closed: EPIPE on one stream (`theme ... | head -1`). Only that
      stream moves to the log; the terminal, stdin and root are untouched.
    The receipt records terminal_hangup / output_closed and console_log.
    Nothing here raises into the transaction.
    """
    PENDING_LIMIT = 1 << 16

    def __init__(self):
        self.reason = None
        self.detected_at = None
        self.closed = {}
        self.log_path = None
        self._log_fd = None
        self._pending = []
        self._announced = set()
        self._detached = False
        self._saved = {}
        self._proxies = []
        self.armed = False

    @classmethod
    def install(cls):
        console = cls()
        for name in ('stdout', 'stderr'):
            console._saved[name] = getattr(sys, name)
            proxy = _ConsoleStream(console, name, console._saved[name])
            console._proxies.append(proxy)
            setattr(sys, name, proxy)
        return console

    def uninstall(self):
        # Once armed, flush here (stdout, then stderr, as interpreter exit
        # would) so a closed pipe is still handled by the proxy. Unarmed, leave
        # that flush to interpreter exit exactly as before.
        if self.armed:
            for proxy in self._proxies:
                proxy.flush()
        for name, original in self._saved.items():
            if isinstance(getattr(sys, name), _ConsoleStream):
                setattr(sys, name, original)

    @property
    def hung_up(self):
        return self.reason is not None

    def watch(self, signals):
        """Wrap a DeferredSignals receiver before its handlers are installed."""
        received = signals.receive

        def receive(signum, frame=None):
            received(signum, frame)
            if signum == signal.SIGHUP and self.reason is None:
                # Flag only: this runs as a signal handler.
                self.reason, self.detected_at = 'SIGHUP', timestamp()
        signals.receive = receive
        return signals

    def hangup(self, reason):
        if self.reason is None:
            self.reason, self.detected_at = reason, timestamp()
        self._detach()

    def close_stream(self, name, detail, fd):
        if name in self.closed:
            return
        self.closed[name] = {'detail': detail, 'at': timestamp()}
        if fd is None:
            return
        try:
            null = os.open(os.devnull, os.O_WRONLY)
        except OSError:
            return
        try:
            os.dup2(null, fd)   # leftover buffered bytes drain silently at exit
        except OSError:
            pass
        finally:
            if null != fd:
                os.close(null)

    def bind(self, directory):
        """Arm the console for a transaction; console.log lives in its folder."""
        self.armed = True
        if self.log_path is None:
            self.log_path = Path(directory) / 'console.log'
        if self.hung_up or self.closed:
            self.emit('')

    def emit(self, text):
        if self.hung_up:
            self._detach()
        self._open_log()
        self._announce()
        if text:
            self._put(text)

    def annotate(self, data):
        """Record diverted output in a receipt about to be saved (main flow only)."""
        if not (self.hung_up or self.closed):
            return
        self.emit('')
        if self.hung_up:
            data['terminal_hangup'] = {'detected': self.reason, 'at': self.detected_at}
        if self.closed:
            data['output_closed'] = {name: dict(info) for name, info in self.closed.items()}
        if self._log_fd is not None:
            data['console_log'] = str(self.log_path)

    def _announce(self):
        if self.hung_up and 'terminal' not in self._announced:
            self._announced.add('terminal')
            self._put(f'--- terminal gone ({self.reason}) at {self.detected_at}; '
                      f'theme continues without it (pid {os.getpid()}) ---\n')
        for name, info in self.closed.items():
            if name not in self._announced:
                self._announced.add(name)
                self._put(f'--- {name} closed ({info["detail"]}) at {info["at"]}; '
                          f'its output continues here (pid {os.getpid()}) ---\n')

    def _put(self, text):
        if self._log_fd is not None:
            self._write_log(text)
        elif sum(map(len, self._pending)) < self.PENDING_LIMIT:
            self._pending.append(text)

    def _detach(self):
        if self._detached:
            return
        self._detached = True
        try:
            null = os.open(os.devnull, os.O_RDWR)
        except OSError:
            return
        for fd in (0, 1, 2):
            try:
                os.dup2(null, fd)
            except OSError:
                pass
        if null > 2:
            os.close(null)

    def _open_log(self):
        if self._log_fd is not None or self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._log_fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                                   | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError:
            return
        pending, self._pending = self._pending, []
        for text in pending:
            self._write_log(text)

    def _write_log(self, text):
        data = text.encode('utf-8', 'replace')
        try:
            while data:
                data = data[os.write(self._log_fd, data):]
        except OSError:
            pass


class _ConsoleStream:
    """sys.stdout/sys.stderr stand-in: pass-through until armed and a write fails.

    It never adds a flush, so buffering and interleaving are unchanged. Before
    the console is armed a failure propagates exactly as before. Text
    that may still sit in the original's buffer (since its last flush) is
    shadowed, so a failure moves it to the log instead of losing it.
    """
    SHADOW_LIMIT = 1 << 16

    def __init__(self, console, name, original):
        self._console, self._name, self._original = console, name, original
        self._shadow = ''

    def _diverted(self):
        console = self._console
        return console.armed and (console.hung_up or self._name in console.closed)

    def write(self, text):
        if self._diverted():
            self._console.emit(text)
            return len(text)
        try:
            self._original.write(text)
        except (OSError, ValueError) as exc:
            if not self._console.armed:
                raise
            self._lost(exc, text)
            return len(text)
        if getattr(self._original, 'line_buffering', False) and ('\n' in text or '\r' in text):
            self._shadow = ''   # a line-buffered stream flushed its whole buffer
        else:
            self._shadow = (self._shadow + text)[-self.SHADOW_LIMIT:]
        return len(text)

    def flush(self):
        if self._diverted():
            return
        try:
            self._original.flush()
        except (OSError, ValueError) as exc:
            if not self._console.armed:
                raise
            self._lost(exc, '')
            return
        self._shadow = ''

    def isatty(self):
        if not self._console.armed:
            return self._original.isatty()
        if self._diverted():
            return False
        try:
            return self._original.isatty()
        except (OSError, ValueError):
            return False

    def fileno(self):
        return self._original.fileno()

    def __getattr__(self, name):
        return getattr(self._original, name)

    def _lost(self, exc, text):
        pending, self._shadow = self._shadow + text, ''
        code = getattr(exc, 'errno', None)
        detail = (errno.errorcode.get(code, str(code)) if code else type(exc).__name__) + ' on ' + self._name
        if code == errno.EPIPE and not self._console.hung_up:
            try:
                fd = self._original.fileno()
            except (OSError, ValueError, AttributeError):
                fd = None
            self._console.close_stream(self._name, detail, fd)
        else:
            self._console.hangup(detail)
        self._console.emit(pending)


class Shortcut:
    def __init__(self, store=STORE, runtime=None, console=None):
        self.store = Path(store)
        self.runtime = runtime or Runtime()
        # TerminalConsole from main(); None for library callers and tests.
        self.console = console

    def records(self):
        records = []
        for path in sorted(self.store.glob('*/receipt.json')):
            if path.is_symlink() or path.parent.is_symlink():
                raise ShortcutError('Shortcut receipt must be a regular local file')
            data = json.loads(path.read_text())
            valid_id(data.get('id'))
            if data['id'] != path.parent.name or data.get('profile') not in PROFILES or data.get('schema') != 1:
                raise ShortcutError('Foreign shortcut receipt: ' + str(path))
            records.append(data)
        return sorted(records, key=lambda row: (row.get('created_at', ''), row['id']))

    def save(self, data, status=None):
        if status:
            data['status'] = status
        data['updated_at'] = timestamp()
        if self.console is not None:
            self.console.annotate(data)
        if data.get('status') in ('restored', 'rolled-back'):
            archive_hangup_evidence(data)
        write_json(self.store / valid_id(data['id']) / 'receipt.json', data)

    def unresolved(self, rows):
        return [row for row in rows if row.get('status') not in FINISHED]

    def selected(self, rows):
        pending = self.unresolved(rows)
        if len(pending) > 1:
            raise ShortcutError('Multiple unfinished shortcut receipts need review: ' + ', '.join(r['id'] for r in pending))
        return pending[0] if pending else next((r for r in reversed(rows) if r['status'] == 'active'), None)

    def recover(self, data, rollback=False):
        """Reverse the exact attempted stages; never guess from a latest pointer."""
        ident, slug = data['id'], data['profile']
        attempt = data.get('system_attempt')
        legacy = attempt is None
        prior_status = data.get('previous_status', data.get('status'))
        evidence = {key: data.get(key) for key in (
            'system_started', 'system_worker', 'unsettled_worker', 'system_transaction')
            if key in data}
        if data.get('system_transaction', ident) != ident:
            raise ShortcutError('Paired receipt does not name its exact system transaction; inspect before recovery')
        if legacy:
            # Old receipts wrote system_started only after a successful apply.
            # desktop-applying is the only old status that proves execution had
            # not reached the root command. Every other unfinished status must
            # reconcile the exact profile/transaction conservatively.
            proven_desktop_only = (prior_status == 'desktop-applying'
                                   and not data.get('system_started')
                                   and not data.get('unsettled_worker'))
            system_required = not proven_desktop_only
            data['legacy_reconciliation'] = {
                'version': 1, 'source_status': prior_status,
                'source_evidence': evidence,
                'classification': 'proven-desktop-only' if proven_desktop_only else 'exact-root-reconciliation-required',
            }
        else:
            if attempt not in {'never', 'launch-intent', 'released', 'confirmed', 'restore-intent', 'restore-released', 'restored', 'reconciled-no-root-receipt'}:
                raise ShortcutError('Unknown system attempt state; inspect the exact transaction before recovery')
            system_required = (attempt in {'released', 'confirmed', 'restore-intent', 'restore-released'}
                              or bool(data.get('system_started'))
                              or bool(data.get('system_worker') or data.get('unsettled_worker')))
        transaction_support.assert_worker_settled(data.get('system_worker') or data.get('unsettled_worker'))
        # Old receipts had no durable worker identity. A root-stage status may
        # still be reconciled when its exact receipt proves terminal application;
        # a missing exact receipt cannot prove a delayed legacy child is gone.
        worker_unproven_legacy = legacy and system_required and not evidence.get('system_worker') and not evidence.get('unsettled_worker')
        desktop_required = data.get('desktop_started', False)
        self.save(data, 'restoring')
        errors = []
        try:
            desktop = self.runtime.desktop_receipt(ident)
            if desktop['status'] == 'missing' and desktop_required:
                raise ShortcutError('Expected desktop recovery receipt is missing')
            if desktop['status'] not in ('missing', 'restored'):
                result = self.runtime.desktop_restore(ident)
                if result['status'] != 'restored':
                    raise ShortcutError('Desktop restore did not complete')
        except BaseException as exc:
            errors.append('Desktop: ' + str(exc))
        # After a terminal hangup the session has no controlling terminal, so
        # sudo's tty-bound ticket from authorize() is unreachable for any
        # launcher. Probe (root runs only /usr/bin/true) before touching the
        # exact root receipt; if root is unavailable, leave the system
        # attempt as it is and name the split state for theme restore.
        if not errors and system_required and self.console is not None and self.console.hung_up:
            probe = getattr(self.runtime, 'root_unavailable', None)
            reason = probe() if probe is not None else None
            if reason:
                data['root_unavailable'] = {'after': self.console.reason, 'detail': reason}
                errors.append(('desktop restored' if desktop_required else 'desktop unchanged')
                              + '; boot/login ' + ('still ' if attempt == 'confirmed' else 'may still be ')
                              + slug + '; run `theme restore` from a terminal')
        # Do not unwind the system layer while the desktop remains selected
        # or has a conflict; keep the receipt retryable as one paired change.
        if not errors and system_required:
            try:
                system = self.runtime.system_receipt(slug, ident)
                if system.get('id', ident) != ident or system.get('profile', slug) != slug:
                    raise ShortcutError('System receipt discovery returned a different profile or transaction')
                if system['status'] == 'missing' and system_required:
                    if legacy or attempt == 'confirmed':
                        raise ShortcutError('Expected exact boot/login recovery receipt is missing; recovery remains unresolved')
                    data['system_attempt'] = 'reconciled-no-root-receipt'
                    data['reconciliation_result'] = 'Exact receipt missing after associated worker settlement; no root stage was recorded.'
                    system_required = False
                if system['status'] in ('invalid', 'busy'):
                    raise ShortcutError('Exact boot/login receipt is ' + system['status'] + '; recovery remains unresolved')
                if worker_unproven_legacy and system['status'] in ('missing', 'invalid', 'busy'):
                    raise ShortcutError('Legacy root worker identity is unavailable and the exact receipt does not prove completed apply; retry after manual reconciliation')
                if system['status'] not in ('missing', 'restored', 'rolled-back'):
                    data['system_attempt'] = 'restore-intent'
                    self.save(data, 'restoring')
                    result = self.runtime.system_restore(slug, ident, data, self.save)
                    if result.get('id', ident) != ident or result.get('profile', slug) != slug:
                        raise ShortcutError('System restore returned a different profile or transaction')
                    if result['status'] not in ('restored', 'rolled-back'):
                        raise ShortcutError('System restore did not complete')
                    data['system_attempt'] = 'restored'
                    self.save(data)
            except BaseException as exc:
                if isinstance(exc, transaction_support.ChildUnsettled):
                    data['unsettled_worker'] = exc.worker
                errors.append('Boot/login: ' + str(exc))
        data['recovery_errors'] = errors
        self.save(data, 'recovery-required' if errors else 'rolled-back' if rollback else 'restored')
        if errors:
            raise ShortcutError('Recovery needs attention. Run theme restore again after resolving: ' + '; '.join(errors))
        return data

    def signals(self):
        signals = transaction_support.DeferredSignals()
        return self.console.watch(signals) if self.console is not None else signals

    def bind_console(self, ident):
        if self.console is not None:
            self.console.bind(self.store / valid_id(ident))

    def switch(self, slug):
        with self.signals() as signals:
            return self._switch(slug, signals)

    def _switch(self, slug, signals):
        if slug not in PROFILES:
            raise ShortcutError('Unknown theme: ' + str(slug))
        with self.runtime.transaction_lock():
            transaction_support.assert_current_start(LEGACY_STORE)
            rows = self.records()
            if self.unresolved(rows):
                raise ShortcutError('An unfinished switch exists. Run: theme restore')
            # This also checks application writers before asking for root or
            # changing any system-owned appearance files.
            preview = self.runtime.preflight(slug)
            signals.checkpoint()
            self.runtime.authorize()
            signals.checkpoint()
            ident = stamp()
            self.bind_console(ident)
            data = {'schema': 1, 'id': ident, 'profile': slug, 'system_attempt': 'never',
                    'name': PROFILES[slug]['name'], 'created_at': timestamp(),
                    'before': self.runtime.current(), 'preview': preview,
                    'desktop_receipt': str(ROOT / 'state' / ident / 'receipt.json'),
                    'system_transaction': ident,
                    'visual_verification': 'Boot and login appearance await a real reboot/login.'}
            # Apply the visible user session first. Its five-minute recovery
            # timer remains armed while the matching root-owned stage runs.
            # This makes the requested desktop/app result reviewable before
            # GRUB, Plymouth or Slick Greeter are changed.
            self.save(data, 'desktop-applying')
            try:
                self.runtime.desktop_apply(slug, ident)
                data['desktop_started'] = True
                self.save(data)
                signals.checkpoint()
                if not self.runtime.desktop_check(ident).get('ok'):
                    raise ShortcutError('Desktop verification failed')
                signals.checkpoint()
                data['system_attempt'] = 'launch-intent'
                self.save(data, 'system-applying')
                self.runtime.system_apply(slug, ident, data, self.save)
                # A successful apply response already establishes a recovery
                # obligation, even if its next receipt lookup fails.
                data['system_started'] = True
                data['system_attempt'] = 'confirmed'
                self.save(data)
                signals.checkpoint()
                if self.runtime.system_receipt(slug, ident)['status'] != 'applied':
                    raise ShortcutError('System stage did not retain an applied receipt')
                if not self.runtime.system_check(slug, ident).get('ok'):
                    raise ShortcutError('Boot/login verification failed')
                signals.checkpoint()
                self.save(data, 'committing')
                kept = self.runtime.desktop_keep(ident)
                if kept['status'] != 'kept':
                    raise ShortcutError('Desktop selection was not retained')
                signals.checkpoint()
                self.save(data, 'active')
                return data
            except transaction_support.ChildUnsettled as exc:
                data['unsettled_worker'] = exc.worker
                data['system_worker'] = exc.worker
                data['error'] = str(exc)
                self.save(data, 'recovery-required')
                raise ShortcutError(str(exc)) from exc
            except BaseException as exc:
                data['error'] = str(exc) or type(exc).__name__
                data['previous_status'] = data.get('status')

                self.save(data, 'recovery-required')
                try:
                    self.recover(data, rollback=True)
                except BaseException as recovery:
                    raise ShortcutError(str(recovery)) from exc
                raise ShortcutError('Switch failed; every started stage was restored. ' + data['error']) from exc

    def restore(self):
        with self.signals():
            return self._restore()

    def _restore(self):
        with self.runtime.transaction_lock():
            data = self.selected(self.records())
            if not data:
                return {'status': 'nothing-to-restore'}
            attempt = data.get('system_attempt')
            has_worker = bool(data.get('system_worker') or data.get('unsettled_worker'))
            legacy = 'system_attempt' not in data
            proven_desktop_only = (legacy and data.get('status') == 'desktop-applying'
                                   and not data.get('system_started') and not has_worker)
            pre_release_only = (not legacy and attempt in ('never', 'launch-intent')
                                and not data.get('system_started') and not has_worker)
            if not (proven_desktop_only or pre_release_only):
                self.runtime.authorize()
            self.bind_console(data['id'])
            return self.recover(data)

    def status(self):
        rows = self.records()
        selected = self.selected(rows)
        return {'current': self.runtime.current(),
                'shortcut': {key: selected[key] for key in ('id', 'profile', 'name', 'status')} if selected else None,
                'history_count': len(rows),
                'current_recovery_errors': selected.get('recovery_errors', []) if selected else [],
                'historical_error': selected.get('error') if selected else None,
                'verification': 'Configuration observation only; theme check verifies the selected shortcut receipt.'}

    def check(self):
        with self.runtime.transaction_lock():
            data = self.selected(self.records())
            if not data or data['status'] != 'active':
                raise ShortcutError('No retained shortcut selection to check. Use theme status or theme restore.')
            self.runtime.authorize()
            desktop = self.runtime.desktop_check(data['id'])
            system = self.runtime.system_check(data['profile'], data['id'])
            return {'ok': bool(desktop.get('ok') and system.get('ok')),
                    'profile': data['profile'], 'desktop': desktop, 'system': system,
                    'visual_verification': 'Actual boot/login pixels require reboot/login.'}


class Runtime:
    def __init__(self):
        self._desktop = None

    @property
    def desktop(self):
        if self._desktop is None:
            spec = importlib.util.spec_from_file_location('theme_shortcut_desktop', ROOT / 'live.py')
            self._desktop = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._desktop)
        return self._desktop

    @contextlib.contextmanager
    def transaction_lock(self):
        if os.geteuid() == 0:
            raise ShortcutError('Run theme as your normal desktop user; it requests sudo only for boot/login.')
        STORE.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (STORE / 'switch.lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ShortcutError('Another theme shortcut is running.') from exc
            with self.desktop.guard.locked(self.desktop.STUDIO):
                yield

    def run_json(self, command):
        result = transaction_support.run_captured(command, timeout=1200)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ShortcutError(detail[-5000:] or 'Command failed: ' + command[0])
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ShortcutError('Command returned no valid receipt; use theme restore to recover.') from exc

    def system_command(self, slug, action, ident=None, commit=False, privileged=False):
        args = ['/usr/bin/python3', str(SYSTEM), slug, action]
        if ident:
            args += ['--transaction', valid_id(ident)]
        if commit:
            args += ['--commit']
        if privileged:
            # Interactive terminal use authenticates once, then keeps every
            # later system command non-interactive: the helper launches root
            # workers in their own process group but in this terminal
            # session, where sudo's tty-bound ticket from authorize() is
            # valid. Desktop coordinators without a terminal can instead
            # provide a private graphical askpass helper; in that mode `-A`
            # is used on each privileged boundary.
            askpass = bool(os.environ.get('SUDO_ASKPASS'))
            args = ['sudo', '-A' if askpass else '-n', '--'] + args
        return self.run_json(args)

    def authorize(self):
        # Keep the password exchange on the user's terminal, never in a log.
        args = ['sudo', '-A', '-v'] if os.environ.get('SUDO_ASKPASS') else ['sudo', '-v']
        if subprocess.run(args).returncode:
            raise ShortcutError('Administrator authentication did not complete; this command made no appearance changes.')
        self.verify_privileged_launcher()

    def verify_privileged_launcher(self):
        """Prove both root worker launchers can reuse the authorization.

        Runs a no-op root command through run_captured (receipt/check
        commands) and through run_gated (apply/restore: sudo's parent is the
        subreaper supervisor), with the same session/process-group placement
        the boot/login stage uses, before any receipt is written or any
        appearance changes. The gated probe's worker record must settle.
        """
        askpass = bool(os.environ.get('SUDO_ASKPASS'))
        failure = self.probe_launchers(('run_captured', 'run_gated'))
        if failure:
            launcher, detail = failure
            hint = ('Check the SUDO_ASKPASS helper.' if askpass else
                    'Retry with a graphical password helper: SUDO_ASKPASS=/usr/bin/ssh-askpass theme NAME '
                    '(or SUDO_ASKPASS=/usr/bin/ssh-askpass theme restore).')
            raise ShortcutError('Administrator access was granted on this terminal, but the boot/login worker could not use it '
                                '(sudo ' + ('-A' if askpass else '-n') + ' true via ' + launcher + ' failed'
                                + (': ' + detail if detail else '') + '). Nothing was changed. ' + hint)

    def probe_launchers(self, launchers):
        """Run sudo -n|-A -- /usr/bin/true through each launcher; (launcher, detail) of the first failure."""
        askpass = bool(os.environ.get('SUDO_ASKPASS'))
        command = ['sudo', '-A' if askpass else '-n', '--', '/usr/bin/true']
        # -A may legitimately show the graphical prompt once more.
        timeout = 120 if askpass else 20
        failure = None
        for launcher in launchers:
            record = {}
            try:
                if launcher == 'run_captured':
                    result = transaction_support.run_captured(command, timeout=timeout)
                else:
                    result = transaction_support.run_gated(command, record.update, timeout=timeout)
                if result.returncode != 0:
                    failure = (launcher, (result.stderr or result.stdout or '').strip()[-500:])
                elif launcher == 'run_gated':
                    transaction_support.assert_worker_settled(record)
            except transaction_support.ChildUnsettled as exc:
                failure = (launcher, 'the check did not finish in time (' + str(exc.worker.get('pid')) + ')')
            except transaction_support.CoordinationError as exc:
                failure = (launcher, str(exc))
            if failure:
                break
        return failure

    def root_unavailable(self):
        """None when a non-interactive root command can run now, else why not.

        Used by recovery only after a terminal hangup; root runs /usr/bin/true.
        Without SUDO_ASKPASS both launchers are probed, exactly as before a
        switch: a ticket keyed to sudo's parent (timestamp_type=ppid, which is
        also where a tty ticket lookup falls back once the terminal is gone)
        can pass under run_captured (receipt lookup) and still fail under
        run_gated (restore). With SUDO_ASKPASS only run_captured is probed: it
        checks that the helper can authenticate, and its parent-keyed ticket
        can serve the receipt lookup that follows. Every run_gated call has a
        fresh supervisor parent, so a run_gated probe could never be reused
        and would only add one more dialog; the restore shows its own, and if
        it fails the receipt stays retryable (Boot/login error).
        """
        launchers = ('run_captured',) if os.environ.get('SUDO_ASKPASS') else ('run_captured', 'run_gated')
        try:
            failure = self.probe_launchers(launchers)
        except OSError as exc:
            return str(exc) or type(exc).__name__
        if failure:
            launcher, detail = failure
            return (detail or 'sudo failed') + ' (via ' + launcher + ')'
        return None

    def preview(self, slug):
        live = self.desktop
        plan = live.plan(slug)
        system = self.system_command(slug, 'preview')
        return {'profile': slug, 'name': PROFILES[slug]['name'],
                'desktop_appearance_fields': len(plan['actions']),
                'application_writers_that_must_be_closed': sorted({a['requires_closed'] for a in plan['actions'] if a.get('requires_closed')}),
                'system_files': system['files'], 'missing': system['missing'],
                'surfaces': ['Cinnamon desktop, applications, Island and Eww',
                             'GRUB artwork and selected-menu colors',
                             'Plymouth wallpaper, progress, password, question and messages',
                             'Slick Greeter background, GTK, icons and cursor'],
                'skipped': plan['skipped'],
                'changes': 'Preview only. theme NAME applies and retains after verification; theme restore undoes both stages.'}

    def preflight(self, slug):
        live = self.desktop
        live.guard.assert_allowed(live.STUDIO, live.OWNER, 'apply')
        live.workspace_runtime().require_receipt(slug,PROFILES[slug],ROOT,live.module)
        plan = live.plan(slug)
        live.ensure_closed(plan)
        live.validate(plan)
        # These seven profiles require the expanded visual + interaction and
        # paired-recovery receipt before they can ever enter the shared list.
        # The candidate registry remains separate until publication.
        release_module = ROOT / 'contributions' / 'all-theme-plan' / 'review_release.py'
        if slug in CANDIDATE_RELEASE_SLUGS:
            if not release_module.is_file():
                raise ShortcutError('Candidate release gate is missing; refusing to apply an unreviewed profile.')
            spec = importlib.util.spec_from_file_location('theme_review_release_gate', release_module)
            gate = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gate)
            if slug not in gate.CANDIDATE_SLUGS:
                raise ShortcutError('Candidate release gate does not recognize the profile; refusing to apply it.')
            evidence_root = ROOT / 'contributions' / 'all-theme-plan' / 'review-release' / slug
            accepted = evidence_root / 'accepted.json'
            if accepted.is_symlink() or not accepted.is_file() or accepted.resolve().parent != evidence_root.resolve():
                raise ShortcutError('Candidate is not eligible: display, interaction, paired recovery, and independent review evidence is required.')
            current = gate.package_fingerprint(slug)['sha256']
            receipt = gate.json.loads(accepted.read_text(encoding='utf-8'))
            result = gate.validate_receipt(slug, current, receipt, evidence_root)
            if not result['release_eligible']:
                raise ShortcutError('Candidate evidence is stale or incomplete: ' + '; '.join(result['problems'][:5]))
        if slug == 'tidal-observatory':
            gate_path = live.STUDIO / 'refinements/tidal-observatory-completion/harness/release_gate.py'
            gate = live.module('tidal_current_release_gate', gate_path)
            accepted = gate.EVIDENCE_ROOT / 'accepted.json'
            if accepted.is_symlink() or not accepted.is_file():
                raise ShortcutError('Tidal release evidence is missing; refusing to apply.')
            integration = ROOT / 'contributions/tidal-integration'
            if PROFILES[slug] != live.E.load(integration / 'profile.json'):
                raise ShortcutError('Tidal profile differs from the accepted integration specification.')
            if live.E.load(ROOT/'runtime-quality/system-stage/profiles/tidal-observatory.json') != live.E.load(integration/'system/tidal-observatory.json'):
                raise ShortcutError('Tidal system profile differs from the accepted package.')
            if gate.bundle_fingerprint(ROOT/'generated'/slug) != gate.bundle_fingerprint(integration/'assembled'):
                raise ShortcutError('Tidal deployed bundle differs from its reviewed package.')
            problems = gate.validate_receipt(live.E.load(accepted), gate.snapshot(), gate.EVIDENCE_ROOT)
            if problems:
                raise ShortcutError('Tidal release evidence is stale or incomplete: ' + '; '.join(problems[:5]))
        isolated = live.E.load(ROOT / 'verification/isolated-latest' / (slug + '.json'))
        preview = live.module('shortcut_isolated_hashes', ROOT / 'isolate.py')
        if isolated.get('status') != 'passed':
            raise ShortcutError('A passing private Cinnamon preview is required.')
        island_id = live.applet_uuid(PROFILES[slug])
        for key, tree in [('theme_sha256', ROOT / 'generated' / slug / 'desktop' / PROFILES[slug]['theme']),
                          ('island_sha256', ROOT / 'generated' / slug / 'island' / island_id)]:
            if isolated.get(key) != preview.sha256_tree(tree):
                raise ShortcutError('Generated theme changed since its isolated verification.')
        result = self.preview(slug)
        if result['missing']:
            raise ShortcutError('Missing system dependencies: ' + ', '.join(result['missing']))
        return result

    def system_apply(self, slug, ident, receipt=None, save=None):
        print('Updating boot/login artwork and rebuilding GRUB and initramfs…', file=sys.stderr, flush=True)
        args = ['/usr/bin/python3', str(SYSTEM), slug, 'apply', '--transaction', valid_id(ident), '--commit']
        askpass = bool(os.environ.get('SUDO_ASKPASS'))
        command = ['sudo', '-A' if askpass else '-n', '--'] + args
        def associate(worker):
            if receipt is not None and save is not None:
                receipt['system_worker'] = worker
                receipt['system_attempt'] = 'released'  # durable before gate byte
                save(receipt)
        result = transaction_support.run_gated(command, associate)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ShortcutError(detail[-5000:] or 'Command failed: ' + command[0])
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ShortcutError('Command returned no valid receipt; use theme restore to recover.') from exc

    def system_receipt(self, slug, ident):
        return self.system_command(slug, 'receipt-status', ident, privileged=True)

    def system_check(self, slug, ident):
        return self.system_command(slug, 'check', ident, privileged=True)

    def system_restore(self, slug, ident, receipt=None, save=None):
        print('Restoring previous boot/login appearance and rebuilding its boot files…', file=sys.stderr, flush=True)
        args = ['/usr/bin/python3', str(SYSTEM), slug, 'restore', '--transaction', valid_id(ident), '--commit']
        askpass = bool(os.environ.get('SUDO_ASKPASS'))
        command = ['sudo', '-A' if askpass else '-n', '--'] + args
        def associate(worker):
            if receipt is not None and save is not None:
                receipt['system_worker'] = worker
                receipt['system_attempt'] = 'restore-released'
                save(receipt)
        result = transaction_support.run_gated(command, associate)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ShortcutError(detail[-5000:] or 'Command failed: ' + command[0])
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ShortcutError('Command returned no valid receipt; use theme restore to recover.') from exc

    def desktop_receipt(self, ident):
        path = ROOT / 'state' / valid_id(ident) / 'receipt.json'
        if not path.exists():
            return {'status': 'missing'}
        data = self.desktop.E.load(path)
        if data.get('id') != ident or data.get('profile') not in PROFILES:
            raise ShortcutError('Foreign desktop receipt')
        return data

    def desktop_apply(self, slug, ident):
        print('Switching Cinnamon, application colors, Island and Eww…', file=sys.stderr, flush=True)
        _, data = self.desktop.apply(slug, transaction_id=ident)
        return data

    def desktop_check(self, ident):
        data = self.desktop_receipt(ident)
        if data['status'] not in ('pending', 'kept'):
            return {'ok': False, 'status': data['status']}
        bad = self.desktop.check(data)
        return {'ok': not bad, 'mismatches': bad, 'status': data['status']}

    def desktop_keep(self, ident):
        data = self.desktop_receipt(ident)
        if data['status'] != 'pending' or time.time() >= data['deadline']:
            raise ShortcutError('Desktop trial expired before the shortcut could retain it.')
        if self.desktop.check(data):
            raise ShortcutError('Desktop changed before retaining the selection.')
        self.desktop.keep(ROOT / 'state' / ident / 'receipt.json', data)
        return data

    def desktop_restore(self, ident):
        print('Restoring the previous desktop appearance…', file=sys.stderr, flush=True)
        data = self.desktop_receipt(ident)
        path = ROOT / 'state' / ident / 'receipt.json'
        if data['status'] == 'missing':
            return data
        self.desktop.guard.assert_allowed(self.desktop.STUDIO, self.desktop.OWNER, 'rollback', path)
        self.desktop.restore(path, data)
        return data

    def current(self):
        result = {}
        for label, args in {
            'desktop': ['gsettings', 'get', 'org.cinnamon.theme', 'name'],
            'gtk': ['gsettings', 'get', 'org.cinnamon.desktop.interface', 'gtk-theme'],
        }.items():
            call = subprocess.run(args, text=True, capture_output=True, timeout=5)
            result[label] = call.stdout.strip().strip("'") if call.returncode == 0 else 'unavailable'
        call = subprocess.run(['update-alternatives', '--query', 'default.plymouth'], text=True, capture_output=True, timeout=5)
        result['plymouth'] = next((line[7:] for line in call.stdout.splitlines() if line.startswith('Value: ')), 'unavailable')
        try:
            override = Path('/etc/default/grub.d/zz-desktop-theme-studio.cfg').read_text()
            match = re.search(r'^GRUB_THEME=[\"\x27]([^\"\x27]+)[\"\x27]\s*$', override, re.M)
            result['grub_theme_configured'] = match.group(1) if match else 'unavailable'
        except OSError:
            result['grub_theme_configured'] = 'unavailable'
        greeter = Path('/etc/lightdm/slick-greeter.conf')
        try:
            result['greeter'] = {k: v for k, v in re.findall(r'^(background|theme-name|icon-theme-name|cursor-theme-name)\s*=\s*(.*)$', greeter.read_text(), re.M)}
        except OSError:
            result['greeter'] = 'unavailable'
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog='Examples: theme graphite-brass | theme preview slate-orbit-studio | theme restore')
    parser.add_argument('--json', action='store_true', dest='as_json')
    parser.add_argument('words', nargs='*', help='list, status, check, preview NAME, restore, or a theme name')
    args = parser.parse_args(argv)
    words = args.words or ['list']
    if words == ['help']:
        parser.print_help()
        return 0
    action = words[0].casefold()
    console = TerminalConsole.install()
    app = Shortcut(console=console)
    try:
        if action == 'list':
            if len(words) != 1:
                raise ShortcutError('Usage: theme list')
            result = {'themes': [{'id': slug, 'name': row['name']} for slug, row in PROFILES.items()],
                      'usage': 'theme NAME | theme preview NAME | theme status | theme check | theme restore',
                      'scope': f'{len(PROFILES)} Cinnamon presets, including GRUB, Plymouth and Slick Greeter. Restore returns the saved prior appearance.'}
        elif action in ('status', 'check', 'restore', 'undo'):
            if len(words) != 1:
                raise ShortcutError('Unexpected arguments after ' + action)
            result = app.status() if action == 'status' else app.check() if action == 'check' else app.restore()
        else:
            name = ' '.join(words[1:] if action in ('preview', 'apply', 'switch') else words)
            slug = resolve_profile(name)
            if action == 'preview':
                result = app.runtime.preview(slug)
            else:
                if not args.as_json:
                    print('Checking ' + PROFILES[slug]['name'] + ' before switching desktop, GRUB, Plymouth and Slick Greeter.', flush=True)
                    print('Administrator access is needed for boot/login. No reboot or logout is performed.', flush=True)
                result = app.switch(slug)
        if result.get('status') in ('active', 'restored'):
            result['terminal_refresh'] = konsole_runtime.refresh()
        if args.as_json:
            print(json.dumps(result, indent=2))
        elif 'themes' in result:
            for row in result['themes']:
                print(f"{row['id']:<22} {row['name']}")
            print('\n' + result['usage'])
            print(result['scope'])
        elif result.get('status') == 'active':
            print(result['name'] + ' is selected and retained. Desktop and system configuration checks passed.')
            print('Boot/login artwork will be visible at your next boot/login. Undo: theme restore')
        elif result.get('status') in ('restored', 'nothing-to-restore'):
            print('Previous desktop and boot/login appearance restored.' if result['status'] == 'restored' else 'No shortcut change to restore.')
        else:
            print(json.dumps(result, indent=2))
        if not args.as_json and result.get('terminal_refresh', {}).get('status') == 'deferred':
            print('The theme is saved. Konsole tab color refresh was deferred: ' + result['terminal_refresh']['reason'], file=sys.stderr)
        return 1 if result.get('ok') is False else 0
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) or 'Interrupted'
        print(json.dumps({'error': error}) if args.as_json else 'theme: ' + error, file=sys.stderr)
        return 2
    finally:
        console.uninstall()


if __name__ == '__main__':
    raise SystemExit(main())
