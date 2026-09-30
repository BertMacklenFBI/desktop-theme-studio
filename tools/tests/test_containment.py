"""Tests for tools/containment.py. Every marker and home tree lives in a temp dir.

Run: /usr/bin/python3 -B -m unittest discover -s tools/tests -p 'test_containment.py' -v
"""
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parents[1]
REAL_STUDIO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import containment  # noqa: E402


def write(path, text='x'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / 'home'
        self.markers = self.root / 'markers'
        self.studio = self.root / 'studio'
        for top in containment.ROOTS:
            (self.home / top).mkdir(parents=True)
        self.guarded = [write(self.home / '.bashrc', 'alias a=b\n'), write(self.home / '.local/bin/play', '#!/bin/sh\n')]
        self.absent = self.home / '.zshrc'

    def tearDown(self):
        self._tmp.cleanup()

    def protected(self):
        """Stand-in for studio.protected(): same node shape, temp paths only."""
        import hashlib
        out = {}
        for p in [*self.guarded, self.absent]:
            if p.exists():
                out[str(p)] = {'kind': 'file', 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                               'mode': p.stat().st_mode & 0o777}
            else:
                out[str(p)] = {'kind': 'missing'}
        return out

    def mark(self, ident='tangerine-graphite', clock=None):
        return containment.mark(ident, markers=self.markers, home=self.home, protected_fn=self.protected,
                                clock=clock)

    def verify(self, ident='tangerine-graphite', **kw):
        return containment.verify(ident, markers=self.markers, home=self.home, protected_fn=self.protected, **kw)

    def later(self):
        # ctime cannot be set; guarantee the next write lands on a later timestamp.
        time.sleep(containment.SLACK_NS / 1e9 + 0.05)


class MarkTests(Base):
    def test_marker_layout(self):
        path = self.mark()
        self.assertEqual(path.parent, self.markers)
        self.assertRegex(path.name, r'^tangerine-graphite-\d{8}T\d{6}Z$')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        data = json.loads(path.read_text())
        self.assertEqual(data['schema'], 1)
        self.assertEqual(data['id'], 'tangerine-graphite')
        self.assertEqual(data['protected_count'], 3)
        self.assertIsInstance(data['protected'][str(self.guarded[0])]['mtime_ns'], int)
        self.assertIsNone(data['protected'][str(self.absent)]['mtime_ns'])
        self.assertEqual(len(data['protected'][str(self.guarded[0])]['sha256']), 64)

    def test_no_overwrite_and_bad_id(self):
        fixed = lambda: 1_790_000_000_000_000_000
        self.mark(clock=fixed)
        with self.assertRaises(FileExistsError):
            self.mark(clock=fixed)
        for bad in ('../x', 'Upper', ''):
            with self.assertRaises(ValueError):
                self.mark(bad)

    def test_default_marker_dir_is_studio_state_markers(self):
        path = containment.mark('p', studio=self.studio, protected_fn=self.protected)
        self.assertEqual(path.parent, self.studio / 'state/markers')

    def test_newest_marker_ignores_prefix_collisions(self):
        self.markers.mkdir()
        for name in ('red-panda-overtime-20260101T000000Z', 'red-panda-overtime-20260301T000000Z',
                     'red-panda-overtime-preflight-20270101T000000Z', 'red-panda-overtime-20260401T000000Z.bak'):
            write(self.markers / name, '{}')
        self.assertEqual(containment.newest_marker('red-panda-overtime', self.markers).name,
                         'red-panda-overtime-20260301T000000Z')
        self.assertIsNone(containment.newest_marker('other', self.markers))
        self.assertIsNone(containment.newest_marker('other', self.root / 'absent'))


class VerifyTests(Base):
    def test_clean(self):
        write(self.home / '.themes/Old/gtk-3.0/gtk.css')
        self.later()
        self.mark()
        result = self.verify()
        self.assertTrue(result['clean'], result)
        self.assertEqual(result['protected_checked'], 3)

    def test_newer_files_and_skips(self):
        existing = write(self.home / '.config/kitty/kitty.conf', 'a')
        self.later()
        self.mark()
        self.later()
        write(self.home / '.themes/New/gtk-3.0/gtk.css')
        existing.write_text('b')
        write(self.home / '.icons/New/icon-theme.cache')          # evidence: kept
        write(self.home / '.config/app/Cache/data_0')              # skipped by name
        write(self.home / '.config/app/GPUCache/x')
        write(self.home / '.config/chromium/Default/Preferences')  # browser profile
        write(self.home / '.config/Claude/state.json')
        write(self.home / '.local/share/Trash/files/x')
        write(self.home / '.local/share/recently-used.xbel')
        write(self.home / '.local/share/app/logs/today')
        write(self.home / '.local/share/krita.log')
        result = self.verify()
        self.assertFalse(result['clean'])
        paths = {r['path'][len(str(self.home)) + 1:] for r in result['newer']}
        self.assertIn('.themes/New/gtk-3.0/gtk.css', paths)
        self.assertIn('.config/kitty/kitty.conf', paths)
        self.assertIn('.icons/New/icon-theme.cache', paths)
        for skipped in ('.config/app/Cache/data_0', '.config/app/GPUCache/x', '.config/chromium/Default/Preferences',
                        '.config/Claude/state.json', '.local/share/Trash/files/x', '.local/share/recently-used.xbel',
                        '.local/share/app/logs/today', '.local/share/krita.log'):
            self.assertFalse(any(p == skipped or p.startswith(skipped + '/') for p in paths), skipped)
        self.assertNotIn('.config/chromium', paths)
        kinds = {r['path'].rsplit('/', 1)[-1]: r['kind'] for r in result['newer']}
        self.assertEqual(kinds['gtk.css'], 'file')
        self.assertEqual(kinds['New'], 'dir')

    def test_depth_bound(self):
        deep = write(self.home / '.themes/A/b/c/d/e/deep.css', 'a')
        self.later()
        self.mark()
        self.later()
        deep.write_text('changed')  # in-place: only the file's own times move
        self.assertEqual(self.verify(depth=3)['newer'], [])
        self.assertEqual([r['path'] for r in self.verify(depth=6)['newer']], [str(deep)])

    def test_symlinks_not_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.home / '.config/awesome').symlink_to(outside)
        self.later()
        self.mark()
        self.later()
        write(outside / 'rc.lua')
        self.assertTrue(self.verify()['clean'])

    def test_truncated_walk_is_not_clean(self):
        for n in range(5):
            write(self.home / f'.config/a{n}')
        self.later()
        self.mark()
        result = self.verify(max_entries=2)
        self.assertTrue(result['scan']['truncated'])
        self.assertFalse(result['clean'])

    def test_protected_changes(self):
        self.later()
        self.mark()
        self.later()
        self.guarded[0].write_text('alias a=c\n')                       # content
        os.utime(self.guarded[1], ns=(time.time_ns(), time.time_ns() + 10**9))  # touched only
        write(self.absent, 'new')                                      # appeared
        rows = {Path(r['path']).name: r['change'] for r in self.verify()['protected_changes']}
        self.assertEqual(rows, {'.bashrc': 'content', 'play': 'touched', '.zshrc': 'content'})

    def test_protected_set_changes(self):
        self.mark()
        self.guarded.append(write(self.home / '.profile'))
        rows = [r['change'] for r in self.verify()['protected_changes']]
        self.assertEqual(rows, ['added-to-protected-set'])

    def test_marker_errors(self):
        with self.assertRaises(FileNotFoundError):
            self.verify()
        bogus = write(self.root / 'bogus', '{"schema": 2}')
        with self.assertRaises(ValueError):
            self.verify(marker=bogus)
        other = self.mark('other')
        with self.assertRaises(ValueError):
            self.verify('mine', marker=other)


class CliTests(Base):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = containment.main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_mark_and_verify_exit_codes(self):
        """Uses the real studio.protected() (read-only hashing) with a temp home and marker dir."""
        common = ['--studio', str(REAL_STUDIO), '--markers-dir', str(self.markers), '--home', str(self.home)]
        code, out, err = self.cli('mark', 'demo', *common)
        self.assertEqual(code, 0, err)
        self.assertIn('protected paths hashed', out)
        marker = next(self.markers.iterdir())
        self.assertEqual(json.loads(marker.read_text())['protected_count'], 28)
        code, out, _ = self.cli('verify', 'demo', *common)
        self.assertEqual(code, 0, out)
        self.assertIn('CLEAN', out)
        self.later()
        write(self.home / '.themes/Demo/index.theme')
        code, out, _ = self.cli('verify', 'demo', *common, '--json')
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(out)['newer']), 2)  # the new theme dir and its file
        self.assertEqual(self.cli('verify', 'missing', *common)[0], 2)
        self.assertEqual(self.cli('verify', 'Bad/Id', *common)[0], 2)
        self.assertEqual(self.cli('verify', 'demo', *common, '--marker', str(self.root / 'nope'))[0], 2)
        self.assertEqual(self.cli('explode')[0], 2)

    def test_render_limits_rows(self):
        result = {'id': 'x', 'marker': 'm', 'since': 's', 'home': 'h', 'protected_checked': 1,
                  'protected_changes': [{'path': '/p', 'change': 'content'}],
                  'newer': [{'path': f'/f{i}', 'kind': 'file', 'field': 'mtime', 'at': '2026-09-23T00:00:00'} for i in range(5)],
                  'scan': {'scanned': 9, 'depth': 6, 'truncated': False, 'errors': []}, 'clean': False}
        text = containment.render(result, 2)
        self.assertIn('... 3 more', text)
        self.assertIn('PROTECTED content', text)
        self.assertTrue(text.endswith('CHANGES FOUND'))


@unittest.skipUnless((REAL_STUDIO / 'studio.py').exists() and (REAL_STUDIO / 'audits/applications.json').exists(),
                     'real studio not present')
class RealStudioTests(unittest.TestCase):
    def test_real_protected_set_is_28(self):
        protected = containment.load_studio(REAL_STUDIO).protected()
        self.assertEqual(len(protected), 28)
        snap = containment.protected_snapshot(lambda: protected)
        self.assertTrue(all('mtime_ns' in node for node in snap.values()))


if __name__ == '__main__':
    unittest.main()
