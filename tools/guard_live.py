#!/usr/bin/python3 -B
"""Claude Code PreToolUse guard for the Desktop Theme Studio subagents.

Hook use (stdin = PreToolUse JSON):
    /usr/bin/python3 -B tools/guard_live.py --role builder-desktop
Manual use:
    /usr/bin/python3 -B tools/guard_live.py --role reviewer --explain "gsettings set a b c"
    /usr/bin/python3 -B tools/guard_live.py --role designer --explain-write presets/x/design.json

    /usr/bin/python3 -B tools/guard_live.py --role builder-desktop --preset new-theme   (hook, one preset)

Exit 0 = allow (silent).  Exit 2 = block, one-line reason on stderr.

Studio paths: Write/Edit targets *and* every Bash write/delete target that
resolves inside the studio pass the same per-role allow-list (role_allows).
The directory that tops a role's area (presets/<id>/desktop for
builder-desktop, presets/<id>/identity for designer, ...) may be created or
written into but never deleted/moved; rm/mv of a whole presets/<id> is denied
for every role; a brand-new presets/<id> may be created by designer/builder.
Without --preset the hook cannot know the agent's preset, so "own area" means
the matching subtree of ANY non-reserved preset (cross-preset writes inside the
same subtree name remain possible).  Pass --preset <id> to close that.
Malformed hook input fails closed (exit 2).

This is a best-effort *static* guard.  It tokenises shell syntax (quotes,
escapes, $'..', $( ), backticks, <( ), heredocs, bash -c, eval, env/nice/
timeout/xargs/find -exec wrappers, python/perl/awk inline code) and checks
every simple command it can see, but it cannot observe what an allowed
program does internally.  The user's permission prompts and the coordinator's
one-stage-at-a-time rule remain the real control.  Stdlib only.
"""
from __future__ import annotations

import argparse
import fnmatch
import glob as globmod
import json
import os
import re
import shlex
import shutil
import sys

ROLES = ('designer', 'builder', 'builder-desktop', 'builder-apps', 'builder-boot',
         'integrator', 'reviewer')

# Live paths nobody but the coordinator may write (home-relative, then absolute).
# The first ten are the coordinator's list; the rest are other per-user appearance
# files Cinnamon/GTK read live.
HOME_PROTECTED = ('.themes', '.icons', '.local/share', '.local/bin', '.config', '.bashrc',
                  '.bash_aliases', '.profile', '.zshrc', '.claude',
                  '.gtkrc-2.0', '.cinnamon', '.xsessionrc', '.Xresources', '.fonts', '.face',
                  '.bash_profile')
ABS_PROTECTED = ('/etc', '/boot', '/usr', '/var')
MARKER_DIRS = frozenset({'state', 'verification', 'backups'})
MARKER_SUFFIXES = ('receipt.json', 'latest.json')
EXCLUDED_PRESETS = ('_template', 'example', 'current-observed', 'red-panda-overtime*')
DEV_OK = re.compile(r'^/dev/(null|stdout|stderr|tty|fd/\d+)$|^/proc/self/fd/\d+$')

MAX_DEPTH = 8
MAX_WALK = 20000
MAX_SCRIPT_BYTES = 2 * 1024 * 1024



LINEAGE_SHIM_RE = re.compile(r"_lineage_code\(\s*['\"]([^'\"]+)['\"]")
LINEAGE_MODULE_RE = re.compile(r'[A-Za-z0-9_]+\.py')

class Denied(Exception):
    pass


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# Shell lexer
# --------------------------------------------------------------------------
class Word:
    """One shell word. parts: ('lit', text, quoted) | ('var', name) | ('tilde', user)
    | ('sub', command-string) | ('dyn', raw)."""

    def __init__(self, start):
        self.parts = []
        self.glob = False
        self.brace = False
        self.start = start
        self.raw = ''

    def add(self, kind, value, quoted=False):
        if kind == 'lit' and self.parts and self.parts[-1][0] == 'lit' and self.parts[-1][2] == quoted:
            self.parts[-1] = ('lit', self.parts[-1][1] + value, quoted)
        else:
            self.parts.append((kind, value, quoted))

    def subs(self):
        return [p[1] for p in self.parts if p[0] == 'sub']

    def unquoted_literal(self):
        if all(p[0] == 'lit' and not p[2] for p in self.parts):
            return ''.join(p[1] for p in self.parts)
        return None

    def last_char(self):
        if self.parts and self.parts[-1][0] == 'lit' and not self.parts[-1][2]:
            return self.parts[-1][1][-1:]
        return ''


ANSI = {'n': '\n', 't': '\t', 'r': '\r', 'a': '\a', 'b': '\b', 'e': '\x1b', 'E': '\x1b',
        'f': '\f', 'v': '\v', '\\': '\\', "'": "'", '"': '"', '?': '?'}


def _ansi_c(s, i):
    """s[i] is the char after $' ; return (decoded, index after closing quote)."""
    out = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == "'":
            return ''.join(out), i + 1
        if c == '\\' and i + 1 < n:
            d = s[i + 1]
            if d in ANSI:
                out.append(ANSI[d]); i += 2; continue
            m = re.match(r'x([0-9a-fA-F]{1,2})|u([0-9a-fA-F]{1,4})|U([0-9a-fA-F]{1,8})|([0-7]{1,3})', s[i + 1:])
            if m:
                val = next(g for g in m.groups() if g is not None)
                base = 8 if m.group(4) else 16
                try:
                    out.append(chr(int(val, base)))
                except (ValueError, OverflowError):
                    pass
                i += 1 + len(m.group(0)); continue
            if d == 'c' and i + 2 < n:
                out.append(chr(ord(s[i + 2]) & 0x1f)); i += 3; continue
            out.append('\\' + d); i += 2; continue
        out.append(c); i += 1
    raise ParseError("unterminated $'...'")


def _skip_squote(s, i):
    j = s.find("'", i + 1)
    if j < 0:
        raise ParseError('unterminated single quote')
    return j + 1


def _skip_dquote(s, i):
    i += 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            i += 2; continue
        if c == '"':
            return i + 1
        if c == '$' and s.startswith('$(', i):
            i = match_paren(s, i + 2) + 1; continue
        if c == '`':
            i = _skip_backtick(s, i); continue
        i += 1
    raise ParseError('unterminated double quote')


def _skip_backtick(s, i):
    i += 1
    n = len(s)
    while i < n:
        if s[i] == '\\':
            i += 2; continue
        if s[i] == '`':
            return i + 1
        i += 1
    raise ParseError('unterminated backtick')


def match_paren(s, i):
    """i is just after '(' ; return index of the matching ')'."""
    depth = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            i += 2; continue
        if c == "'":
            i = _skip_squote(s, i); continue
        if c == '"':
            i = _skip_dquote(s, i); continue
        if c == '`':
            i = _skip_backtick(s, i); continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ParseError('unbalanced parenthesis')


def match_brace(s, i):
    depth = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            i += 2; continue
        if c == "'":
            i = _skip_squote(s, i); continue
        if c == '"':
            i = _skip_dquote(s, i); continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ParseError('unbalanced ${')


def extract_subs(text):
    """Command substitutions inside free text (heredoc bodies, ${..} operands)."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '\\':
            i += 2; continue
        if text.startswith('$(', i):
            j = match_paren(text, i + 2)
            out.append(text[i + 2:j]); i = j + 1; continue
        if text[i] == '`':
            j = _skip_backtick(text, i)
            out.append(text[i + 1:j - 1].replace('\\`', '`')); i = j; continue
        i += 1
    return out


NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
WORD_END = set(' \t\n;&|()<>')


def lex(s):
    toks = []
    pending = []
    state = {'w': None}
    i = 0
    n = len(s)

    def cur(pos):
        if state['w'] is None:
            state['w'] = Word(pos)
        return state['w']

    def flush(pos):
        w = state['w']
        if w is not None:
            w.raw = s[w.start:pos]
            toks.append(('word', w))
            state['w'] = None

    def dollar(i, w, quoted):
        nxt = s[i + 1] if i + 1 < n else ''
        if nxt == '(':
            j = match_paren(s, i + 2)
            w.add('sub', s[i + 2:j]); return j + 1
        if nxt == '{':
            j = match_brace(s, i + 2)
            inner = s[i + 2:j]
            if NAME_RE.fullmatch(inner):
                w.add('var', inner, quoted)
            else:
                w.add('dyn', inner)
                for sub in extract_subs(inner):
                    w.add('sub', sub)
            return j + 1
        m = NAME_RE.match(s, i + 1)
        if m:
            w.add('var', m.group(0), quoted); return m.end()
        if nxt and nxt in '0123456789@*#?$!-':
            w.add('dyn', '$' + nxt); return i + 2
        w.add('lit', '$', quoted); return i + 1

    while i < n:
        c = s[i]
        w = state['w']
        if c in ' \t':
            flush(i); i += 1; continue
        if c == '\n':
            flush(i); toks.append(('op', '\n')); i += 1
            while pending:
                hd = pending.pop(0)
                body = []
                while i < n:
                    j = s.find('\n', i)
                    if j < 0:
                        line, i = s[i:], n
                    else:
                        line, i = s[i:j], j + 1
                    cmp = line.lstrip('\t') if hd['op'] == '<<-' else line
                    if cmp == hd['delim']:
                        break
                    body.append(line)
                hd['body'] = '\n'.join(body) + ('\n' if body else '')
            continue
        if c == '#' and w is None:
            j = s.find('\n', i)
            i = n if j < 0 else j
            continue
        if c == '\\':
            if i + 1 < n and s[i + 1] == '\n':
                i += 2; continue
            if i + 1 < n:
                cur(i).add('lit', s[i + 1], True); i += 2; continue
            i += 1; continue
        if c == "'":
            j = _skip_squote(s, i)
            cur(i).add('lit', s[i + 1:j - 1], True); i = j; continue
        if c == '$' and i + 1 < n and s[i + 1] == "'":
            text, i2 = _ansi_c(s, i + 2)
            cur(i).add('lit', text, True); i = i2; continue
        if c == '"' or (c == '$' and i + 1 < n and s[i + 1] == '"'):
            ww = cur(i)
            i = i + 1 if c == '"' else i + 2
            buf = []
            while True:
                if i >= n:
                    raise ParseError('unterminated double quote')
                d = s[i]
                if d == '"':
                    i += 1; break
                if d == '\\' and i + 1 < n and s[i + 1] in '$`"\\\n':
                    if s[i + 1] != '\n':
                        buf.append(s[i + 1])
                    i += 2; continue
                if d == '$':
                    ww.add('lit', ''.join(buf), True); buf = []
                    i = dollar(i, ww, True); continue
                if d == '`':
                    ww.add('lit', ''.join(buf), True); buf = []
                    j = _skip_backtick(s, i)
                    ww.add('sub', s[i + 1:j - 1].replace('\\`', '`')); i = j; continue
                buf.append(d); i += 1
            ww.add('lit', ''.join(buf), True)
            continue
        if c == '`':
            j = _skip_backtick(s, i)
            cur(i).add('sub', s[i + 1:j - 1].replace('\\`', '`')); i = j; continue
        if c == '$':
            i = dollar(i, cur(i), False); continue
        if c in '<>' and i + 1 < n and s[i + 1] == '(':
            j = match_paren(s, i + 2)
            cur(i).add('sub', s[i + 2:j]); i = j + 1; continue
        if c in '<>' or (c == '&' and i + 1 < n and s[i + 1] == '>'):
            fd = None
            if w is not None and c != '&':
                lit = w.unquoted_literal()
                if lit is not None and lit.isdigit():
                    fd = lit; state['w'] = None
            flush(i)
            for op in ('&>>', '&>', '<<<', '<<-', '<<', '<>', '<&', '>>', '>|', '>&', '<', '>'):
                if s.startswith(op, i):
                    break
            i += len(op)
            if op in ('<<', '<<-'):
                while i < n and s[i] in ' \t':
                    i += 1
                j = i
                while j < n and s[j] not in WORD_END:
                    if s[j] in '\'"':
                        k = s.find(s[j], j + 1)
                        j = n if k < 0 else k + 1
                    else:
                        j += 1
                raw = s[i:j]
                if not raw:
                    raise ParseError('heredoc without delimiter')
                hd = {'op': op, 'delim': re.sub(r'[\'"\\]', '', raw),
                      'quoted': bool(re.search(r'[\'"\\]', raw)), 'body': None}
                toks.append(('heredoc', hd)); pending.append(hd)
                i = j
            else:
                toks.append(('redir', op, fd))
            continue
        if c in ';&|()':
            flush(i)
            two = s[i:i + 2]
            if two in ('&&', '||', ';;', '|&', ';&'):
                toks.append(('op', two)); i += 2
            else:
                toks.append(('op', c)); i += 1
            continue
        ww = cur(i)
        if c == '~' and (not ww.parts or ww.last_char() in ('=', ':')):
            m = re.match(r'[A-Za-z0-9_.-]*', s[i + 1:])
            user = m.group(0)
            end = i + 1 + len(user)
            if end >= n or s[end] == '/' or s[end] in WORD_END or s[end] in ':':
                ww.add('tilde', user); i = end; continue
        if c in '*?[':
            ww.glob = True
        if c == '{':
            ww.brace = True
        ww.add('lit', c, False)
        i += 1
    flush(n)
    for hd in pending:
        if hd['body'] is None:
            hd['body'] = ''
    return toks


class Cmd:
    def __init__(self, pipe_in=False):
        self.words = []
        self.redirs = []   # (op, fd, Word | heredoc-dict)
        self.pipe_in = pipe_in


def parse(tokens):
    items = []
    cmd = Cmd()
    pending = None

    def end(pipe_next=False):
        nonlocal cmd
        if cmd.words or cmd.redirs:
            items.append(('cmd', cmd))
        cmd = Cmd(pipe_next)

    for tok in tokens:
        kind = tok[0]
        if kind == 'word':
            if pending:
                cmd.redirs.append((pending[0], pending[1], tok[1])); pending = None
            else:
                cmd.words.append(tok[1])
        elif kind == 'redir':
            if pending:
                raise ParseError('redirection without target')
            pending = (tok[1], tok[2])
        elif kind == 'heredoc':
            cmd.redirs.append((tok[1]['op'], None, tok[1]))
        else:
            if pending:
                raise ParseError('redirection without target')
            op = tok[1]
            if op == '(':
                end(cmd.pipe_in); items.append(('push',))
            elif op == ')':
                end(); items.append(('pop',))
            else:
                end(op in ('|', '|&'))
    if pending:
        raise ParseError('redirection without target')
    end()
    return items


def brace_expand(text, limit=64):
    m = None
    depth = 0
    start = -1
    for idx, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = idx
            depth += 1
        elif ch == '}' and depth:
            depth -= 1
            if depth == 0:
                inner = text[start + 1:idx]
                if ',' in inner:
                    m = (start, idx, inner); break
    if not m:
        return [text]
    start, stop, inner = m
    opts, d, buf = [], 0, ''
    for ch in inner:
        if ch == ',' and d == 0:
            opts.append(buf); buf = ''; continue
        d += ch == '{'
        d -= ch == '}'
        buf += ch
    opts.append(buf)
    out = []
    for o in opts:
        out.extend(brace_expand(text[:start] + o + text[stop + 1:], limit))
        if len(out) > limit:
            break
    return out[:limit]


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
class A:
    """A resolved argument. text is None when it cannot be determined statically."""
    __slots__ = ('text', 'word', 'within')

    def __init__(self, text, word=None, within=False):
        self.text = text
        self.word = word
        self.within = within

    @property
    def raw(self):
        if self.word is not None and self.word.raw:
            return self.word.raw
        return self.text if self.text is not None else '<runtime value>'

    @property
    def glob(self):
        return bool(self.word is not None and self.word.glob)

    @property
    def brace(self):
        return bool(self.word is not None and self.word.brace)


ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=')
COMMIT_RE = re.compile(r'^--c(o(m(m(it?)?)?)?)?(=.*)?$')
KEYWORDS = {'if', 'then', 'elif', 'else', 'fi', 'do', 'done', 'while', 'until', '!', '{', '}',
            'esac', 'coproc', '[[', ']]'}
SKIP_SEGMENT = {'for', 'select', 'case'}
PRIV = {'sudo', 'sudoedit', 'pkexec', 'doas', 'run0', 'su', 'runuser', 'chroot', 'nsenter',
        'unshare', 'machinectl', 'systemd-run'}
KILLERS = {'kill', 'pkill', 'killall', 'killall5', 'xkill', 'skill', 'snice'}
BOOT_TOOLS = {'update-initramfs', 'update-grub', 'update-grub2', 'grub-mkconfig', 'grub-install',
              'grub-set-default', 'grub-reboot', 'grub-editenv', 'grub2-mkconfig',
              'plymouth-set-default-theme', 'mkinitramfs', 'dracut', 'efibootmgr', 'bootctl',
              'update-alternatives', 'dpkg-reconfigure'}
POWER = {'shutdown', 'reboot', 'poweroff', 'halt', 'telinit', 'init', 'service', 'systemctl',
         'cinnamon-session-quit', 'gnome-session-quit', 'cinnamon-screensaver-command',
         'xdg-screensaver', 'dm-tool'}
INPUT_AUTOMATION = {'xdotool', 'ydotool', 'xte', 'wtype'}
WM_REPLACE = {'cinnamon', 'muffin', 'metacity', 'marco', 'mutter', 'xfwm4', 'openbox', 'compiz',
              'cinnamon2d', 'nemo-desktop'}
READONLY = {'grep', 'egrep', 'fgrep', 'rg', 'ag', 'ack', 'cat', 'less', 'more', 'head', 'tail',
            'wc', 'diff', 'cmp', 'echo', 'printf', 'ls', 'stat', 'file', 'jq', 'git-log', 'true',
            'false', 'test', '[', 'basename', 'dirname', 'realpath', 'readlink', 'md5sum',
            'sha256sum', 'sha1sum', 'column', 'sort', 'uniq', 'cut', 'tr', 'nl', 'od', 'xxd',
            'hexdump', 'strings'}
THEME_LIVE = {'apply', 'keep', 'restore', 'recover', 'refresh'}
# script basename -> (denied first-positional actions or None = always deny)
LIVE_SCRIPTS = {
    'isolate.py': None,
    'repair_installer.py': None,
    'restore.sh': None,
    'install-shortcut.py': None,
    'shortcut.py': 'ANYARG',
    'eww_reload.py': None,
    'trial.py': None,
    'widget_apply.py': None,
    'theme.sh': THEME_LIVE,
    'theme.py': THEME_LIVE,
    'studio.py': {'snapshot', 'apply', 'restore'},
    'publish_sources.py': {'publish', 'apply', 'restore'},
    'desktop_control.py': {'install', 'trial', 'keep', 'restore', 'recover'},
    'live.py': {'apply', 'keep', 'restore'},
}
OUTPUT_FLAG_RE = re.compile(r'^(--?(?:o|out|output|outdir|output-dir|dest|destination|target|target-dir|'
                            r'install|install-dir|install-root|prefix|to|stage-dir|export-dir))(?:=(.*))?$')
# Other scripts that drive or verify the live session (GUI automation, serial live trials).
LIVE_SCRIPT_GLOBS = ('capture_live*.py', 'verify_live*.py', 'verify_apps.py', 'rollout.py',
                     'reload_fragment.py', 'rpo-flow.py', 'flow.py')
SHELLS = {'bash', 'sh', 'dash', 'zsh', 'ksh', 'mksh', 'ash', 'rbash', 'fish', 'busybox-sh'}
PY_RE = re.compile(r'^(python[0-9.]*|pypy[0-9.]*)$')
OTHER_INTERP = {'perl', 'ruby', 'node', 'nodejs', 'php', 'lua', 'luajit', 'tclsh', 'wish',
                'osascript', 'deno', 'bun', 'gjs', 'cjs', 'Rscript', 'julia'}
AWKS = {'awk', 'gawk', 'mawk', 'nawk', 'busybox-awk'}
EDITORS = {'ed', 'ex', 'vi', 'vim', 'nvim', 'nano', 'emacs', 'micro', 'kak', 'joe', 'mcedit',
           'xed', 'gedit', 'code'}

_P = r'(?<![\w.-])'
FUZZY = [(label, re.compile(rx)) for label, rx in (
    ('gsettings write', r'\bgsettings\s+(?:--schemadir\s+\S+\s+)?(?:set|reset|reset-recursively)\b'),
    ('dconf write', r'\bdconf\s+(?:write|reset|load)\b'),
    ('privilege escalation', _P + r'(?:sudo|pkexec|doas|run0)(?![\w-])'),
    ('systemctl', _P + r'systemctl(?![\w-])'),
    ('--replace', r'(?<![\w-])--replace\b'),
    ('process kill', _P + r'(?:pkill|killall|xkill)(?![\w-])|\bos\.kill(?:pg)?\b|' + _P + r'kill\s+-?\w'),
    ('live theme action', r'\btheme\.(?:sh|py)\s+(?:apply|keep|restore|recover|refresh)\b'),
    ('isolate.py', r'\bisolate\.py\b'),
    ('--commit', r'(?<![\w-])--commit\b'),
    ('boot tool', r'\b(?:update-initramfs|update-grub2?|grub-mkconfig|grub-install|plymouth-set-default-theme)\b'),
    ('repair_installer.py', r'\brepair_installer\.py\b'),
    ('publish_sources publish/apply/restore', r'\bpublish_sources\.py\s+(?:publish|apply|restore)\b'),
    ('studio.py snapshot/apply/restore', r'\bstudio\.py\s+(?:snapshot|apply|restore)\b'),
    ('restore.sh', r'\brestore\.sh\b'),
    ('desktop_control live action', r'\bdesktop_control\.py\s+(?:install|trial|keep|restore|recover)\b'),
    ('theme shortcut', r'\.local/bin/theme\b|\bshortcut\.py\b|\binstall-shortcut\.py\b'),
    ('input automation', r'\b(?:xdotool|ydotool|wmctrl)\b'),
    ('cinnamon-settings', r'\bcinnamon-settings\b'),
    ('D-Bus method call', r'\bgdbus\s+(?:call|emit)\b|\bdbus-send\b|\bbusctl\s+(?:call|set-property|emit)\b|\bcall_sync\s*\(|\bdbus\.(?:SessionBus|SystemBus)\b'),
    ('Gio.Settings write', r'\bGio\.Settings\b[\s\S]*\b(?:set_\w+|reset)\s*\('),
    ('xdg default change', r'\bxdg-(?:settings\s+set|mime\s+default)\b'),
)]
WRITE_INTENT = re.compile(
    r"""\bopen\s*\([^)]*['"](?:[wax]|r\+)|\.write_(?:text|bytes)\s*\(|\bshutil\.\w+|"""
    r"""\bos\.(?:rename|replace|remove|unlink|makedirs|mkdir|symlink|link|chmod|chown|rmdir|truncate|removedirs)\b|"""
    r"""\.(?:unlink|rmdir|mkdir|touch|rename|replace|symlink_to|hardlink_to|chmod|write)\s*\(|"""
    r"""\bcopy(?:file|tree|2)?\s*\(|>>?|(?<![\w.-])(?:cp|mv|ln|rsync|install|tee|rm|touch|mkdir|dd)\s""")


def under(path, root):
    if root == '/':
        return True
    return path == root or path.startswith(root.rstrip('/') + '/')


class Guard:
    def __init__(self, role, studio_root=None, home=None, tmp_roots=None, environ=None, preset=None):
        if role not in ROLES:
            raise ValueError('unknown role %r' % role)
        if preset is not None and (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', preset) or
                                   preset in ('.', '..') or self._reserved(preset)):
            raise ValueError('invalid or reserved --preset %r' % preset)
        self.role = role
        self.preset = preset
        self.home = os.path.normpath(home or os.path.expanduser('~'))
        here = os.path.dirname(os.path.abspath(__file__))
        self.studio = os.path.realpath(studio_root or os.path.join(here, '..'))
        self.tmp_roots = [os.path.realpath(t) for t in (tmp_roots or ['/tmp'])]
        self.environ = dict(os.environ if environ is None else environ)
        self.environ['HOME'] = self.home
        self.protected = []
        for rel in HOME_PROTECTED:
            p = os.path.join(self.home, rel)
            for v in {os.path.normpath(p), os.path.realpath(p)}:
                self.protected.append((v, '~/' + rel))
        for p in ABS_PROTECTED:
            for v in {p, os.path.realpath(p)}:
                self.protected.append((v, p))
        self.tree_protected = [(v, label) for v, label in self.protected]
        self.tree_protected.append((self.studio, 'the studio root'))
        self.shortcut_real = os.path.realpath(os.path.join(self.home, '.local/bin/theme'))
        self.text_protected = self._text_protected()

    def _text_protected(self):
        rx = []
        h = re.escape(self.home)
        for rel in HOME_PROTECTED:
            parts = [re.escape(x) for x in rel.split('/')]
            body = r'\W{1,6}'.join(parts)
            rx.append(('~/' + rel, re.compile(r'(?:(?<![\w.])|' + h + '/)' + body + r'(?![\w-])')))
        return rx

    # ---------------------------------------------------------------- entry
    def decide(self, payload):
        try:
            self._decide(payload)
        except Denied as exc:
            return False, str(exc)
        return True, ''

    def _decide(self, payload):
        if not isinstance(payload, dict):
            raise Denied('unreadable hook input')
        tool = payload.get('tool_name')
        tin = payload.get('tool_input')
        if not isinstance(tool, str) or not isinstance(tin, dict):
            raise Denied('unreadable hook input')
        cwd = payload.get('cwd')
        if not isinstance(cwd, str) or not cwd:
            cwd = os.getcwd()
        if tool == 'Bash':
            cmd = tin.get('command')
            if not isinstance(cmd, str):
                raise Denied('Bash call without a command string')
            self.check_bash(cmd, cwd)
        elif tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
            path = tin.get('file_path') or tin.get('notebook_path')
            if not isinstance(path, str) or not path:
                raise Denied('%s call without a file path' % tool)
            self.check_edit_path(path, cwd)
        # other tools (Read, Grep, Glob, WebFetch, ...) are not this guard's concern

    # ---------------------------------------------------------------- paths
    def expand(self, text, env=None):
        env = env if env is not None else self.environ
        if text.startswith('~'):
            user, _, rest = text[1:].partition('/')
            if user == '':
                return os.path.join(env.get('HOME') or self.home, rest) if rest else (env.get('HOME') or self.home)
            text = os.path.expanduser(text)

        def var(m):
            name = m.group(1) or m.group(2)
            val = env.get(name)
            if val is None:
                raise Denied('path %r uses unset variable $%s' % (text, name))
            return val
        return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)', var, text)

    def absolute(self, text, cwd):
        if not os.path.isabs(text):
            if cwd is None:
                raise Denied('relative path %r after an unresolvable cd' % text)
            text = os.path.join(cwd, text)
        return os.path.normpath(text)

    def protected_hit(self, path):
        for root, label in self.protected:
            if under(path, root):
                return label
        return None

    def marker_hit(self, path):
        comps = path.split('/')
        for c in comps:
            if c in MARKER_DIRS:
                return '/%s/' % c
        if comps[-1].endswith(MARKER_SUFFIXES):
            return comps[-1]
        return None

    def in_tmp(self, real):
        return any(under(real, t) for t in self.tmp_roots)

    def check_edit_path(self, path, cwd):
        lex = self.absolute(self.expand(path), cwd)
        real = os.path.realpath(lex)
        for p in (lex, real):
            hit = self.protected_hit(p)
            if hit:
                raise Denied('%s: %s is under protected live path %s' % (self.role, path, hit))
        for p in (lex, real):
            hit = self.marker_hit(p)
            if hit:
                raise Denied('%s: %s touches studio record path (%s); coordinator only' % (self.role, path, hit))
        if under(real, self.studio):
            rel = os.path.relpath(real, self.studio)
            if self.role != 'reviewer' and self.role_allows(rel):
                return
            raise Denied('%s may not write studio path %s' % (self.role, rel))
        if self.in_tmp(real):
            return
        raise Denied('%s may only write %s; %s is outside' % (
            self.role, '/tmp/' if self.role == 'reviewer' else 'its studio paths or /tmp/', real))

    @staticmethod
    def _reserved(pid):
        return any(fnmatch.fnmatchcase(pid, x) for x in EXCLUDED_PRESETS)

    def preset_ok(self, pid):
        if pid in ('', '.', '..') or self._reserved(pid):
            return False
        return self.preset is None or pid == self.preset

    ROLE_AREAS = {'designer': ('identity',), 'builder-desktop': ('desktop', 'artwork'),
                  'builder-apps': ('applications',), 'builder-boot': ('system',)}
    INTEGRATOR_AREAS = ('contributions', 'generated', 'runtime-quality', 'tests')

    def area_root(self, parts):
        """True when parts name the top directory of an area this role owns (e.g.
        presets/<id>/desktop for builder-desktop). The directory itself is not
        'inside' the area: it may be created or written into, never deleted."""
        if self.role == 'reviewer':
            return False
        if self.role == 'integrator':
            return (len(parts) == 3 and parts[:2] == ['refinements', 'cinnamon-current-collection'] and
                    parts[2] in self.INTEGRATOR_AREAS) or parts == ['repairs', 'nim-nitch', 'catalog']
        if len(parts) != 3 or parts[0] != 'presets' or not self.preset_ok(parts[1]):
            return False
        return self.role == 'builder' or parts[2] in self.ROLE_AREAS[self.role]

    def bash_role_allows(self, rel, mode, within, exists):
        """Role gate for Bash write/delete targets inside the studio."""
        if self.role == 'reviewer':
            return False
        parts = rel.split('/')
        if self.role_allows(rel):
            return True
        if mode == 'destroy' and not within:
            return False  # rm/mv of an area root, a whole preset, or anything above
        if self.area_root(parts):
            return True
        if len(parts) == 2 and parts[0] == 'presets' and self.preset_ok(parts[1]) and \
                self.role in ('designer', 'builder'):
            if within and self.role == 'builder':
                return True  # find presets/<id> ... -delete: entries below; records checked by walk
            return mode != 'destroy' and not exists  # creating a brand-new preset folder
        return False

    def role_allows(self, rel):
        parts = rel.split('/')
        if self.role == 'integrator':
            if rel == 'refinements/cinnamon-current-collection/profiles.json':
                return True
            if len(parts) >= 4 and parts[:2] == ['refinements', 'cinnamon-current-collection'] and \
                    parts[2] in ('contributions', 'generated', 'runtime-quality', 'tests'):
                return True
            return len(parts) >= 4 and parts[:3] == ['repairs', 'nim-nitch', 'catalog']
        if len(parts) < 3 or parts[0] != 'presets':
            return False
        pid = parts[1]
        if not self.preset_ok(pid):
            return False
        sub = parts[2:]
        if self.role == 'designer':
            return (len(sub) == 1 and sub[0] in ('design.json', 'BRIEF.md', 'README.md')) or \
                (len(sub) >= 2 and sub[0] == 'identity')
        if self.role == 'builder':
            return True
        allowed = {'builder-desktop': ('desktop', 'artwork'), 'builder-apps': ('applications',),
                   'builder-boot': ('system',)}[self.role]
        return len(sub) >= 2 and sub[0] in allowed

    def check_write_target(self, arg, cwd, mode, why):
        """mode: 'file' (create/overwrite), 'tree' (recursive into), 'destroy' (remove/move away)."""
        if arg is None or arg.text is None:
            raise Denied('%s %s cannot be resolved statically' % (why, arg.raw if arg else '<runtime value>'))
        texts = brace_expand(arg.text) if arg.brace else [arg.text]
        for t in texts:
            if DEV_OK.match(t):
                continue
            p = self.absolute(self.expand(t), cwd)
            if arg.glob and re.search(r'[*?\[]', p):
                self._check_glob(p, mode, why, arg.raw)
                for m in globmod.glob(p):
                    self._check_resolved(os.path.normpath(m), mode, why, arg.raw, arg.within)
                continue
            self._check_resolved(p, mode, why, arg.raw, arg.within)

    def _check_glob(self, pattern, mode, why, raw):
        pc = pattern.split('/')
        literal = []
        for c in pc:
            if re.search(r'[*?\[]', c):
                break
            literal.append(c)
        prefix = '/'.join(literal) or '/'
        hit = self.protected_hit(prefix)
        if hit:
            raise Denied('%s %s is under protected live path %s' % (why, raw, hit))
        for root, label in self.protected + ([(self.studio, 'the studio root')] if mode == 'destroy' else []):
            rc = root.split('/')
            k = min(len(rc), len(pc))
            if all(fnmatch.fnmatchcase(rc[i], pc[i]) for i in range(k)):
                if len(pc) >= len(rc) or mode in ('tree', 'destroy'):
                    raise Denied('%s glob %s can match protected path %s' % (why, raw, label))

    def _check_resolved(self, p, mode, why, raw, within=False):
        real = os.path.realpath(p)
        for q in (p, real):
            hit = self.protected_hit(q)
            if hit:
                raise Denied('%s %s is under protected live path %s' % (why, raw, hit))
            if mode in ('tree', 'destroy'):
                roots = self.tree_protected if mode == 'destroy' else self.protected
                for root, label in roots:
                    if under(root, q):
                        raise Denied('%s %s contains protected path %s' % (why, raw, label))
            hit = self.marker_hit(q)
            if hit:
                raise Denied('%s %s touches studio record path (%s)' % (why, raw, hit))
        if under(real, self.studio):
            rel = os.path.relpath(real, self.studio)
            if not self.bash_role_allows(rel, mode, within, os.path.lexists(real)):
                if self.role == 'reviewer':
                    raise Denied('reviewer is read-only; %s target %s is inside the studio' % (why, raw))
                scope = ' (preset %s only)' % self.preset if self.preset else ''
                verb = 'delete/move' if mode == 'destroy' else 'write'
                raise Denied('%s may not %s studio path %s%s (%s %s)' % (self.role, verb, rel, scope, why, raw))
        elif self.role == 'reviewer' and not self.in_tmp(real) and not DEV_OK.match(real):
            raise Denied('reviewer is read-only; %s target %s is outside /tmp' % (why, raw))
        if mode == 'destroy' and os.path.isdir(real) and not os.path.islink(p):
            count = 0
            for dirpath, dirnames, filenames in os.walk(real):
                count += len(dirnames) + len(filenames)
                if count > MAX_WALK:
                    raise Denied('%s %s is too large to verify' % (why, raw))
                for name in dirnames + filenames:
                    if name in MARKER_DIRS or name.endswith(MARKER_SUFFIXES):
                        raise Denied('%s %s contains studio record %s' % (
                            why, raw, os.path.relpath(os.path.join(dirpath, name), real)))

    # ---------------------------------------------------------------- inline code
    def fuzzy(self, text, where):
        norm = re.sub(r'[\'"`,()\[\]{}+;]', ' ', text)
        norm = re.sub(r'\s+', ' ', norm)
        for label, rx in FUZZY:
            if rx.search(norm) or rx.search(text):
                raise Denied('%s contains %s (inline-code scan)' % (where, label))
        if WRITE_INTENT.search(text):
            for label, rx in self.text_protected:
                if rx.search(text):
                    raise Denied('%s writes and mentions protected path %s (inline-code scan)' % (where, label))

    def scan_script_file(self, arg, cwd, lang):
        """Scan agent-writable scripts that live outside the studio (e.g. /tmp payloads)."""
        if arg.text is None:
            raise Denied('script path %s cannot be resolved statically' % arg.raw)
        try:
            p = os.path.realpath(self.absolute(self.expand(arg.text), cwd))
        except Denied:
            raise
        if under(p, '/dev') or under(p, '/proc'):
            raise Denied('script read from %s; cannot inspect it' % arg.raw)
        if under(p, self.studio):
            return
        if not os.path.lexists(p):
            raise Denied('%s does not exist yet (created in this command?); run it separately so it can be inspected' % arg.raw)
        if not os.path.isfile(p):
            return
        if any(under(p, r) for r in ('/usr', '/bin', '/sbin', '/lib', '/opt', '/snap')):
            return
        try:
            with open(p, 'rb') as fh:
                data = fh.read(MAX_SCRIPT_BYTES + 1)
        except OSError:
            return
        if data.startswith(b'\x7fELF'):
            raise Denied('refusing to run unreviewed binary %s outside the studio' % arg.raw)
        if len(data) > MAX_SCRIPT_BYTES:
            raise Denied('script %s is too large to scan' % arg.raw)
        self.fuzzy(data.decode('utf-8', 'replace'), 'script %s' % arg.raw)

    def script_mentions_commit(self, arg, cwd):
        try:
            p = os.path.realpath(self.absolute(self.expand(arg.text), cwd))
            with open(p, 'r', encoding='utf-8', errors='replace') as fh:
                text = fh.read(MAX_SCRIPT_BYTES)
            if '--commit' in text:
                return True
            # Phase 6 lineage shims (repairs/lineage-lib-20260923) exec lib/lineage/<module> from the
            # studio root; the real argument handling, and any --commit, lives in that module.
            for module in LINEAGE_SHIM_RE.findall(text):
                if not LINEAGE_MODULE_RE.fullmatch(module):
                    return True
                with open(os.path.join(self.studio, 'lib', 'lineage', module), 'r',
                          encoding='utf-8', errors='replace') as fh:
                    if '--commit' in fh.read(MAX_SCRIPT_BYTES):
                        return True
            return False
        except (OSError, Denied):
            return True

    # ---------------------------------------------------------------- bash
    def check_bash(self, command, cwd):
        env = dict(self.environ)
        env['PWD'] = cwd
        self.analyze(command, cwd, env, 0)

    def analyze(self, text, cwd, env, depth):
        if depth > MAX_DEPTH:
            raise Denied('command nesting too deep to verify')
        try:
            items = parse(lex(text))
        except ParseError as exc:
            raise Denied('could not parse command (%s); failing closed' % exc)
        stack = []
        for item in items:
            if item[0] == 'push':
                stack.append((cwd, dict(env)))
            elif item[0] == 'pop':
                if stack:
                    cwd, env = stack.pop()
            else:
                cwd = self.simple(item[1], cwd, env, depth)
        return cwd

    MKTEMP_RE = re.compile(r'^\s*mktemp((?:\s+(?:-d|-u|-q|-t|--directory|--dry-run|--quiet|--suffix=\S+|-p\s+\S+|--tmpdir(?:=\S+)?|[^\s-]\S*))*)\s*$')

    def mktemp_value(self, word, env, cwd):
        """NAME=$(mktemp ...) -> a representative path, so later writes to $NAME can be checked."""
        subs = [p for p in word.parts if p[0] == 'sub']
        if len(subs) != 1 or len(word.parts) != 2 or word.parts[1][0] != 'sub':
            return None
        m = self.MKTEMP_RE.match(subs[0][1])
        if not m:
            return None
        toks = m.group(1).split()
        tmpdir = env.get('TMPDIR') or '/tmp'
        base_dir = None
        template = None
        use_tmp = False
        i = 0
        while i < len(toks):
            t = toks[i]
            if t == '-p':
                base_dir = toks[i + 1]; i += 2; continue
            if t.startswith('--tmpdir'):
                base_dir = t.split('=', 1)[1] if '=' in t else tmpdir; i += 1; continue
            if t == '-t':
                use_tmp = True
            elif not t.startswith('-'):
                template = t
            i += 1
        if template is None:
            path = os.path.join(base_dir or tmpdir, 'tmp.XXXXXXXXXX')
        elif '/' in template and not (base_dir or use_tmp):
            path = template
        elif base_dir or use_tmp:
            path = os.path.join(base_dir or tmpdir, os.path.basename(template))
        else:
            path = os.path.join(cwd or '.', template)
        name = word.parts[0][1]
        return name + path

    def resolve(self, word, env):
        out = []
        for kind, val, _q in word.parts:
            if kind == 'lit':
                out.append(val)
            elif kind == 'tilde':
                if val == '':
                    h = env.get('HOME')
                    if h is None:
                        return None
                    out.append(h)
                else:
                    e = os.path.expanduser('~' + val)
                    out.append(e)
            elif kind == 'var':
                v = env.get(val)
                if v is None:
                    return None
                out.append(v)
            else:
                return None
        return ''.join(out)

    def simple(self, cmd, cwd, env, depth):
        for w in cmd.words + [r[2] for r in cmd.redirs if isinstance(r[2], Word)]:
            for sub in w.subs():
                self.analyze(sub, cwd, dict(env), depth + 1)
        stdin = None
        for op, fd, target in cmd.redirs:
            if isinstance(target, dict):
                body = target['body'] or ''
                if not target['quoted']:
                    try:
                        subs = extract_subs(body)
                    except ParseError as exc:
                        raise Denied('could not parse heredoc (%s)' % exc)
                    for sub in subs:
                        self.analyze(sub, cwd, dict(env), depth + 1)
                stdin = ('heredoc', body)
            elif op == '<<<':
                stdin = ('herestring', self.resolve(target, env))
            elif op == '<':
                stdin = ('file', None)
            elif op == '<&' or (op == '>&' and re.fullmatch(r'\d*-?', target.unquoted_literal() or 'x')):
                continue  # fd duplication / close, not a file
            else:
                self.check_write_target(A(self.resolve(target, env), target), cwd, 'file',
                                        'redirection')
        words = list(cmd.words)
        assigns = []
        while words:
            lit = words[0].unquoted_literal()
            if lit == 'function':
                words = words[2:]; continue
            if lit in SKIP_SEGMENT:
                return cwd
            if lit in KEYWORDS or lit == 'time':
                words.pop(0); continue
            first = words[0].parts[0] if words[0].parts else None
            if first and first[0] == 'lit' and not first[2] and ASSIGN_RE.match(first[1]):
                assigns.append(words.pop(0)); continue
            break
        if not words:
            for a in assigns:
                val = self.resolve(a, env)
                if val is None:
                    val = self.mktemp_value(a, env, cwd)
                name = ASSIGN_RE.match(a.parts[0][1]).group(0).rstrip('=+')
                name = re.sub(r'\[.*', '', name)
                env[name] = None if val is None else val.split('=', 1)[1]
                if env[name] is None:
                    env.pop(name)
            return cwd
        args = []
        for w in words:
            txt = self.resolve(w, env)
            if txt is not None and any(p[0] == 'var' and not p[2] for p in w.parts):
                args.extend(A(piece, w) for piece in txt.split())  # unquoted $VAR word-splits
            else:
                args.append(A(txt, w))
        if not args:
            return cwd
        return self.run(args, cwd, env, depth, stdin, cmd.pipe_in)

    def run(self, args, cwd, env, depth, stdin, pipe_in):
        ccwd = cwd
        for _ in range(40):
            if not args:
                return cwd
            head = args[0]
            if head.text is None:
                raise Denied('command name %s is computed at runtime; cannot verify' % head.raw)
            name = head.text
            base = os.path.basename(name)
            if '/' in name:
                try:
                    rp = os.path.realpath(self.absolute(self.expand(name, env), ccwd))
                    if os.path.exists(rp):
                        base = os.path.basename(rp)
                except Denied:
                    pass
            rest = args[1:]
            if base in PRIV:
                raise Denied('%s is not allowed for subagents (privilege/namespace change)' % base)
            if base == 'busybox' and rest:
                args = rest; continue
            if base in ('export', 'declare', 'typeset', 'local', 'readonly'):
                for a in rest:
                    if a.text and ASSIGN_RE.match(a.text):
                        k, _, v = a.text.partition('=')
                        env[k.rstrip('+')] = v
                return cwd
            if base in ('cd', 'pushd'):
                tgt = [a for a in rest if not (a.text or '').startswith('-')]
                if not tgt:
                    return env.get('HOME') or self.home
                if tgt[0].text is None or tgt[0].text == '-':
                    return None
                try:
                    return os.path.normpath(self.absolute(self.expand(tgt[0].text, env), cwd))
                except Denied:
                    return None
            if base == 'popd':
                return None
            if base == 'trap':
                pos = [a for a in rest if not (a.text or '').startswith('-')]
                if len(pos) >= 2:
                    self.analyze(self._need(pos[0], 'trap command'), ccwd, dict(env), depth + 1)
                return cwd
            if base in ('ssh', 'mosh') and len([a for a in rest if not (a.text or '').startswith('-')]) >= 2:
                raise Denied('%s with a remote command can re-enter this machine unguarded' % base)
            if base == 'env':
                args, ccwd, done = self._unwrap_env(rest, ccwd, env, depth)
                if done:
                    return cwd
                continue
            if base in ('nice', 'nohup', 'setsid', 'stdbuf', 'ionice', 'chrt', 'taskset', 'timeout',
                        'unbuffer', 'catchsegv', 'builtin', 'exec', 'time', 'command', 'strace',
                        'ltrace', 'dbus-launch', 'dbus-run-session', 'xvfb-run', 'flock', 'sg',
                        'watch', 'script', 'gtk-launch', 'xargs', 'eval', 'source', '.'):
                res = self._unwrap_simple(base, rest, ccwd, env, depth, stdin, pipe_in)
                if res is None:
                    return cwd
                args = res
                continue
            if base == 'gio' and rest and rest[0].text == 'launch':
                if len(rest) < 2:
                    return cwd
                app = rest[1].text
                args = [A(None if app is None else re.sub(r'\.desktop$', '', os.path.basename(app)), rest[1])] + rest[2:]
                continue
            if PY_RE.match(base):
                kind, val, more = self._py_split(rest)
                if kind == 'code':
                    self.fuzzy(val, 'python -c code')
                    return cwd
                if kind == 'module':
                    if val is None:
                        raise Denied('python -m with runtime module name')
                    args = [A(val.split('.')[0])] + more
                    continue
                if kind == 'stdin':
                    self._interp_stdin(base, stdin, pipe_in, 'py', ccwd, env, depth)
                    return cwd
                self.scan_script_file(val, ccwd, 'py')
                args = [val] + more
                continue
            if base in SHELLS:
                kind, val, more = self._sh_split(rest)
                if kind == 'code':
                    if val is None:
                        raise Denied('%s -c string is computed at runtime; cannot verify' % base)
                    self.analyze(val, ccwd, dict(env), depth + 1)
                    return cwd
                if kind == 'stdin':
                    self._interp_stdin(base, stdin, pipe_in, 'sh', ccwd, env, depth)
                    return cwd
                self.scan_script_file(val, ccwd, 'sh')
                args = [val] + more
                continue
            if base in OTHER_INTERP:
                res = self._other_interp(base, rest, ccwd, env, depth, stdin, pipe_in)
                if res is None:
                    return cwd
                args = res
                continue
            break
        else:
            raise Denied('too many nested wrappers')
        self.check_program(args, ccwd, env, depth, stdin, pipe_in)
        return cwd

    # -- wrapper helpers
    def _need(self, a, what):
        if a is None or a.text is None:
            raise Denied('%s is computed at runtime; cannot verify' % what)
        return a.text

    def _unwrap_env(self, rest, ccwd, env, depth):
        i = 0
        while i < len(rest):
            t = self._need(rest[i], 'env argument')
            if t == '--':
                i += 1; break
            if t in ('-u', '--unset'):
                i += 2; continue
            if t in ('-C', '--chdir') or t.startswith('--chdir='):
                d = t.split('=', 1)[1] if '=' in t else self._need(rest[i + 1] if i + 1 < len(rest) else None, 'env -C')
                ccwd = os.path.normpath(self.absolute(self.expand(d, env), ccwd))
                i += 1 if '=' in t else 2; continue
            if t in ('-S', '--split-string') or t.startswith('--split-string=') or (t.startswith('-S') and len(t) > 2):
                if t in ('-S', '--split-string'):
                    s = self._need(rest[i + 1] if i + 1 < len(rest) else None, 'env -S'); j = i + 2
                else:
                    s = t.split('=', 1)[1] if t.startswith('--') else t[2:]; j = i + 1
                tail = ' '.join(shlex.quote(self._need(a, 'env -S argument')) for a in rest[j:])
                self.analyze(s + ' ' + tail, ccwd, dict(env), depth + 1)
                return [], ccwd, True
            if t.startswith('-') or ASSIGN_RE.match(t):
                i += 1; continue
            break
        return rest[i:], ccwd, False

    def _skip_opts(self, rest, with_value=(), positional_skip=0):
        i = 0
        while i < len(rest):
            t = self._need(rest[i], 'wrapper option')
            if t == '--':
                i += 1; break
            if t.startswith('-') and len(t) > 1:
                if t in with_value:
                    i += 2
                else:
                    i += 1
                continue
            break
        return rest[i + positional_skip:]

    def _unwrap_simple(self, base, rest, ccwd, env, depth, stdin, pipe_in):
        if base in ('nohup', 'setsid', 'unbuffer', 'catchsegv', 'builtin', 'time', 'dbus-launch',
                    'dbus-run-session', 'gtk-launch'):
            out = self._skip_opts(rest, {'-f', '-o', '--config-file', '--dbus-daemon'})
            if base == 'time':
                for k, a in enumerate(rest):
                    if a.text in ('-o', '--output') and k + 1 < len(rest):
                        self.check_write_target(rest[k + 1], ccwd, 'file', 'time -o')
            if base == 'gtk-launch' and out:
                out = [A(None if out[0].text is None else re.sub(r'\.desktop$', '', out[0].text), out[0].word)] + out[1:]
            return out
        if base == 'exec':
            return self._skip_opts(rest, {'-a'})
        if base == 'command':
            if any(a.text in ('-v', '-V') for a in rest[:2]):
                return None
            return self._skip_opts(rest)
        if base == 'nice':
            return self._skip_opts(rest, {'-n', '--adjustment'})
        if base == 'stdbuf':
            return self._skip_opts(rest, {'-i', '-o', '-e', '--input', '--output', '--error'})
        if base == 'ionice':
            return self._skip_opts(rest, {'-c', '-n', '--class', '--classdata'})
        if base == 'chrt':
            return self._skip_opts(rest, {'-T', '-P', '-D'}, positional_skip=1)
        if base == 'taskset':
            return self._skip_opts(rest, {'-c', '--cpu-list'}, positional_skip=0 if any(
                a.text in ('-c', '--cpu-list') for a in rest) else 1)
        if base == 'timeout':
            return self._skip_opts(rest, {'-s', '--signal', '-k', '--kill-after'}, positional_skip=1)
        if base in ('strace', 'ltrace'):
            for k, a in enumerate(rest):
                if a.text in ('-o',) and k + 1 < len(rest):
                    self.check_write_target(rest[k + 1], ccwd, 'file', base + ' -o')
            return self._skip_opts(rest, {'-o', '-e', '-p', '-s', '-u', '-E', '-a', '-b', '-I', '-O', '-S', '-X', '-P', '-n'})
        if base == 'xvfb-run':
            return self._skip_opts(rest, {'-n', '-s', '-f', '-e', '-p', '-w', '--server-num', '--server-args'})
        if base == 'flock':
            i = 0
            while i < len(rest):
                t = self._need(rest[i], 'flock argument')
                if t in ('-c', '--command'):
                    s = self._need(rest[i + 1] if i + 1 < len(rest) else None, 'flock -c')
                    self.analyze(s, ccwd, dict(env), depth + 1)
                    return None
                if t in ('-w', '--timeout', '-E', '--conflict-exit-code'):
                    i += 2; continue
                if t.startswith('-'):
                    i += 1; continue
                i += 1  # lock file
                if i < len(rest) and rest[i].text in ('-c', '--command'):
                    s = self._need(rest[i + 1] if i + 1 < len(rest) else None, 'flock -c')
                    self.analyze(s, ccwd, dict(env), depth + 1)
                    return None
                return rest[i:]
            return None
        if base in ('sg', 'script', 'watch'):
            i = 0
            is_exec = False
            while i < len(rest):
                t = self._need(rest[i], base + ' argument')
                if t in ('-c', '--command'):
                    s = self._need(rest[i + 1] if i + 1 < len(rest) else None, base + ' -c')
                    self.analyze(s, ccwd, dict(env), depth + 1)
                    if base == 'script':
                        for a in rest[i + 2:]:
                            if not (a.text or '').startswith('-'):
                                self.check_write_target(a, ccwd, 'file', 'script typescript')
                    return None
                if t in ('-x', '--exec'):
                    is_exec = True; i += 1; continue
                if t in ('-n', '--interval', '-q', '-T', '--timing', '-O', '-I', '-B'):
                    i += 2 if t not in ('-q',) else 1; continue
                if t.startswith('-'):
                    i += 1; continue
                break
            tail = rest[i + (1 if base == 'sg' else 0):]
            if base == 'script':
                for a in tail:
                    self.check_write_target(a, ccwd, 'file', 'script typescript')
                return None
            if base == 'watch' and not is_exec:
                s = ' '.join(self._need(a, 'watch command') for a in tail)
                self.analyze(s, ccwd, dict(env), depth + 1)
                return None
            return tail
        if base == 'eval':
            s = ' '.join(self._need(a, 'eval argument') for a in rest)
            self.analyze(s, ccwd, dict(env), depth + 1)
            return None
        if base in ('source', '.'):
            if not rest:
                return None
            self.scan_script_file(rest[0], ccwd, 'sh')
            return rest
        if base == 'xargs':
            valued = {'-a', '--arg-file', '-d', '--delimiter', '-E', '-e', '-I', '-L', '-l', '-n',
                      '--max-args', '-P', '--max-procs', '-s', '--max-chars', '--process-slot-var'}
            i = 0
            repl = None
            while i < len(rest):
                t = self._need(rest[i], 'xargs option')
                if t == '--':
                    i += 1; break
                if t == '-I' and i + 1 < len(rest):
                    repl = self._need(rest[i + 1], 'xargs -I'); i += 2; continue
                if t.startswith('-I') and len(t) > 2:
                    repl = t[2:]; i += 1; continue
                if t.startswith('--replace'):
                    repl = t.split('=', 1)[1] if '=' in t else '{}'; i += 1; continue
                if t.startswith('-i'):
                    repl = t[2:] or '{}'; i += 1; continue
                if t in valued:
                    i += 2; continue
                if t.startswith('-'):
                    i += 1; continue
                break
            tail = rest[i:] or [A('echo')]
            if repl is not None:
                return [A(None) if repl in (a.text or '') else a for a in tail]
            return tail + [A(None)]
        return rest

    def _py_split(self, rest):
        i = 0
        while i < len(rest):
            t = self._need(rest[i], 'python option')
            if t == '--':
                i += 1; break
            if t == '-':
                return 'stdin', None, rest[i + 1:]
            if t.startswith('--'):
                i += 1; continue
            if t.startswith('-') and len(t) > 1:
                j = 1
                step = 1
                while j < len(t):
                    ch = t[j]
                    if ch in 'cmXW':
                        attached = t[j + 1:]
                        if attached:
                            val = attached
                        else:
                            val = rest[i + 1].text if i + 1 < len(rest) else None
                            step = 2
                        if ch in 'cm':
                            if val is None:
                                raise Denied('python -%s value is computed at runtime' % ch)
                            return ('code' if ch == 'c' else 'module'), val, rest[i + step:]
                        break
                    j += 1
                i += step
                continue
            break
        if i < len(rest):
            return 'script', rest[i], rest[i + 1:]
        return 'stdin', None, []

    def _sh_split(self, rest):
        i = 0
        has_c = has_s = False
        while i < len(rest):
            t = self._need(rest[i], 'shell option')
            if t in ('--', '-'):
                i += 1; break
            if t in ('-o', '+o', '-O', '+O', '--rcfile', '--init-file'):
                i += 2; continue
            if t.startswith('--'):
                i += 1; continue
            if t[0] in '-+' and len(t) > 1:
                if t[0] == '-' and 'c' in t[1:]:
                    has_c = True
                if t[0] == '-' and 's' in t[1:]:
                    has_s = True
                i += 1; continue
            break
        if has_c:
            return 'code', (rest[i].text if i < len(rest) else ''), []
        if has_s or i >= len(rest):
            return 'stdin', None, []
        return 'script', rest[i], rest[i + 1:]

    def _interp_stdin(self, base, stdin, pipe_in, lang, ccwd, env, depth):
        if stdin and stdin[0] in ('heredoc', 'herestring'):
            body = stdin[1]
            if body is None:
                raise Denied('%s input is computed at runtime; cannot verify' % base)
            if lang == 'sh':
                self.analyze(body, ccwd, dict(env), depth + 1)
            else:
                self.fuzzy(body, '%s heredoc code' % base)
            return
        src = 'a pipe' if pipe_in else ('a redirected file' if stdin else 'stdin')
        raise Denied('%s would read its program from %s; cannot inspect it' % (base, src))

    def _other_interp(self, base, rest, ccwd, env, depth, stdin, pipe_in):
        code_flags = {'-e', '-E', '--eval', '-p', '--print', '-r', '-c'}
        codes = []
        inplace = False
        i = 0
        pos = []
        while i < len(rest):
            t = self._need(rest[i], base + ' option')
            if t == '--':
                pos.extend(rest[i + 1:]); break
            if t in code_flags:
                if i + 1 >= len(rest):
                    break
                codes.append(self._need(rest[i + 1], base + ' code')); i += 2; continue
            if base == 'perl' and t.startswith('-') and not t.startswith('--'):
                flags = t[1:]
                if 'i' in flags:
                    inplace = True
                m = re.search(r'[eE]', flags)
                if m:
                    attached = flags[m.end():]
                    if attached:
                        codes.append(attached); i += 1
                    else:
                        codes.append(self._need(rest[i + 1] if i + 1 < len(rest) else None, 'perl -e code')); i += 2
                    continue
                i += 1; continue
            if t.startswith('-'):
                i += 1; continue
            pos.append(rest[i]); i += 1
        for c in codes:
            self.fuzzy(c, '%s inline code' % base)
        if codes:
            if inplace:
                for a in pos:
                    self.check_write_target(a, ccwd, 'file', 'perl -i')
            return None
        if not pos:
            self._interp_stdin(base, stdin, pipe_in, 'other', ccwd, env, depth)
            return None
        self.scan_script_file(pos[0], ccwd, base)
        if inplace:
            for a in pos[1:]:
                self.check_write_target(a, ccwd, 'file', 'perl -i')
        return pos

    # -- program rules
    def check_program(self, args, cwd, env, depth, stdin, pipe_in):
        name = args[0].text
        base = os.path.basename(name)
        rest = args[1:]
        texts = [a.text for a in rest]
        pos = [a for a in rest if a.text is None or not a.text.startswith('-') or a.text == '-']
        first = pos[0].text if pos else ''
        real = None
        if '/' in name:
            try:
                real = os.path.realpath(self.absolute(self.expand(name, env), cwd))
            except Denied:
                real = None
        elif base == 'theme':
            found = shutil.which('theme', path=env.get('PATH') or self.environ.get('PATH'))
            real = os.path.realpath(found) if found else None
        real_base = os.path.basename(real) if real else base
        orig_base = base
        if real and os.path.exists(real) and real_base != 'shortcut.py':
            base = real_base
        if base.split('.')[0] in EDITORS:
            base = base.split('.')[0]

        def deny(msg):
            raise Denied('%s: %s' % (base, msg))

        if base not in READONLY:
            for t in texts:
                if t and COMMIT_RE.match(t):
                    deny('--commit (or an abbreviation) is coordinator-only')
            if cwd is not None and base not in ('git', 'find', 'cd', 'pwd') and (
                    self.protected_hit(cwd) or self.protected_hit(os.path.realpath(cwd))):
                deny('refusing to run a program with live directory %s as its working directory' % cwd)
            for k, a in enumerate(rest):
                m = OUTPUT_FLAG_RE.match(a.text or '')
                if m:
                    if m.group(2) is not None:
                        tgt = A(m.group(2), a.word)
                    else:
                        tgt = rest[k + 1] if k + 1 < len(rest) else None
                    if tgt is not None and tgt.text is not None and not tgt.text.startswith('-'):
                        self.check_write_target(tgt, cwd, 'tree', '%s %s' % (base, m.group(1)))
        if base in PRIV:
            deny('privilege escalation is not allowed')
        if base == 'gsettings':
            if any(t is None or t in ('set', 'reset', 'reset-recursively') for t in texts):
                deny('gsettings set/reset changes the live desktop')
            return
        if base == 'dconf':
            if any(t is None or t in ('write', 'reset', 'load', 'update') for t in texts):
                deny('dconf write/reset/load changes the live desktop')
            return
        if base in POWER:
            deny('session/service control is coordinator-only')
        if base == 'loginctl' and not re.match(r'^(show|list|session-status|user-status|seat-status)', first or 'list'):
            deny('loginctl session control is coordinator-only')
        if base in WM_REPLACE or base.startswith('cinnamon-launcher'):
            if not rest or any(t is None or t in ('--replace', '-r') for t in texts):
                deny('starting/replacing the window manager or shell is not allowed')
        if base in KILLERS or (base == 'fuser' and any(t and t.startswith('-') and 'k' in t for t in texts)):
            deny('process kills are not allowed')
        if base in BOOT_TOOLS or base == 'plymouth' and rest:
            deny('boot/initramfs changes are coordinator-only')
        if base in INPUT_AUTOMATION:
            deny('input automation drives the live desktop')
        if base == 'wmctrl' and any(t is None or t not in ('-l', '-m', '-d', '-p', '-G', '-x', '-u') for t in texts):
            deny('wmctrl window actions drive the live desktop')
        if base.startswith('cinnamon-settings') or base in ('dconf-editor', 'gnome-tweaks', 'cinnamon-menu-editor'):
            deny('settings GUIs write the live desktop')
        if base == 'eww' and any(t in ('open', 'close', 'update', 'reload', 'kill', 'daemon', 'open-many', 'close-all') or t is None for t in texts):
            deny('eww widget changes are live')
        if base == 'nemo' and any(t in ('-q', '--quit') for t in texts):
            deny('quitting nemo affects the live desktop')
        if base == 'gdbus' and any(t in ('call', 'emit') or t is None for t in texts):
            deny('D-Bus method calls can change the live session')
        if base == 'dbus-send':
            deny('D-Bus method calls can change the live session')
        if base == 'busctl' and any(t in ('call', 'set-property', 'emit') or t is None for t in texts):
            deny('D-Bus method calls can change the live session')
        if base in ('qdbus', 'qdbus6') and len(pos) >= 3:
            deny('D-Bus method calls can change the live session')
        if base == 'xdg-settings' and first == 'set' or base == 'xdg-mime' and first == 'default':
            deny('changes desktop defaults')
        if base in ('xdg-desktop-menu', 'xdg-icon-resource', 'xdg-desktop-icon') and first in ('install', 'uninstall', 'forceupdate'):
            deny('installs into ~/.local/share')
        if base == 'xrandr' and any(t is None or t not in ('-q', '--query', '--listmonitors', '--listactivemonitors', '--current', '--verbose', '--prop', '--props') for t in texts):
            deny('display changes are live')
        if base == 'xset' and any(t is None or t != 'q' for t in texts):
            deny('X settings changes are live')
        if base == 'setxkbmap' and any(t is None or t not in ('-query', '-print', '-v') for t in texts):
            deny('keyboard map changes are live')
        if base == 'xinput' and re.match(r'^(set-|enable|disable|float|reattach|create-master|remove-master|map-to-output)', first or ''):
            deny('input device changes are live')
        if base in ('feh', 'nitrogen') and any(t and (t.startswith('--bg') or t in ('--set-zoom-fill', '--restore') or t.startswith('--set')) for t in texts):
            deny('wallpaper changes are live')
        if base == 'crontab' and texts != ['-l'] or base in ('at', 'batch'):
            deny('scheduling persistent jobs is not allowed')
        if base in ('apt', 'apt-get', 'aptitude', 'snap', 'flatpak') and first in (
                'install', 'remove', 'purge', 'upgrade', 'dist-upgrade', 'full-upgrade', 'autoremove',
                'refresh', 'uninstall', 'update', 'override', 'remote-add', 'remote-delete', 'reinstall', None):
            deny('package changes alter the live system')
        if base == 'dpkg' and any(t is None or re.match(r'^(-[irP]|--(install|remove|purge|configure|unpack))', t) for t in texts):
            deny('package changes alter the live system')
        if base in ('pip', 'pip3') and first in ('install', 'uninstall', None) and not any(
                t and (t in ('-t', '--target') or t.startswith('--target=')) for t in texts):
            deny('pip install writes ~/.local (use --target into the preset or /tmp)')
        if base in ('pipx', 'gem') and first in ('install', 'uninstall', 'upgrade', 'update', None):
            deny('installs into ~/.local')
        if base == 'npm' and any(t in ('-g', '--global') for t in texts):
            deny('global npm install writes outside the studio')

        # studio / collection live scripts
        rule = LIVE_SCRIPTS.get(real_base, LIVE_SCRIPTS.get(base, False))
        if base == 'theme' or (real and real == self.shortcut_real) or real_base == 'shortcut.py':
            if rest:
                deny('the theme shortcut switches the live desktop')
        elif rule is None:
            deny('is a live-apply/isolation script; coordinator only')
        elif rule is not False:
            if rule == 'ANYARG':
                if rest:
                    deny('live shortcut')
            elif first is None or first in rule:
                deny('%s is a live action; coordinator only' % (first or '<runtime value>'))
        if any(fnmatch.fnmatchcase(real_base, g) or fnmatch.fnmatchcase(base, g) for g in LIVE_SCRIPT_GLOBS):
            deny('drives or verifies the live session; coordinator only, one at a time')
        if base == 'apply.sh' and any(t is None for t in texts):
            deny('arguments computed at runtime could include --commit')
        if (real_base.endswith(('.py', '.sh')) or base.endswith(('.py', '.sh'))) and any(t is None for t in texts):
            if self.script_mentions_commit(args[0], cwd):
                deny('arguments computed at runtime could include --commit')

        # direct execution of a script path: scan payloads outside the studio
        if '/' in name and not PY_RE.match(base):
            self.scan_script_file(args[0], cwd, 'exec')

        self.check_writers(base, rest, cwd, env, depth, stdin, pipe_in)
        if orig_base != base:
            self.check_writers(orig_base, rest, cwd, env, depth, stdin, pipe_in)

    def check_writers(self, base, rest, cwd, env, depth, stdin, pipe_in):
        W = self.check_write_target

        def split(with_value=()):
            pos, opts = [], []
            i = 0
            end = False
            while i < len(rest):
                a = rest[i]
                t = a.text
                if end or t is None or not t.startswith('-') or t == '-':
                    pos.append(a); i += 1; continue
                if t == '--':
                    end = True; i += 1; continue
                if t.startswith('--'):
                    nm, eq, val = t.partition('=')
                    if not eq and nm in with_value:
                        opts.append((nm, rest[i + 1] if i + 1 < len(rest) else A(None))); i += 2; continue
                    opts.append((nm, A(val) if eq else None)); i += 1; continue
                j = 1
                step = 1
                while j < len(t):
                    ch = '-' + t[j]
                    if ch in with_value:
                        v = t[j + 1:]
                        if v:
                            opts.append((ch, A(v)))
                        else:
                            opts.append((ch, rest[i + 1] if i + 1 < len(rest) else A(None))); step = 2
                        break
                    opts.append((ch, None)); j += 1
                i += step
            return pos, opts

        def has(opts, *names):
            return any(o[0] in names for o in opts)

        def val(opts, *names):
            for o in opts:
                if o[0] in names:
                    return o[1]
            return None

        if base in ('cp', 'mv', 'ln', 'install'):
            pos, opts = split({'-t', '--target-directory', '-S', '--suffix', '-m', '--mode', '-o',
                               '--owner', '-g', '--group'} if base == 'install' else
                              {'-t', '--target-directory', '-S', '--suffix'})
            tdir = val(opts, '-t', '--target-directory')
            rec = has(opts, '-r', '-R', '-a', '--recursive', '--archive')
            if base == 'install' and has(opts, '-d', '--directory'):
                for a in pos:
                    W(a, cwd, 'file', 'install -d')
                return
            if base == 'mv':
                srcs = pos if tdir else pos[:-1]
                for a in srcs:
                    W(a, cwd, 'destroy', 'mv source')
                dest = tdir or (pos[-1] if pos else None)
                if dest is not None:
                    W(dest, cwd, 'tree', 'mv destination')
                return
            if tdir is not None:
                W(tdir, cwd, 'tree' if rec else 'file', base + ' destination')
            elif len(pos) >= 2:
                W(pos[-1], cwd, 'tree' if rec else 'file', base + ' destination')
            elif len(pos) == 1 and base == 'ln' and pos[0].text:
                W(A(os.path.join(cwd or '.', os.path.basename(pos[0].text.rstrip('/')))), cwd, 'file', 'ln link')
            return
        if base == 'rsync':
            pos, opts = split({'-e', '--rsh', '-f', '--filter', '--exclude', '--include', '--exclude-from',
                               '--include-from', '--files-from', '-T', '--temp-dir', '--log-file',
                               '--backup-dir', '--partial-dir', '--compare-dest', '--link-dest',
                               '--copy-dest', '--chmod', '--chown', '-M', '--password-file'})
            if len(pos) < 2:
                return
            dest = pos[-1]
            if dest.text and re.match(r'^[^/.~$][^/]*:', dest.text):
                return
            mode = 'destroy' if any(o[0].startswith('--del') for o in opts) else 'tree'
            W(dest, cwd, mode, 'rsync destination')
            if has(opts, '--remove-source-files'):
                for a in pos[:-1]:
                    W(a, cwd, 'destroy', 'rsync source')
            lf = val(opts, '--log-file')
            if lf is not None:
                W(lf, cwd, 'file', 'rsync --log-file')
            return
        if base == 'tee':
            pos, _ = split()
            for a in pos:
                if a.text != '-':
                    W(a, cwd, 'file', 'tee')
            return
        if base == 'dd':
            for a in rest:
                if a.text is None:
                    raise Denied('dd operand computed at runtime')
                if a.text.startswith('of='):
                    W(A(a.text[3:], a.word), cwd, 'file', 'dd of=')
            return
        if base in ('touch', 'mkdir', 'truncate', 'mkfifo', 'mknod'):
            pos, _ = split({'-r', '--reference', '-d', '--date', '-t', '-m', '--mode', '-s', '--size', '--context'})
            for a in pos:
                W(a, cwd, 'file', base)
            return
        if base in ('rm', 'rmdir', 'unlink', 'shred', 'trash-put', 'trash', 'gvfs-trash', 'srm'):
            pos, _ = split({'-n', '--iterations', '-s', '--size', '--random-source'})
            for a in pos:
                W(a, cwd, 'destroy', base)
            return
        if base in ('chmod', 'chown', 'chgrp', 'chattr', 'setfacl', 'setfattr'):
            pos, opts = split({'-m', '-x', '-n', '-v', '-M', '-X'})
            rec = has(opts, '-R', '--recursive')
            targets = pos if has(opts, '--reference') or base in ('setfacl', 'setfattr') else pos[1:]
            for a in targets:
                W(a, cwd, 'tree' if rec else 'file', base)
            return
        if base == 'sed':
            self._sed(rest, cwd)
            return
        if base in AWKS:
            pos, opts = split({'-f', '--file', '-v', '--assign', '-F', '--field-separator', '-i', '--include', '-e', '--source'})
            prog_given = has(opts, '-f', '--file', '-e', '--source')
            for o in opts:
                if o[0] in ('-e', '--source') and o[1] is not None:
                    self.fuzzy(self._need(o[1], 'awk program'), 'awk program')
            if not prog_given and pos:
                self.fuzzy(self._need(pos[0], 'awk program'), 'awk program')
            inplace = any(o[0] in ('-i', '--include') and o[1] is not None and o[1].text == 'inplace' for o in opts)
            if inplace:
                for a in (pos if prog_given else pos[1:]):
                    W(a, cwd, 'file', 'awk -i inplace')
            return
        if base == 'find':
            self._find(rest, cwd, env, depth, stdin, pipe_in)
            return
        if base == 'tar':
            self._tar(rest, cwd)
            return
        if base in ('unzip', '7z', '7za', 'bsdtar'):
            if base == 'unzip':
                pos, opts = split({'-d', '-x'})
                if has(opts, '-l', '-t', '-v', '-p', '-Z', '-z'):
                    return
                d = val(opts, '-d')
                W(d if d is not None else A(cwd), cwd, 'tree', 'unzip destination')
            else:
                for a in rest:
                    if a.text and a.text.startswith('-o'):
                        W(A(a.text[2:], a.word), cwd, 'tree', base + ' output')
                if rest and rest[0].text in ('x', 'e') and not any((a.text or '').startswith('-o') for a in rest):
                    W(A(cwd), cwd, 'tree', base + ' output')
            return
        if base == 'git':
            self._git(rest, cwd)
            return
        if base in ('curl', 'wget'):
            pos, opts = split({'-o', '--output', '-O', '--output-document', '-P', '--directory-prefix',
                               '--output-dir', '-H', '--header', '-d', '--data', '-u', '--user', '-X',
                               '-A', '-e', '-T', '-F', '-b', '-c', '--cookie-jar'} - ({'-O'} if base == 'curl' else set()))
            for o in opts:
                if o[0] in ('-o', '--output', '--output-document', '-c', '--cookie-jar') or (base == 'wget' and o[0] == '-O'):
                    if o[1] is not None and o[1].text != '-':
                        W(o[1], cwd, 'file', base + ' output')
                if o[0] in ('-P', '--directory-prefix', '--output-dir'):
                    W(o[1], cwd, 'tree', base + ' output dir')
            if base == 'wget' and not has(opts, '-O', '--output-document', '-P', '--directory-prefix', '--spider'):
                W(A(cwd), cwd, 'file', 'wget download dir')
            if base == 'curl' and has(opts, '-O', '--remote-name', '--remote-name-all') and not has(opts, '--output-dir'):
                W(A(cwd), cwd, 'file', 'curl -O download dir')
            return
        if base == 'gio' and rest:
            sub = rest[0].text
            pos = [a for a in rest[1:] if not (a.text or '').startswith('-')]
            if sub in ('copy',) and len(pos) >= 2:
                W(pos[-1], cwd, 'tree', 'gio copy')
            elif sub in ('move', 'remove', 'trash', 'rename'):
                for a in (pos if sub != 'rename' else pos[:1]):
                    W(a, cwd, 'destroy', 'gio ' + sub)
                if sub == 'move' and pos:
                    W(pos[-1], cwd, 'tree', 'gio move')
            elif sub in ('save', 'set', 'mkdir'):
                for a in (pos if sub == 'mkdir' else pos[:1]):
                    W(a, cwd, 'file', 'gio ' + sub)
            elif sub is None:
                raise Denied('gio subcommand computed at runtime')
            return
        if base in ('gtk-update-icon-cache', 'gtk4-update-icon-cache', 'glib-compile-schemas',
                    'update-mime-database', 'update-desktop-database', 'update-icon-caches', 'fc-cache'):
            pos, opts = split({'--targetdir', '-c', '--index-file'})
            td = val(opts, '--targetdir')
            if td is not None:
                W(td, cwd, 'file', base)
            for a in pos:
                W(a, cwd, 'file', base)
            return
        if base in ('convert', 'magick', 'ffmpeg', 'xcursorgen', 'uniq', 'cpp'):
            pos, _ = split({'-i', '-f', '-c', '-b', '-s', '-r', '-map', '-size', '-density', '-resize',
                            '-background', '-fill', '-font', '-pointsize', '-gravity', '-extent'})
            if len(pos) >= 2:
                W(pos[-1], cwd, 'file', base + ' output')
            return
        if base in ('rsvg-convert', 'inkscape', 'cairosvg', 'pngquant', 'optipng', 'sort', 'patch',
                    'msgfmt', 'glib-compile-resources', 'sassc', 'sass', 'zip', 'pandoc'):
            pos, opts = split({'-o', '--output', '--export-filename', '-out', '--target', '-d', '--directory'})
            for o in opts:
                if o[0] in ('-o', '--output', '--export-filename', '-out', '--target') and o[1] is not None:
                    W(o[1], cwd, 'file', base + ' output')
                if o[0] in ('--export-png', '--export-pdf', '--export-plain-svg') and o[1] is not None:
                    W(o[1], cwd, 'file', base + ' output')
                if base == 'patch' and o[0] in ('-d', '--directory'):
                    W(o[1], cwd, 'tree', 'patch dir')
            if base == 'patch' and not has(opts, '-o', '--output', '--dry-run'):
                W(A(cwd), cwd, 'file', 'patch working dir')
            if base in ('sassc', 'sass') and len(pos) >= 2:
                W(pos[-1], cwd, 'file', base + ' output')
            if base == 'zip' and pos:
                W(pos[0], cwd, 'file', 'zip archive')
            return
        if base in EDITORS:
            pos, _ = split({'-c', '-S', '-u', '-i', '--cmd', '-T'})
            for a in pos:
                W(a, cwd, 'file', base)
            return

    def _sed(self, rest, cwd):
        inplace = False
        scripts = []
        given = False
        pos = []
        i = 0
        end = False
        while i < len(rest):
            a = rest[i]
            t = a.text
            if end or t is None or not t.startswith('-') or t == '-':
                pos.append(a); i += 1; continue
            if t == '--':
                end = True; i += 1; continue
            if t.startswith('--'):
                if t.startswith('--in-place'):
                    inplace = True
                elif t in ('--expression', '--file'):
                    given = True
                    if t == '--expression':
                        scripts.append(self._need(rest[i + 1] if i + 1 < len(rest) else None, 'sed script'))
                    i += 2; continue
                elif t.startswith(('--expression=', '--file=')):
                    given = True
                    if t.startswith('--expression='):
                        scripts.append(t.split('=', 1)[1])
                i += 1; continue
            j = 1
            step = 1
            while j < len(t):
                ch = t[j]
                if ch == 'i':
                    inplace = True; break
                if ch in 'ef':
                    given = True
                    v = t[j + 1:]
                    if not v:
                        v = self._need(rest[i + 1] if i + 1 < len(rest) else None, 'sed script'); step = 2
                    if ch == 'e':
                        scripts.append(v)
                    break
                if ch == 'l':
                    if not t[j + 1:]:
                        step = 2
                    break
                j += 1
            i += step
        if not given:
            if not pos:
                return
            scripts.append(self._need(pos[0], 'sed script'))
            pos = pos[1:]
        for s in scripts:
            self.fuzzy(s, 'sed script')
            for m in re.finditer(r'(?:^|[;\n}])\s*(?:\d+|/[^/]*/|\$)?\s*[wW]\s+(\S+)|s(.)(?:(?!\2).)*\2(?:(?!\2).)*\2[gpiIm0-9]*w\s+(\S+)', s):
                target = m.group(1) or m.group(3)
                if target:
                    self.check_write_target(A(target), cwd, 'file', 'sed w command')
            if re.search(r's(.)(?:(?!\1).)*\1(?:(?!\1).)*\1[gpiIm0-9w]*e|(?:^|[;\n])\s*e\s', s):
                raise Denied('sed script executes commands (e flag)')
        if inplace:
            for a in pos:
                self.check_write_target(a, cwd, 'file', 'sed -i')

    def _find(self, rest, cwd, env, depth, stdin, pipe_in):
        roots = []
        i = 0
        while i < len(rest) and rest[i].text is not None and not re.match(r'^[-(!]', rest[i].text) and rest[i].text not in ('(', '!'):
            roots.append(rest[i]); i += 1
        if i < len(rest) and rest[i].text is None:
            raise Denied('find argument computed at runtime')
        if not roots:
            roots = [A('.')]
        while i < len(rest):
            t = rest[i].text
            if t in ('-exec', '-execdir', '-ok', '-okdir'):
                j = i + 1
                sub = []
                while j < len(rest) and rest[j].text not in (';', '+'):
                    sub.append(rest[j]); j += 1
                if not sub:
                    raise Denied('find -exec without command')
                for r in roots:
                    subst = [A(r.text, r.word, within=True) if (a.text or '').strip() == '{}' else
                             (A(None) if a.text is None or '{}' in a.text else a) for a in sub]
                    self.run(subst, cwd, dict(env), depth + 1, None, False)
                i = j + 1; continue
            if t == '-delete':
                for r in roots:
                    self.check_write_target(A(r.text, r.word, within=True), cwd, 'destroy', 'find -delete')
            if t in ('-fprint', '-fprint0', '-fprintf', '-fls') and i + 1 < len(rest):
                self.check_write_target(rest[i + 1], cwd, 'file', 'find ' + t)
            i += 1

    def _tar(self, rest, cwd):
        toks = [a for a in rest]
        extract = create = False
        target_dir = None
        archive = None
        i = 0
        while i < len(toks):
            t = self._need(toks[i], 'tar argument')
            cluster = None
            if i == 0 and not t.startswith('-'):
                cluster = t
            elif t.startswith('--'):
                nm, eq, v = t.partition('=')
                if nm in ('--extract', '--get'):
                    extract = True
                elif nm in ('--create', '--append', '--update', '--delete'):
                    create = True
                elif nm == '--directory':
                    target_dir = A(v) if eq else (toks[i + 1] if i + 1 < len(toks) else None)
                    i += 0 if eq else 1
                elif nm == '--file':
                    archive = A(v) if eq else (toks[i + 1] if i + 1 < len(toks) else None)
                    i += 0 if eq else 1
                i += 1; continue
            elif t.startswith('-'):
                cluster = t[1:]
            if cluster is None:
                i += 1; continue
            step = 1
            for k, ch in enumerate(cluster):
                if ch == 'x':
                    extract = True
                elif ch in 'cru':
                    create = True
                elif ch in 'fCTXbNgKLV':
                    attached = cluster[k + 1:]
                    v = A(attached) if attached and not (i == 0 and not t.startswith('-')) else None
                    if v is None:
                        v = toks[i + step] if i + step < len(toks) else None
                        step += 1
                    if ch == 'f':
                        archive = v
                    elif ch == 'C':
                        target_dir = v
                    if attached and not (i == 0 and not t.startswith('-')):
                        break
            i += step
        if extract:
            self.check_write_target(target_dir or A(cwd), cwd, 'tree', 'tar extract')
        if create and archive is not None and archive.text != '-':
            self.check_write_target(archive, cwd, 'file', 'tar archive')

    GIT_WRITE = {'clone', 'checkout', 'switch', 'pull', 'reset', 'restore', 'merge', 'rebase',
                 'stash', 'apply', 'am', 'init', 'worktree', 'clean', 'rm', 'mv', 'commit',
                 'cherry-pick', 'revert', 'submodule', 'fetch', 'add', 'gc', 'prune'}

    def _git(self, rest, cwd):
        i = 0
        gdir = cwd
        while i < len(rest):
            t = self._need(rest[i], 'git option')
            if t == '-C':
                d = self._need(rest[i + 1] if i + 1 < len(rest) else None, 'git -C')
                gdir = os.path.normpath(self.absolute(self.expand(d), gdir)); i += 2; continue
            if t in ('-c', '--git-dir', '--work-tree', '--namespace'):
                if t in ('--git-dir', '--work-tree'):
                    self.check_write_target(rest[i + 1] if i + 1 < len(rest) else None, gdir, 'tree', 'git ' + t)
                i += 2; continue
            if t.startswith(('--git-dir=', '--work-tree=')):
                self.check_write_target(A(t.split('=', 1)[1]), gdir, 'tree', 'git ' + t.split('=')[0])
                i += 1; continue
            if t.startswith('-'):
                i += 1; continue
            break
        if i >= len(rest):
            return
        sub = rest[i].text
        if sub not in self.GIT_WRITE:
            return
        if sub == 'clone':
            pos = [a for a in rest[i + 1:] if not (a.text or '').startswith('-')]
            if len(pos) >= 2:
                self.check_write_target(pos[1], gdir, 'tree', 'git clone')
            elif pos and pos[0].text:
                name = re.sub(r'\.git$', '', os.path.basename(pos[0].text.rstrip('/')))
                self.check_write_target(A(os.path.join(gdir or '.', name)), gdir, 'tree', 'git clone')
            return
        if gdir is None:
            raise Denied('git %s after an unresolvable cd' % sub)
        self.check_write_target(A(gdir), gdir, 'file', 'git ' + sub)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description='PreToolUse live-desktop guard for studio subagents')
    ap.add_argument('--role', required=True, choices=ROLES)
    ap.add_argument('--explain', metavar='COMMAND', help='check a Bash command and print ALLOW/DENY')
    ap.add_argument('--explain-write', metavar='PATH', help='check a Write/Edit path and print ALLOW/DENY')
    ap.add_argument('--preset', metavar='ID', help='restrict preset roles to presets/ID only')
    ap.add_argument('--cwd', help='working directory for --explain (default: current)')
    ap.add_argument('--studio-root', help=argparse.SUPPRESS)
    ap.add_argument('--home', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        guard = Guard(args.role, studio_root=args.studio_root, home=args.home, preset=args.preset)
    except Exception as exc:  # noqa: BLE001 - fail closed
        print('guard_live: cannot initialise (%s)' % exc, file=sys.stderr)
        return 2
    if args.explain is not None or args.explain_write is not None:
        cwd = args.cwd or os.getcwd()
        if args.explain is not None:
            payload = {'tool_name': 'Bash', 'tool_input': {'command': args.explain}, 'cwd': cwd}
        else:
            payload = {'tool_name': 'Write', 'tool_input': {'file_path': args.explain_write}, 'cwd': cwd}
        ok, reason = guard.decide(payload)
        print('ALLOW' if ok else 'DENY  ' + reason)
        return 0 if ok else 2
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001 - fail closed on anything unreadable
        print('guard_live: unreadable hook input', file=sys.stderr)
        return 2
    try:
        ok, reason = guard.decide(payload)
    except Exception as exc:  # noqa: BLE001 - a guard bug must not open the gate
        print('guard_live[%s]: internal error, failing closed (%s: %s)' % (args.role, type(exc).__name__, exc),
              file=sys.stderr)
        return 2
    if ok:
        return 0
    print('guard_live[%s]: %s' % (args.role, reason.replace('\n', ' ')), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
