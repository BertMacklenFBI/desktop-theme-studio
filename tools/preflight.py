#!/usr/bin/python3 -B
"""Read-only activation preflight for one Studio preset, refinement or collection profile.

    /usr/bin/python3 -B tools/preflight.py ID --stage user|root|catalog [--json]

Exit codes: 0 all clear (warnings allowed), 1 at least one blocking finding,
2 invalid invocation (unknown id, bad arguments).

Nothing here writes, locks for longer than a probe, sets GSettings, or starts
an apply/keep/restore. Checks:

  pending   unresolved transaction receipts. Mirrors ``pending()`` in
            presets/tangerine-graphite/applications/runtime/guard.py (preset
            latest.json + desktop.json/recovery.json, state/activation-trials)
            and extends it to refinements/*/state/latest.json, the collection
            shortcut store, the Nim/Nitch repair store, the legacy ocean-silk
            snapshot receipt and studio.py transactions. Any status outside the
            store's terminal vocabulary is blocking.
  locks     every flock file the Studio and collection use, probed with a
            non-blocking shared then exclusive flock on a read-only descriptor
            (files are never created); holders are named from /proc/locks.
  procs     running Studio/collection controllers, Xephyr, ``cinnamon --replace``.
  plymouth  (root) /etc/plymouth/plymouthd.conf Theme= versus the target's
            Plymouth theme name; SUDO_ASKPASS for the ``theme`` shortcut.
  catalog   (catalog; user for collection profiles) the Nim/Nitch registry
            builder's --check, after statically verifying --check cannot write.
  panels    (user, warning only) design.json panel heights versus the live
            ``org.cinnamon panels-height`` (``gsettings get`` only).

Every filesystem root is injectable (arguments or DTS_* environment variables)
so tests run against temporary trees and a fake /proc.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

sys.dont_write_bytecode = True

STUDIO = Path(__file__).resolve().parents[1]
GUARD_REL = 'presets/tangerine-graphite/applications/runtime/guard.py'
COLLECTION_REL = 'refinements/cinnamon-current-collection'
CATALOG_REL = 'repairs/nim-nitch/catalog/build_theme_registry.py'
SLUG = re.compile(r'[a-z0-9][a-z0-9-]*\Z')

# guard.BUSY at the time of writing; the live module's value is preferred when loadable.
GUARD_BUSY = frozenset({'applying', 'pending', 'restoring', 'recovery-required'})

# Terminal vocabularies, derived from the writers and the receipts on disk (2026-09-23):
#   preset desktop.json (desktop_control.py): applying -> pending -> kept | restoring -> restored | recovery-required
#   refinement receipt.json (live.py, refinements/*/theme.py): same lifecycle, terminal kept/restored
#   activation-trials markers: mirror of the receipt status
#   collection shortcut receipts (shortcut.py FINISHED): active, restored, rolled-back
#   nim-nitch repair receipts (repair_installer.py FINISHED): active, restored (+ rolled-back tolerated)
#   legacy ocean-silk snapshot (nim_transaction_support.legacy_pending): kept, rolled_back
#   studio.py transactions: applied, restored, rolled-back
TERMINAL = {
    'preset': frozenset({'kept', 'restored'}),
    'refinement': frozenset({'kept', 'restored'}),
    'trial': frozenset({'kept', 'restored'}),
    'shortcut': frozenset({'active', 'restored', 'rolled-back'}),
    'nim-repair': frozenset({'active', 'restored', 'rolled-back'}),
    'legacy-snapshot': frozenset({'kept', 'rolled_back'}),
    'studio-transaction': frozenset({'applied', 'restored', 'rolled-back'}),
}

PROC_NAMES = frozenset({'theme.sh', 'theme.py', 'shortcut.py', 'isolate.py', 'collection.py',
                        'integrate_candidates.py', 'stage_catalog.py',
                        'repair_installer.py', 'publish_sources.py', 'desktop_control.py'})
# Generic basenames only counted when the path identifies the Studio component.
PROC_QUALIFIED = {'system.py': ('system/system.py', 'system-stage/system.py'),
                  'live.py': ('cinnamon-current-collection/live.py',)}
WRAPPERS = frozenset({'sudo', 'env', 'nice', 'ionice', 'timeout', 'setsid', 'pkexec', 'nohup',
                      'systemd-run', 'stdbuf', 'chrt', 'taskset', 'flock'})
INTERPRETER = re.compile(r'(python[0-9.]*|bash|sh|dash|zsh)\Z')

LEVELS = ('block', 'warn', 'ok', 'info', 'skip')


@dataclasses.dataclass
class Context:
    studio: Path = STUDIO
    home: Path = Path.home()
    proc: Path = Path('/proc')
    plymouthd_conf: Path = Path('/etc/plymouth/plymouthd.conf')
    plymouth_themes_dir: Path = Path('/usr/share/plymouth/themes')
    system_state: Path = Path('/var/lib/desktop-theme-studio')
    env: dict = dataclasses.field(default_factory=lambda: dict(os.environ))
    self_pid: int = dataclasses.field(default_factory=os.getpid)
    gsettings: Callable[[str, str], str] | None = None
    run_catalog: Callable[[Path], tuple[int, str, str]] | None = None

    @property
    def state_home(self) -> Path:
        raw = self.env.get('XDG_STATE_HOME')
        return Path(raw) if raw else self.home / '.local/state'


def finding(check, level, message, **detail):
    assert level in LEVELS
    return {'check': check, 'level': level, 'message': message, **({'detail': detail} if detail else {})}


def read_json(path):
    return json.loads(Path(path).read_text())


# --------------------------------------------------------------------- target

@dataclasses.dataclass
class Target:
    ident: str
    kinds: list
    primary: str

    @property
    def collection(self):
        return 'collection' in self.kinds


def collection_profiles(studio):
    path = Path(studio) / COLLECTION_REL / 'profiles.json'
    try:
        return read_json(path).get('profiles', {})
    except (OSError, ValueError):
        return {}


def resolve_target(studio, ident, kind=None):
    if not SLUG.fullmatch(ident or ''):
        raise ValueError(f'invalid id {ident!r}; expected a lowercase slug')
    studio = Path(studio)
    kinds = []
    if (studio / 'presets' / ident).is_dir() and not ident.startswith('_'):
        kinds.append('preset')
    if (studio / 'refinements' / ident).is_dir():
        kinds.append('refinement')
    if ident in collection_profiles(studio):
        kinds.append('collection')
    if not kinds:
        raise ValueError(f'unknown id {ident!r}: not a preset, refinement or collection profile')
    if kind and kind not in kinds:
        raise ValueError(f'{ident!r} is not a {kind} (found: {", ".join(kinds)})')
    return Target(ident, kinds, kind or kinds[0])


# -------------------------------------------------------------------- pending

def guard_busy(studio):
    """guard.BUSY from the live guard module (it has no import-time side effects)."""
    path = Path(studio) / GUARD_REL
    try:
        spec = importlib.util.spec_from_file_location('_dts_guard_readonly', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return frozenset(module.BUSY)
    except Exception:
        return GUARD_BUSY


def _receipt_row(store, path, status, busy, **extra):
    terminal = TERMINAL[store]
    if status in terminal:
        state = 'terminal'
    elif status in busy:
        state = 'busy'
    else:
        state = 'unknown'
    return {'store': store, 'path': str(path), 'status': status, 'state': state, **extra}


def collect_receipts(ctx):
    """Return (rows, errors): one row per *current* receipt examined."""
    studio = Path(ctx.studio)
    busy = guard_busy(studio)
    rows, errors = [], []

    def load(path):
        try:
            data = read_json(path)
            if not isinstance(data, dict):
                raise ValueError('not a JSON object')
            return data
        except (OSError, ValueError) as exc:
            errors.append({'path': str(path), 'error': str(exc)})
            return None

    # 1. guard.pending(): presets/*/state/latest.json -> <id>/desktop.json (+ recovery.json)
    for latest in sorted(studio.glob('presets/*/state/latest.json')):
        data = load(latest)
        if data is None:
            continue
        ident = data.get('id')
        if not isinstance(ident, str) or Path(ident).name != ident:
            errors.append({'path': str(latest), 'error': 'invalid latest receipt identity'})
            continue
        receipt = latest.parent / ident / 'desktop.json'
        state = load(receipt)
        if state is None:
            continue
        rows.append(_receipt_row('preset', receipt, state.get('status'), busy, owner=latest.parents[1].name))
        recovery = receipt.with_name('recovery.json')
        if recovery.exists():
            rec = load(recovery)
            if rec is not None and rec.get('status') == 'recovery-required':
                rows.append(_receipt_row('preset', recovery, 'recovery-required', busy, owner=latest.parents[1].name))
    # guard.pending() supplement: state/activation-trials/*.json
    for marker in sorted(studio.glob('state/activation-trials/*.json')):
        data = load(marker)
        if data is not None:
            rows.append(_receipt_row('trial', marker, data.get('status'), busy, owner=data.get('preset'),
                                     receipt=data.get('receipt')))

    # 2. refinements/*/state/latest.json: {"receipt": ABS} (collection, evergreen, ocean-silk) or {"id": ...}
    for latest in sorted(studio.glob('refinements/*/state/latest.json')):
        data = load(latest)
        if data is None:
            continue
        owner = latest.parents[1].name
        if isinstance(data.get('receipt'), str):
            receipt = Path(data['receipt'])
            if not receipt.is_absolute():
                receipt = latest.parent / receipt
        elif isinstance(data.get('id'), str) and Path(data['id']).name == data['id']:
            receipt = latest.parent / data['id'] / 'receipt.json'
        else:
            errors.append({'path': str(latest), 'error': 'latest.json names no receipt'})
            continue
        state = load(receipt)
        if state is None:
            continue
        rows.append(_receipt_row('refinement', receipt, state.get('status'), busy, owner=owner))
        recovery = receipt.with_name('recovery.json')
        if recovery.exists():
            rec = load(recovery)
            if rec is not None and rec.get('status') == 'recovery-required':
                rows.append(_receipt_row('refinement', recovery, 'recovery-required', busy, owner=owner))

    # 3. collection `theme` shortcut store: every receipt must be finished (shortcut.py / paired_records)
    for receipt in sorted((studio / COLLECTION_REL / 'state/shortcuts').glob('*/receipt.json')):
        data = load(receipt)
        if data is not None:
            rows.append(_receipt_row('shortcut', receipt, data.get('status'), busy | {'prepared', 'switching'},
                                     owner=data.get('profile')))
    # 4. Nim/Nitch repair installer store
    for receipt in sorted((studio / 'state/nim-nitch-repair').glob('*/receipt.json')):
        data = load(receipt)
        if data is not None:
            rows.append(_receipt_row('nim-repair', receipt, data.get('status'),
                                     busy | {'prepared', 'installing'}, owner='nim-nitch'))
    # 5. legacy ocean-silk snapshot controller (shared with the shortcut's legacy lock)
    legacy = ctx.state_home / 'ocean-silk/snapshots/transaction.json'
    if legacy.exists():
        data = load(legacy)
        if data is not None:
            rows.append(_receipt_row('legacy-snapshot', legacy, data.get('status'),
                                     frozenset({'applying', 'pending', 'rollback_failed'}), owner=data.get('target')))
    # 6. studio.py generic transactions
    for journal in sorted((studio / 'state/transactions').glob('*/transaction.json')):
        data = load(journal)
        if data is not None:
            rows.append(_receipt_row('studio-transaction', journal, data.get('status'), busy, owner=data.get('name')))
    return rows, errors


def check_pending(ctx, target, stage):
    rows, errors = collect_receipts(ctx)
    out = []
    for row in rows:
        if row['state'] == 'terminal':
            continue
        why = 'unresolved transaction' if row['state'] == 'busy' else 'non-terminal/unknown status'
        out.append(finding('pending', 'block', f"{why}: {row['store']} {row.get('owner') or ''} status={row['status']!r}".replace('  ', ' '),
                           path=row['path']))
    for error in errors:
        out.append(finding('pending', 'block', 'unreadable receipt needs inspection', **error))
    if not out:
        vocab = sorted({r['status'] for r in rows if r['status'] is not None})
        out.append(finding('pending', 'ok', f'{len(rows)} receipts examined, all terminal ({", ".join(vocab)})'))
    return out


# ---------------------------------------------------------------------- locks

def lock_candidates(ctx):
    """Every flock path found in the controllers (see module docstring)."""
    studio = Path(ctx.studio)
    paths = [
        (studio / 'state/activation.lock', 'guard.locked / nim studio_lock (theme.py, live.py, shortcut.py, repair_installer.py)'),
        (studio / 'state/lock', 'studio.py locked()'),
        (studio / COLLECTION_REL / 'state/shortcuts/switch.lock', 'shortcut.py transaction_lock'),
        (studio / 'state/nim-nitch-repair/install.lock', 'repair_installer.py install lock'),
        (ctx.state_home / 'ocean-silk/snapshots/.lock', 'legacy ocean-silk snapshots engine.lock'),
        (Path(ctx.system_state) / 'system.lock', 'root system.py stages (preset + collection system-stage)'),
    ]
    for path in sorted(studio.glob('presets/*/state/.lock')):
        paths.append((path, 'desktop_control.py locked()'))
    # Incremental collection builds (repairs/incremental-build-20260923): build-cache/ for generated/,
    # build-cache-<name>/ for other output roots such as preview-generated/.
    collection = studio / COLLECTION_REL
    for path in sorted(collection.glob('build-cache*/.lock')) + sorted(collection.glob('contributions/*/build-cache*/.lock')):
        paths.append((path, 'collection.py / integrate_candidates.py build lock'))
    return paths


def proc_locks(ctx):
    """Map (major, minor, inode) -> [(pid, type, mode)] from /proc/locks."""
    table = {}
    try:
        text = (Path(ctx.proc) / 'locks').read_text()
    except OSError:
        return None
    for line in text.splitlines():
        parts = [p for p in line.split() if p != '->']
        if len(parts) < 6:
            continue
        try:
            major, minor, inode = parts[5].split(':')
            key = (int(major, 16), int(minor, 16), int(inode))
            table.setdefault(key, []).append((int(parts[4]), parts[1], parts[3]))
        except ValueError:
            continue
    return table


def probe_lock(path):
    """Return 'free' | 'held-exclusive' | 'held-shared' | 'absent' | 'unreadable:<err>'. Never creates."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'absent'
    except OSError as exc:
        return 'unreadable:' + (exc.strerror or type(exc).__name__)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'held-exclusive'
        fcntl.flock(fd, fcntl.LOCK_UN)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'held-shared'
        fcntl.flock(fd, fcntl.LOCK_UN)
        return 'free'
    finally:
        os.close(fd)


def check_locks(ctx, target, stage):
    table = proc_locks(ctx)
    out, free = [], []
    for path, owner in lock_candidates(ctx):
        holders = []
        if table is not None:
            try:
                st = os.stat(path, follow_symlinks=False)
                holders = [h for h in table.get((os.major(st.st_dev), os.minor(st.st_dev), st.st_ino), [])
                           if h[0] != ctx.self_pid]
            except OSError:
                pass
        state = probe_lock(path)
        if state == 'absent':
            free.append(f'{path} (absent)')
            continue
        if state.startswith('held') or holders:
            out.append(finding('locks', 'block', f'lock held: {path}', owner=owner, probe=state,
                               holders=[{'pid': p, 'type': t, 'mode': m} for p, t, m in holders]))
        elif state.startswith('unreadable'):
            out.append(finding('locks', 'warn', f'cannot probe {path} ({state.split(":", 1)[1]}); /proc/locks shows no holder',
                               owner=owner))
        else:
            free.append(str(path))
    if not any(f['level'] == 'block' for f in out):
        out.append(finding('locks', 'ok', f'{len(free)} lock files free or absent', paths=free))
    return out


# ------------------------------------------------------------------ processes

SESSION_SHELL_PARENTS = frozenset({'cinnamon-launcher'})


def classify_cmdline(argv, parent_argv=None):
    """Return a reason string when argv is an in-flight Studio/desktop mutation, else None.

    ``cinnamon --replace`` is also how cinnamon-launcher starts (and respawns) the session's
    own shell, so it only counts when its parent is not cinnamon-launcher.
    """
    if not argv:
        return None
    first = os.path.basename(argv[0])
    if first == 'Xephyr' or first.startswith('Xephyr'):
        return 'Xephyr nested display (isolated preview)'
    if first == 'cinnamon' and '--replace' in argv:
        parent = os.path.basename(parent_argv[0]) if parent_argv else ''
        return None if parent in SESSION_SHELL_PARENTS else 'cinnamon --replace (not the session launcher)'
    tokens = list(argv)
    # Strip wrappers (sudo env nice ...) with their flags and VAR=value assignments.
    while tokens:
        base = os.path.basename(tokens[0])
        if base in WRAPPERS:
            tokens.pop(0)
            while tokens and (tokens[0].startswith('-') or ('=' in tokens[0] and '/' not in tokens[0].split('=')[0])):
                tokens.pop(0)
            continue
        break
    if not tokens:
        return None
    candidates = [tokens[0]]
    if INTERPRETER.fullmatch(os.path.basename(tokens[0])):
        rest = tokens[1:]
        while rest and rest[0].startswith('-'):
            flag = rest.pop(0)
            if flag in ('-c', '-m'):
                return None
        if rest:
            candidates = [rest[0]]
    for token in candidates:
        base = os.path.basename(token)
        if base in PROC_NAMES:
            return base
        if base == 'theme' and ('/' not in token or token.endswith('.local/bin/theme')):
            return 'theme shortcut'
        if base in PROC_QUALIFIED and any(('/' + token).endswith('/' + s) for s in PROC_QUALIFIED[base]):
            return base
    return None


def _argv(proc, pid):
    try:
        raw = (Path(proc) / str(pid) / 'cmdline').read_bytes()
    except OSError:
        return []
    return [a.decode(errors='replace') for a in raw.split(b'\0') if a]


def _ppid(proc, pid):
    try:
        stat = (Path(proc) / str(pid) / 'stat').read_text()
        return int(stat[stat.rindex(')') + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def scan_processes(ctx):
    """Return (blocking rows, informational rows) or None when the proc root is unreadable."""
    found, benign = [], []
    try:
        entries = list(os.scandir(ctx.proc))
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == ctx.self_pid:
            continue
        pid = int(entry.name)
        argv = _argv(ctx.proc, pid)
        if not argv:
            continue
        parent_argv = None
        if os.path.basename(argv[0]) == 'cinnamon':
            ppid = _ppid(ctx.proc, pid)
            parent_argv = _argv(ctx.proc, ppid) if ppid else None
        reason = classify_cmdline(argv, parent_argv)
        row = {'pid': pid, 'cmdline': ' '.join(argv)[:200]}
        if reason:
            found.append(dict(row, reason=reason))
        elif parent_argv is not None and '--replace' in argv:
            benign.append(dict(row, parent=' '.join(parent_argv)[:80]))
    return sorted(found, key=lambda r: r['pid']), benign


def check_processes(ctx, target, stage):
    scanned = scan_processes(ctx)
    if scanned is None:
        return [finding('procs', 'block', f'cannot read {ctx.proc}; process scan impossible')]
    found, benign = scanned
    out = [finding('procs', 'block', f"running: {r['reason']} (pid {r['pid']})", cmdline=r['cmdline']) for r in found]
    if not found:
        out.append(finding('procs', 'ok', 'no Studio/collection controller, Xephyr or stray cinnamon --replace running'))
    out += [finding('procs', 'info', f"session shell pid {r['pid']} ({r['cmdline']}) started by {r['parent']}; ignored")
            for r in benign]
    return out


# ------------------------------------------------------------------- plymouth

def plymouthd_theme(path):
    """Theme= under [Daemon]; returns (value|None, error|None)."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, str(exc)
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip()
            continue
        if section == 'Daemon' and '=' in line:
            key, value = line.split('=', 1)
            if key.strip() == 'Theme':
                return value.strip() or None, None
    return None, None


def preset_plymouth_name(folder):
    """Plymouth theme dir name staged by presets/<id>/system/ (or refinements/<id>/system/)."""
    base = Path(folder) / 'system/plymouth'
    if not base.is_dir():
        return None
    theme, _ = plymouthd_theme(base / 'plymouthd.conf')
    if theme:
        return theme
    for pattern, strip in (('*.plymouth', '.plymouth'), ('*.script', '.script'), ('*.script.in', '.script.in')):
        names = sorted(p.name[:-len(strip)] for p in base.glob(pattern) if p.name != 'theme.plymouth.in')
        if names:
            return names[0]
    return None


def collection_plymouth_name(studio, ident):
    path = Path(studio) / COLLECTION_REL / 'runtime-quality/system-stage/profiles' / f'{ident}.json'
    try:
        stage_id = read_json(path).get('stage_id')
    except (OSError, ValueError):
        return None
    return stage_id if isinstance(stage_id, str) else None


def target_plymouth(ctx, target, kind):
    if kind == 'collection':
        return collection_plymouth_name(ctx.studio, target.ident)
    return preset_plymouth_name(Path(ctx.studio) / ('presets' if kind == 'preset' else 'refinements') / target.ident)


def check_plymouth(ctx, target, stage):
    out = []
    live, error = plymouthd_theme(ctx.plymouthd_conf)
    if error:
        return [finding('plymouth', 'warn', f'cannot read {ctx.plymouthd_conf}: {error}')]
    if not live:
        return [finding('plymouth', 'ok', f'{ctx.plymouthd_conf} sets no Theme=; the default.plymouth alternative applies')]
    owner = "Tangerine Graphite's" if live == 'tangerine-graphite' else f'the Theme={live}'
    restore = (f"{owner} {ctx.plymouthd_conf} (Theme={live}) must be restored first; it overrides "
               'every other preset/collection Plymouth selection')
    wanted = target_plymouth(ctx, target, target.primary)
    if wanted is None:
        out.append(finding('plymouth', 'warn', f'{target.primary} {target.ident} stages no Plymouth theme; '
                           f'Theme={live} from plymouthd.conf stays in effect'))
    elif wanted != live:
        out.append(finding('plymouth', 'block', restore, live=live, target=wanted))
    else:
        out.append(finding('plymouth', 'ok', f'plymouthd.conf Theme={live} matches the target', target=wanted))
    if target.collection and target.primary != 'collection':
        via = collection_plymouth_name(ctx.studio, target.ident)
        if via and via != live:
            out.append(finding('plymouth', 'warn', f'via the `theme` shortcut the collection Plymouth {via} would be '
                               f'overridden: {restore}', live=live, target=via))
    return out


def check_askpass(ctx, target, stage):
    if not target.collection:
        return [finding('askpass', 'skip', 'not a collection profile; `theme` shortcut not involved')]
    raw = ctx.env.get('SUDO_ASKPASS')
    # Only a warning when the shortcut is the chosen path (--kind collection or collection-only ids).
    level = 'warn' if target.primary == 'collection' else 'info'
    if not raw:
        # Since the sudo-ticket fix (repairs/sudo-ticket-20260923) root workers keep the terminal session,
        # and `theme` probes sudo before changing anything, so plain `theme NAME` works.
        return [finding('askpass', 'info', 'SUDO_ASKPASS is unset: fine; plain `theme NAME` uses the terminal '
                        'sudo ticket and stops with "Nothing was changed" if sudo would fail')]
    if not (os.path.isfile(raw) and os.access(raw, os.X_OK)):
        return [finding('askpass', level, f'SUDO_ASKPASS={raw} is not an executable file')]
    return [finding('askpass', 'ok', f'SUDO_ASKPASS={raw}')]


# -------------------------------------------------------------------- catalog

WRITE_METHODS = frozenset({'write', 'write_text', 'write_bytes', 'touch', 'mkdir', 'unlink', 'rmdir',
                           'symlink_to', 'hardlink_to', 'rename', 'chmod', 'truncate', 'writelines'})
WRITE_MODULES = frozenset({'shutil', 'tempfile', 'subprocess'})
WRITE_OS = frozenset({'replace', 'rename', 'renames', 'unlink', 'remove', 'removedirs', 'mkdir', 'makedirs',
                      'chmod', 'chown', 'fdopen', 'rmdir', 'symlink', 'link', 'system', 'write', 'truncate',
                      'open', 'mkfifo', 'utime', 'popen', 'spawnv', 'execv'})


def _write_call(node):
    """True when an ast.Call can modify the filesystem or spawn a process."""
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == 'open':
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == 'mode'), None)
            return not (mode is None or (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                                         and not set(mode.value) & set('wax+')))
        return False
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id in WRITE_MODULES:
            return True
        if isinstance(func.value, ast.Name) and func.value.id == 'os':
            return func.attr in WRITE_OS
        if func.attr == 'open':
            mode = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'mode'), None)
            return not (mode is None or (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                                         and not set(mode.value) & set('wax+')))
        return func.attr in WRITE_METHODS
    return False


def verify_check_is_readonly(source):
    """Static proof that ``--check`` exists and cannot write. Returns (ok, reason).

    Accepted shape (build_theme_registry.py as of 2026-09-23): main() declares --check;
    every filesystem-writing call in the module lives either inside a writer helper
    function that is called only from the ``else`` of ``if args.check:``, or inside
    that ``else`` itself.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f'cannot parse builder: {exc}'
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    main = functions.get('main')
    if main is None:
        return False, 'builder has no main()'
    declares = any(isinstance(n, ast.Call) and getattr(n.func, 'attr', '') == 'add_argument'
                   and any(isinstance(a, ast.Constant) and a.value == '--check' for a in n.args)
                   for n in ast.walk(main))
    if not declares:
        return False, 'builder does not declare --check'
    branch = next((n for n in ast.walk(main) if isinstance(n, ast.If) and isinstance(n.test, ast.Attribute)
                   and n.test.attr == 'check'), None)
    if branch is None:
        return False, 'no `if args.check:` branch found'
    write_only = set()  # ids of nodes inside the non-check (else) branch
    for stmt in branch.orelse:
        write_only |= {id(n) for n in ast.walk(stmt)}
    writers = {name for name, fn in functions.items() if name != 'main'
               and any(isinstance(n, ast.Call) and _write_call(n) for n in ast.walk(fn))}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        in_writer = any(node in ast.walk(functions[w]) for w in writers)
        callee = getattr(node.func, 'id', None)
        if callee in writers and id(node) not in write_only:
            return False, f'writer {callee}() reachable outside the non-check branch'
        if _write_call(node) and not in_writer and id(node) not in write_only:
            return False, f'write-capable call at line {node.lineno} outside the non-check branch'
    if any(isinstance(n, (ast.Import, ast.ImportFrom)) and any(a.name.split('.')[0] == 'subprocess' for a in n.names)
           for n in ast.walk(tree)):
        return False, 'builder imports subprocess'
    return True, ('main() declares --check; writes (' + ', '.join(sorted(writers)) +
                  ') are reachable only from the non-check branch')


def default_run_catalog(script):
    proc = subprocess.run(['/usr/bin/python3', '-B', str(script), '--check'], capture_output=True, text=True,
                          timeout=120, cwd=str(Path(script).parent), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    return proc.returncode, proc.stdout, proc.stderr


def check_catalog(ctx, target, stage):
    script = Path(ctx.studio) / CATALOG_REL
    try:
        source = script.read_text()
    except OSError as exc:
        return [finding('catalog', 'warn', f'registry builder unreadable, catalog check skipped: {exc}')]
    ok, reason = verify_check_is_readonly(source)
    if not ok:
        return [finding('catalog', 'warn', f'catalog check skipped: {reason}', script=str(script))]
    out = []
    try:
        tree = ast.parse(source)
        consts = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name) and t.id in ('EXPECTED_IDS', 'CURRENT_IDS')}
        expected = set(ast.literal_eval(consts['EXPECTED_IDS'].args[0])) if 'EXPECTED_IDS' in consts else None
        current = set(ast.literal_eval(consts['CURRENT_IDS'].args[0])) if 'CURRENT_IDS' in consts else None
        if expected is not None and target.ident not in expected:
            out.append(finding('catalog', 'warn', f'{target.ident} is not in the catalog EXPECTED_IDS'))
        if target.collection and current is not None and target.ident not in current:
            out.append(finding('catalog', 'warn', f'{target.ident} is a collection profile but not in CURRENT_IDS'))
    except Exception:
        pass
    runner = ctx.run_catalog or default_run_catalog
    try:
        code, stdout, stderr = runner(script)
    except Exception as exc:  # timeout or spawn failure
        return out + [finding('catalog', 'block', f'catalog --check could not run: {exc}')]
    if code == 0:
        try:
            summary = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            summary = {'stdout': stdout.strip()[:300]}
        out.append(finding('catalog', 'ok', 'registry pins current (build_theme_registry.py --check)', **summary))
    else:
        out.append(finding('catalog', 'block', 'catalog stale or invalid: ' + (stderr.strip() or stdout.strip())[:400],
                           exit=code))
    return out


# --------------------------------------------------------------------- panels

def default_gsettings(schema, key):
    proc = subprocess.run(['gsettings', 'get', schema, key], capture_output=True, text=True, timeout=10)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or 'gsettings get failed')
    return proc.stdout.strip()


def gvariant_strings(text):
    text = text.strip()
    if text.startswith('@as '):
        text = text[4:]
    value = ast.literal_eval(text)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f'expected a string list: {text}')
    return value


def collection_design_panels(ctx, target):
    """{'left'|'right': px, ...} from profiles.json panel_layout (DESIGN.md section 4/1), or
    (None, note) for a collection profile with no panel_layout (the pre-existing behavior: the
    collection preserves whatever panel layout is live)."""
    profile = collection_profiles(ctx.studio).get(target.ident, {})
    layout = profile.get('panel_layout') if isinstance(profile, dict) else None
    panel = layout.get('panel') if isinstance(layout, dict) else None
    if not isinstance(panel, dict):
        return None, 'collection profiles preserve the live panel layout'
    try:
        heights = dict(item.split(':', 1) for item in panel.get('panels_height') or [])
        out = {}
        for item in panel.get('panels_enabled') or []:
            pid, _monitor, position = item.split(':', 2)
            if pid in heights:
                out[position] = int(heights[pid])
    except (ValueError, TypeError):
        return None, 'profiles.json panel_layout is malformed'
    return (out or None), (None if out else 'profiles.json panel_layout declares no panel heights')


def design_panels(ctx, target):
    """Return ({'top'/'bottom'/'left'/'right': px, ...}, note)."""
    if target.primary == 'collection':
        return collection_design_panels(ctx, target)
    folder = Path(ctx.studio) / ('refinements' if target.primary == 'refinement' else 'presets') / target.ident
    path = folder / 'design.json'
    if not path.exists():
        return None, f'no {path}'
    try:
        geometry = read_json(path).get('geometry', {})
    except (OSError, ValueError) as exc:
        return None, f'unreadable design.json: {exc}'
    if not isinstance(geometry, dict):
        return None, 'design.json geometry is not an object'
    if geometry.get('preserve_panel_allocations') or geometry.get('preserve_panel_layout'):
        return None, 'design.json preserves the live panel layout'
    heights = {}
    for key, position in (('panel_height', 'top'), ('top_panel_px', 'top'), ('bottom_panel_height', 'bottom')):
        if isinstance(geometry.get(key), int):
            heights.setdefault(position, geometry[key])
    return (heights or None), (None if heights else 'design.json declares no panel heights')


def check_panels(ctx, target, stage):
    wanted, note = design_panels(ctx, target)
    if not wanted:
        return [finding('panels', 'skip', note)]
    get = ctx.gsettings or default_gsettings
    try:
        heights = dict(item.split(':', 1) for item in gvariant_strings(get('org.cinnamon', 'panels-height')))
        positions = {}
        for item in gvariant_strings(get('org.cinnamon', 'panels-enabled')):
            pid, _monitor, pos = item.split(':', 2)
            positions.setdefault(pos, pid)
    except Exception as exc:
        return [finding('panels', 'warn', f'live panel geometry unavailable: {exc}')]
    out = []
    for position, px in sorted(wanted.items()):
        pid = positions.get(position)
        live = heights.get(pid) if pid else None
        if live is None:
            out.append(finding('panels', 'warn', f'design has a {position} panel ({px}px) but none is enabled live'))
        elif int(live) != px:
            out.append(finding('panels', 'warn', f'{position} panel drift: design {px}px, live {live}px (panel {pid})'))
    if not out:
        out.append(finding('panels', 'ok', 'live panel heights match design.json',
                           **{k: f'{v}px' for k, v in sorted(wanted.items())}))
    return out


def collection_panel_state(ctx):
    """('home'/'rail'/'unknown', owning profile ident or None) from state/panel-epoch.json,
    ordered by panel_seq exactly like publication A's publish_sources.py restore_preconditions /
    panel_layout.derive_state (API v1.2: by panel_seq, never by folder name). Fails CLOSED
    (coordinator review item 8, 2026-09-27): an unreadable pointer/receipt, a missing or
    non-int panel_seq, or an unresolved receipt status returns 'unknown', never 'home' --
    check_panel_owner then blocks rather than assuming it is safe to proceed."""
    state_dir = Path(ctx.studio) / COLLECTION_REL / 'state'
    if not state_dir.is_dir():
        return 'home', None
    panel_receipts = {}
    for path in sorted(state_dir.glob('*/receipt.json')):
        try:
            raw = path.read_bytes()
        except OSError:
            return 'unknown', None
        if b'"panel":' not in raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            return 'unknown', None
        if not isinstance(data, dict) or not isinstance(data.get('panel'), dict):
            continue
        panel_receipts[path] = data
    seqs = {}
    for path, data in panel_receipts.items():
        seq = data['panel'].get('panel_seq')
        if type(seq) is not int:
            return 'unknown', None
        seqs[path] = seq
    pointer_path = state_dir / 'panel-epoch.json'
    candidate, pointer, pointer_seq = None, None, None
    if pointer_path.is_file():
        try:
            value = read_json(pointer_path)
            pointer = Path(value['receipt'])
            pointer_seq = value.get('panel_seq')
            if pointer.name != 'receipt.json' or pointer.parent.parent != state_dir:
                return 'unknown', None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return 'unknown', None
        if pointer not in panel_receipts:
            return 'unknown', None
        if pointer_seq is not None and seqs.get(pointer) != pointer_seq:
            return 'unknown', None
        candidate, pointer_seq = pointer, seqs.get(pointer)
    above = [(seq, path) for path, seq in seqs.items()
             if (pointer_seq is None or seq > pointer_seq) and panel_receipts[path].get('status') != 'restored']
    if above:
        candidate = max(above)[1]
    if candidate is None:
        return 'home', None
    data = panel_receipts[candidate]
    panel, status = data['panel'], data.get('status')
    if status == 'kept':
        state = panel.get('state_after')
    elif status == 'restored':
        state = panel.get('state_before')
    else:
        return 'unknown', None
    if state not in ('home', 'rail'):
        return 'unknown', None
    profile = (panel.get('rail') or {}).get('profile') or panel.get('rail_profile') or panel.get('profile')
    return state, profile


def any_preset_rail_kept(ctx):
    """(True, owning ident, unreadable) for the first presets/<ident> (scanned in sorted order)
    whose state/latest.json's receipt is status kept with a live panel.rail section; otherwise
    (False, None, unreadable), where `unreadable` lists any preset whose own state could not be
    read (reported as a warning below, never silently treated as safe). Scans EVERY preset, not
    only the collection target (coordinator review item 1, 2026-09-27: `theme <profile>` keeps
    whatever panel is live -- DESIGN.md Finding #2 -- so ANY kept preset rail conflicts with
    switching to ANY collection profile; `theme slate-orbit` used to layer over an
    indigo-lunchbox-only-scoped check)."""
    presets_dir = Path(ctx.studio) / 'presets'
    unreadable = []
    if not presets_dir.is_dir():
        return False, None, unreadable
    for preset_dir in sorted(p for p in presets_dir.iterdir() if p.is_dir() and not p.name.startswith('_')):
        latest = preset_dir / 'state' / 'latest.json'
        if not latest.is_file():
            continue
        try:
            latest_data = read_json(latest)
            receipt_id = latest_data.get('id') if isinstance(latest_data, dict) else None
            receipt = read_json(preset_dir / 'state' / receipt_id / 'desktop.json') if receipt_id else None
        except (OSError, ValueError, TypeError):
            unreadable.append(preset_dir.name)
            continue
        if (isinstance(receipt, dict) and receipt.get('status') == 'kept'
                and isinstance(receipt.get('panel'), dict) and receipt['panel'].get('rail')):
            return True, preset_dir.name, unreadable
    return False, None, unreadable


def plymouth_theme_installed(ctx, ident):
    """True when <plymouth_themes_dir>/<ident> exists: the preset root stage's own marker that
    it is still installed (coordinator review item 3, 2026-09-27; DESIGN.md section 7 handover
    order step 2, preset root restore, must run before `theme <profile>` for that ident)."""
    return (Path(ctx.plymouth_themes_dir) / ident).exists()


def collection_profile_is_rail(ctx, ident):
    """True only when profiles.json's own row for `ident` carries a panel_layout (a rail
    profile; only indigo-lunchbox today)."""
    profile = collection_profiles(ctx.studio).get(ident)
    return isinstance(profile, dict) and isinstance(profile.get('panel_layout'), dict)


def check_panel_owner(ctx, target, stage):
    """BLOCKING (DESIGN.md section 4, amended by coordinator review items 1/3/8/R1, 2026-09-27):
    refuse `theme <profile>` (any collection profile) while ANY preset's own rail stage is kept
    and live, or -- only when the TARGET profile is itself a rail profile (has a profiles.json
    panel_layout; coordinator review item R1: the Plymouth check regressed every other current
    profile, since a normal preset's own root stage stays installed permanently, e.g.
    /usr/share/plymouth/themes/tangerine-graphite) -- while that rail profile's own preset root
    stage is still installed (its Plymouth theme directory exists); refuse the preset's own apply
    (theme.sh apply -> --stage user primary=preset; system.py apply --commit -> --stage root)
    while the collection panel state is rail and owned by this same profile, or is unknown (fails
    CLOSED: an undeterminable collection panel state blocks rather than assumes home).
    hook_coordinator.py only ever calls preflight before an apply (theme.sh apply, system.py
    apply --commit, theme <profile>), never around keep/restore, so this never blocks either of
    those."""
    out = []
    if target.primary == 'collection':
        kept, owner, unreadable = any_preset_rail_kept(ctx)
        if kept:
            out.append(finding('panel-owner', 'block',
                f'presets/{owner} already owns a kept, live panel rail; restore it '
                f'(theme.sh restore) before theme {target.ident}'))
        for name in unreadable:
            out.append(finding('panel-owner', 'warn',
                f'presets/{name} state is unreadable; panel ownership could not be checked there'))
        if collection_profile_is_rail(ctx, target.ident) and plymouth_theme_installed(ctx, target.ident):
            out.append(finding('panel-owner', 'block',
                f'{ctx.plymouth_themes_dir}/{target.ident} still exists; the preset root stage is '
                f'still installed. Restore it (system.py restore --commit) before theme {target.ident}'))
    else:
        state, profile = collection_panel_state(ctx)
        if state == 'unknown':
            out.append(finding('panel-owner', 'block',
                'the collection panel state cannot be determined (failing closed); reconcile '
                f'{COLLECTION_REL}/state/panel-epoch.json before the {target.ident} preset apply'))
        elif state == 'rail' and profile == target.ident:
            out.append(finding('panel-owner', 'block',
                f'the collection panel state is rail (owned by {profile}); switch to a normal '
                f'profile (theme <normal-profile>) before the {target.ident} preset apply'))
    if not out:
        out.append(finding('panel-owner', 'ok', 'no double panel ownership detected'))
    return out


# ---------------------------------------------------------------------- driver

def plan(target, stage):
    checks = [check_pending, check_locks, check_processes]
    if stage == 'root':
        checks += [check_plymouth, check_askpass, check_panel_owner]
    if stage == 'user':
        if target.collection:
            checks += [check_askpass, check_catalog]
        checks += [check_panels, check_panel_owner]
    if stage == 'catalog':
        checks.append(check_catalog)
    return checks


def run(ctx, target, stage):
    results = []
    for check in plan(target, stage):
        try:
            results += check(ctx, target, stage)
        except Exception as exc:  # a check that cannot complete is never "clear"
            name = check.__name__.replace('check_', '')
            results.append(finding(name, 'block', f'check failed: {type(exc).__name__}: {exc}'))
    return results


def render(report):
    rows = [(f['check'], f['level'].upper(), f['message']) for f in report['findings']]
    widths = [max(len(r[i]) for r in rows + [('CHECK', 'STATUS', '')]) for i in range(2)]
    lines = [f"preflight {report['id']} --stage {report['stage']}  (target: {', '.join(report['kinds'])}; "
             f"primary {report['primary']})", '',
             f"{'CHECK':<{widths[0]}}  {'STATUS':<{widths[1]}}  DETAIL",
             f"{'-' * widths[0]}  {'-' * widths[1]}  {'-' * 6}"]
    for check, level, message in rows:
        lines.append(f'{check:<{widths[0]}}  {level:<{widths[1]}}  {message}')
    for f in report['findings']:
        if f['level'] in ('block', 'warn') and f.get('detail'):
            lines.append(f"  [{f['check']}] " + json.dumps(f['detail'], sort_keys=True)[:400])
    verdict = 'BLOCKED' if report['blocking'] else 'CLEAR'
    lines += ['', f"{verdict}: {report['blocking']} blocking, {report['warnings']} warning(s)"]
    return '\n'.join(lines)


def build_context(args):
    env = dict(os.environ)
    pick = lambda value, var, default: Path(value or env.get(var) or default)
    return Context(studio=pick(args.studio, 'DTS_STUDIO', STUDIO).resolve(),
                   home=pick(args.home, 'DTS_HOME', Path.home()),
                   proc=pick(args.proc, 'DTS_PROC', '/proc'),
                   plymouthd_conf=pick(args.plymouthd_conf, 'DTS_PLYMOUTHD_CONF', '/etc/plymouth/plymouthd.conf'),
                   plymouth_themes_dir=pick(args.plymouth_themes_dir, 'DTS_PLYMOUTH_THEMES_DIR', '/usr/share/plymouth/themes'),
                   system_state=pick(args.system_state, 'DTS_SYSTEM_STATE', '/var/lib/desktop-theme-studio'),
                   env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only activation preflight (exit 0 clear, 1 blocked, 2 bad invocation).')
    parser.add_argument('id')
    parser.add_argument('--stage', required=True, choices=('user', 'root', 'catalog'))
    parser.add_argument('--kind', choices=('preset', 'refinement', 'collection'),
                        help='disambiguate ids that are both a preset and a collection profile')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--studio'), parser.add_argument('--home'), parser.add_argument('--proc')
    parser.add_argument('--plymouthd-conf'), parser.add_argument('--plymouth-themes-dir'), parser.add_argument('--system-state')
    args = parser.parse_args(argv)
    ctx = build_context(args)
    try:
        target = resolve_target(ctx.studio, args.id, args.kind)
    except ValueError as exc:
        print(f'preflight: {exc}', file=sys.stderr)
        return 2
    findings = run(ctx, target, args.stage)
    report = {'id': target.ident, 'stage': args.stage, 'kinds': target.kinds, 'primary': target.primary,
              'studio': str(ctx.studio), 'findings': findings,
              'blocking': sum(f['level'] == 'block' for f in findings),
              'warnings': sum(f['level'] == 'warn' for f in findings)}
    report['exit'] = 1 if report['blocking'] else 0
    print(json.dumps(report, indent=2) if args.json else render(report))
    return report['exit']


if __name__ == '__main__':
    sys.exit(main())
