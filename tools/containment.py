#!/usr/bin/python3 -B
"""Containment marker and verifier for one Studio activation.

    /usr/bin/python3 -B tools/containment.py mark ID
    /usr/bin/python3 -B tools/containment.py verify ID [--marker PATH] [--json]

``mark`` is the only writer: it creates ``state/markers/<ID>-<UTC ts>`` (mode 0600,
exclusive create) holding a JSON snapshot of ``studio.protected()`` (the 28 protected
command/shell paths: sha256, kind, mode) plus each path's mtime.

``verify`` is read-only. Against the newest marker for ID (or --marker) it reports:
  * protected paths whose kind/hash/mode changed, which appeared/disappeared, or whose
    mtime moved (touched without content change);
  * entries under ~/.themes ~/.icons ~/.config ~/.local/share whose mtime or ctime is
    newer than the marker (depth-bounded, no symlink traversal), minus SKIP_* below.
Exit codes: 0 clean, 1 changes found (or the walk hit --max-entries and is incomplete),
2 error (no marker, bad marker, bad id).

``studio.protected()`` always hashes the real $HOME (studio.HOME); --home only moves the walk.

Skip list (documented; paths are relative to $HOME):
  SKIP_PATHS  whole subtrees that churn for reasons unrelated to theming, or hold
              private data: browser/Electron profiles, agent/IDE state, mail/PIM
              stores, clipboard managers, recents, Trash, indexers, keyrings,
              flatpak/container stores, game data, audio cookies.
  SKIP_NAMES  component names skipped anywhere under ~/.config and ~/.local/share
              (never under ~/.themes or ~/.icons, where icon-theme.cache is evidence):
              caches, crash dumps, web storage, logs, editor swap/lock files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

sys.dont_write_bytecode = True

STUDIO = Path(__file__).resolve().parents[1]
ROOTS = ('.themes', '.icons', '.config', '.local/share')
DEFAULT_DEPTH = 6
DEFAULT_MAX_ENTRIES = 400_000
# Kernel file timestamps use the coarse clock (up to one tick behind time.time_ns()),
# so a write just after `mark` can carry a stamp slightly *before* created_ns. Compare
# against created_ns - SLACK_NS: an edge false positive beats a missed write.
SLACK_NS = 100_000_000
SLUG = re.compile(r'[a-z0-9][a-z0-9-]*\Z')
STAMP = '%Y%m%dT%H%M%SZ'
STAMP_RE = r'\d{8}T\d{6}Z'

SKIP_PATHS = (
    # browsers and Electron/Chromium profiles
    '.config/chromium', '.config/google-chrome*', '.config/BraveSoftware', '.config/microsoft-edge*',
    '.config/vivaldi*', '.config/opera*', '.config/mozilla', '.config/Code', '.config/Code - OSS',
    '.config/VSCodium', '.config/discord', '.config/Slack', '.config/Signal', '.config/Element',
    '.local/share/webkitgtk', '.local/share/kcookiejar',
    # agent / assistant state (this tooling's own sessions)
    '.config/Claude', '.config/Codex', '.local/share/claude',
    # mail, PIM, accounts and secrets
    '.config/evolution', '.local/share/evolution', '.config/akonadi', '.local/share/akonadi',
    '.config/goa-1.0', '.config/gh', '.local/share/keyrings', '.local/share/kwalletd',
    # clipboard managers and recents
    '.config/copyq', '.config/clipit', '.config/parcellite', '.local/share/klipper', '.local/share/gpaste',
    '.local/share/recently-used.xbel', '.local/share/RecentDocuments', '.local/share/user-places.xbel*',
    # trash, indexers, metadata daemons, session bookkeeping
    '.local/share/Trash', '.local/share/zeitgeist', '.local/share/baloo', '.local/share/tracker*',
    '.local/share/gvfs-metadata', '.local/share/kactivitymanagerd', '.config/session',
    '.config/pulse', '.config/ibus/bus',
    # large package / container / VM / game stores
    '.local/share/flatpak', '.local/share/containers', '.local/share/pnpm', '.config/VirtualBox',
    '.local/share/Steam', '.local/share/Valve Corporation', '.local/share/Larian Studios',
    '.local/share/Aspyr', '.local/share/aspyr-media', '.config/unity3d',
)
SKIP_NAMES = (
    '*cache*', 'crashpad', 'crash reports', 'crashes', 'service worker', 'indexeddb', 'local storage',
    'session storage', 'blob_storage', 'logs', '*.log', '*.swp', '*~', '.~lock.*#', 'singleton*',
)


def now_ns():
    return time.time_ns()


def load_studio(studio=STUDIO):
    """Import the Studio's studio.py (side-effect free at import) for protected()."""
    sys.path.insert(0, str(studio))
    try:
        import studio as module  # noqa: E402
    finally:
        sys.path.pop(0)
    return module


def protected_snapshot(protected_fn):
    snap = protected_fn()
    for raw, node in snap.items():
        try:
            node['mtime_ns'] = os.lstat(raw).st_mtime_ns
        except OSError:
            node['mtime_ns'] = None
    return snap


def marker_dir(studio):
    return Path(studio) / 'state/markers'


def check_id(ident):
    if not SLUG.fullmatch(ident or ''):
        raise ValueError(f'invalid id {ident!r}; expected a lowercase slug')
    return ident


def mark(ident, *, studio=STUDIO, markers=None, home=None, protected_fn=None, clock=None):
    """Write one marker; refuses to overwrite. Returns its path."""
    check_id(ident)
    created_ns = (clock or now_ns)()
    protected_fn = protected_fn or load_studio(studio).protected
    snapshot = protected_snapshot(protected_fn)
    when = dt.datetime.fromtimestamp(created_ns / 1e9, dt.timezone.utc)
    folder = Path(markers) if markers else marker_dir(studio)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f'{ident}-{when.strftime(STAMP)}'
    payload = {'schema': 1, 'id': ident, 'created_at': when.isoformat(), 'created_ns': created_ns,
               'home': str(home or Path.home()), 'roots': list(ROOTS), 'protected_count': len(snapshot),
               'protected': snapshot}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write('\n')
    return path


def newest_marker(ident, folder):
    pattern = re.compile(re.escape(ident) + '-' + STAMP_RE + r'\Z')
    try:
        found = sorted(p for p in Path(folder).iterdir() if pattern.fullmatch(p.name) and p.is_file())
    except FileNotFoundError:
        return None
    return found[-1] if found else None


_SKIP_PATH_RE = re.compile('|'.join(fnmatch.translate(p) for p in SKIP_PATHS))
_SKIP_NAME_RE = re.compile('|'.join(fnmatch.translate(p) for p in SKIP_NAMES))


def skipped(rel, top):
    if _SKIP_PATH_RE.match(rel):
        return True
    if top in ('.config', '.local/share'):
        return bool(_SKIP_NAME_RE.match(rel.rsplit('/', 1)[-1].lower()))
    return False


def newer_entries(home, since_ns, *, depth=DEFAULT_DEPTH, max_entries=DEFAULT_MAX_ENTRIES, roots=ROOTS):
    """Walk ROOTS under home; return (changes, stats). Never follows symlinks."""
    home = Path(home)
    prefix = len(str(home).rstrip('/')) + 1
    changes, scanned, truncated, errors = [], 0, False, []
    for top in roots:
        stack = [(str(home / top), 0)]
        while stack:
            folder, level = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except FileNotFoundError:
                continue
            except OSError as exc:
                errors.append(f'{folder}: {exc.strerror}')
                continue
            for entry in entries:
                scanned += 1
                if scanned > max_entries:
                    truncated = True
                    break
                full = entry.path
                rel = full[prefix:]
                if skipped(rel, top):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = stat.S_ISDIR(st.st_mode)
                newest = max(st.st_mtime_ns, st.st_ctime_ns)
                if newest > since_ns:
                    kind = 'dir' if is_dir else 'symlink' if stat.S_ISLNK(st.st_mode) else 'file'
                    changes.append({'path': full, 'kind': kind,
                                    'field': 'mtime' if st.st_mtime_ns > since_ns else 'ctime',
                                    'at': dt.datetime.fromtimestamp(newest / 1e9, dt.timezone.utc).isoformat()})
                if is_dir and level + 1 < depth:
                    stack.append((full, level + 1))
            if truncated:
                break
        if truncated:
            break
    changes.sort(key=lambda c: c['path'])
    return changes, {'scanned': scanned, 'truncated': truncated, 'depth': depth, 'errors': errors[:20]}


def compare_protected(before, after):
    rows = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old is None:
            rows.append({'path': path, 'change': 'added-to-protected-set'})
        elif new is None:
            rows.append({'path': path, 'change': 'dropped-from-protected-set'})
        else:
            strip = lambda n: {k: v for k, v in n.items() if k != 'mtime_ns'}
            if strip(old) != strip(new):
                rows.append({'path': path, 'change': 'content', 'before': strip(old), 'after': strip(new)})
            elif old.get('mtime_ns') != new.get('mtime_ns'):
                rows.append({'path': path, 'change': 'touched'})
    return rows


def verify(ident, *, studio=STUDIO, markers=None, marker=None, home=None, protected_fn=None,
           depth=DEFAULT_DEPTH, max_entries=DEFAULT_MAX_ENTRIES):
    check_id(ident)
    path = Path(marker) if marker else newest_marker(ident, Path(markers) if markers else marker_dir(studio))
    if path is None:
        raise FileNotFoundError(f'no marker for {ident} in {markers or marker_dir(studio)}; run: containment.py mark {ident}')
    data = json.loads(Path(path).read_text())
    if data.get('schema') != 1 or not isinstance(data.get('created_ns'), int) or not isinstance(data.get('protected'), dict):
        raise ValueError(f'not a containment marker: {path}')
    if data.get('id') != ident:
        raise ValueError(f'marker {path} belongs to {data.get("id")!r}, not {ident!r}')
    home = Path(home or data.get('home') or Path.home())
    protected_fn = protected_fn or load_studio(studio).protected
    protected_changes = compare_protected(data['protected'], protected_snapshot(protected_fn))
    files, scan = newer_entries(home, data['created_ns'] - SLACK_NS, depth=depth, max_entries=max_entries)
    result = {'id': ident, 'marker': str(path), 'since': data.get('created_at'), 'home': str(home),
              'protected_checked': len(data['protected']), 'protected_changes': protected_changes,
              'newer': files, 'scan': scan, 'skip_paths': list(SKIP_PATHS), 'skip_names': list(SKIP_NAMES)}
    # A truncated walk cannot prove containment, so it is never reported clean.
    result['clean'] = not protected_changes and not files and not scan['truncated']
    return result


def render(result, limit):
    lines = [f"containment verify {result['id']}: marker {result['marker']}",
             f"  since {result['since']}; home {result['home']}",
             f"  protected paths: {result['protected_checked']} checked, {len(result['protected_changes'])} changed"]
    for row in result['protected_changes']:
        lines.append(f"    PROTECTED {row['change']:<10} {row['path']}")
    scan = result['scan']
    lines.append(f"  newer entries: {len(result['newer'])} (scanned {scan['scanned']}, depth {scan['depth']}"
                 f"{', TRUNCATED' if scan['truncated'] else ''})")
    for row in result['newer'][:limit]:
        lines.append(f"    {row['kind']:<7} {row['field']:<5} {row['at'][:19]}  {row['path']}")
    if len(result['newer']) > limit:
        lines.append(f"    ... {len(result['newer']) - limit} more (use --json or --limit)")
    for error in scan['errors']:
        lines.append(f'    unreadable: {error}')
    lines.append('CLEAN' if result['clean'] else 'CHANGES FOUND')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Containment marker (mark) and read-only verifier (verify).')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('mark', 'verify'):
        p = sub.add_parser(name)
        p.add_argument('id')
        p.add_argument('--studio', default=os.environ.get('DTS_STUDIO') or str(STUDIO))
        p.add_argument('--markers-dir', help='default: <studio>/state/markers')
        p.add_argument('--home', default=os.environ.get('DTS_HOME'))
        p.add_argument('--json', action='store_true')
        if name == 'verify':
            p.add_argument('--marker')
            p.add_argument('--max-depth', type=int, default=DEFAULT_DEPTH)
            p.add_argument('--max-entries', type=int, default=DEFAULT_MAX_ENTRIES)
            p.add_argument('--limit', type=int, default=100, help='rows shown in the text report')
    args = parser.parse_args(argv)
    studio = Path(args.studio).resolve()
    try:
        if args.command == 'mark':
            path = mark(args.id, studio=studio, markers=args.markers_dir, home=args.home)
            data = json.loads(path.read_text())
            summary = {'marker': str(path), 'created_at': data['created_at'], 'protected_count': data['protected_count']}
            print(json.dumps(summary, indent=2) if args.json else
                  f"marker {path}\n  {data['protected_count']} protected paths hashed at {data['created_at']}")
            return 0
        result = verify(args.id, studio=studio, markers=args.markers_dir, marker=args.marker, home=args.home,
                        depth=args.max_depth, max_entries=args.max_entries)
        print(json.dumps(result, indent=2) if args.json else render(result, args.limit))
        return 0 if result['clean'] else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'containment: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
