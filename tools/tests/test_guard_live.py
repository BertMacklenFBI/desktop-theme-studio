#!/usr/bin/python3 -B
"""Table-driven tests for tools/guard_live.py.

Run:  /usr/bin/python3 -B -m unittest tools/tests/test_guard_live.py -v
      (or /usr/bin/python3 -B tools/tests/test_guard_live.py)

Everything runs against a throw-away fixture (fake $HOME, fake studio, fake /tmp);
nothing touches the live desktop, and no hook is installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOLS, 'guard_live.py')
sys.path.insert(0, TOOLS)
import guard_live  # noqa: E402

ALLOW, DENY = True, False


def build_fixture():
    base = os.path.realpath(tempfile.mkdtemp(prefix='guard-live-test-'))
    home = os.path.join(base, 'home')
    studio = os.path.join(home, 'Documents', 'studio')
    tmp = os.path.join(base, 'tmp')
    dirs = [
        'home/.config/gtk-3.0', 'home/.themes', 'home/.icons', 'home/.local/share/Trash',
        'home/.local/bin', 'home/.claude/agents', 'home/Documents/other', 'tmp',
        'home/Documents/studio/presets/foo/desktop', 'home/Documents/studio/presets/foo/identity',
        'home/Documents/studio/presets/foo/applications', 'home/Documents/studio/presets/foo/system',
        'home/Documents/studio/presets/foo/state', 'home/Documents/studio/presets/foo/verification',
        'home/Documents/studio/presets/foo/build/sub',
        'home/Documents/studio/presets/_template', 'home/Documents/studio/presets/example',
        'home/Documents/studio/presets/red-panda-overtime-x',
        'home/Documents/studio/refinements/cinnamon-current-collection/contributions',
        'home/Documents/studio/refinements/cinnamon-current-collection/state',
        'home/Documents/studio/repairs/nim-nitch/catalog',
        'home/Documents/studio/refinements/cinnamon-current-collection/generated',
        'home/Documents/studio/refinements/cinnamon-current-collection/runtime-quality',
        'home/Documents/studio/refinements/cinnamon-current-collection/tests',
        'home/Documents/studio/presets/tangerine-graphite/applications',
        'home/Documents/studio/presets/tangerine-graphite/desktop',
        'home/Documents/studio/presets/X/system/plymouth',
        'home/Documents/studio/presets/pastel-leather/desktop',
        'home/Documents/studio/presets/moonstone-stereo/desktop/build',
        'home/Documents/studio/presets/moonstone-stereo/artwork',
        'home/Documents/studio/presets/new-theme/desktop/build',
        'home/Documents/studio/presets/new-theme/artwork',
        'home/Documents/studio/presets/new-theme/identity',
        'home/Documents/studio/presets/new-theme/applications',
        'home/Documents/studio/presets/new-theme/system',
        'home/Documents/studio/tools',
    ]
    for d in dirs:
        os.makedirs(os.path.join(base, d), exist_ok=True)
    # symlink escapes: a preset path and a /tmp path that point at live dirs
    os.symlink(os.path.join(home, '.themes'), os.path.join(studio, 'presets/foo/desktop/escape'))
    os.symlink(os.path.join(home, '.config'), os.path.join(tmp, 'cfglink'))
    os.symlink(os.path.join(studio, 'presets/foo/desktop'), os.path.join(tmp, 'into-studio'))
    os.symlink('/bin/bash', os.path.join(tmp, 'mybash'))
    os.symlink(os.path.join(studio, 'presets/new-theme/applications'), os.path.join(tmp, 'in-studio-link'))
    os.symlink(shutil.which('gsettings') or '/usr/bin/gsettings', os.path.join(tmp, 'gs2'))
    with open(os.path.join(tmp, 'payload.sh'), 'w') as fh:
        fh.write('#!/bin/sh\ngsettings set org.cinnamon.desktop.interface gtk-theme X\n')
    with open(os.path.join(tmp, 'payload.py'), 'w') as fh:
        fh.write('import subprocess\nsubprocess.run(["dconf", "write", "/org/x", "1"])\n')
    with open(os.path.join(tmp, 'benign.py'), 'w') as fh:
        fh.write('print("hello")\n')
    for rel in ('presets/X/design.json', 'presets/X/BRIEF.md', 'presets/pastel-leather/design.json',
                'presets/moonstone-stereo/desktop/build.py', 'presets/tangerine-graphite/desktop/build.py',
                'presets/new-theme/system/grub.cfg', 'check.sh', 'theme.sh', 'tools/design_lint.py',
                'tools/containment.py'):
        with open(os.path.join(studio, rel), 'w') as fh:
            fh.write('# fixture\n')
    with open(os.path.join(studio, 'apply.sh'), 'w') as fh:
        fh.write('#!/bin/sh\nexec python3 studio.py apply "$@"  # --commit installs\n')
    return base, home, studio, tmp


# (role, tool, value, expected, note).  tool 'bash' -> command; 'write' / 'edit' / 'nb' -> path.
# {H}=fake home, {S}=fake studio, {T}=fake tmp.
CASES = [
    # ---- allowed reads / previews
    ('builder', 'bash', 'cat README.md', ALLOW, 'plain read'),
    ('reviewer', 'bash', 'grep -rn "gsettings set" presets', ALLOW, 'grep for a denied phrase is data'),
    ('reviewer', 'bash', 'find presets -name "*.json" | head', ALLOW, 'find + pipe'),
    ('builder', 'bash', '/usr/bin/python3 presets/foo/desktop/build.py', ALLOW, 'builder run'),
    ('builder', 'bash', '/usr/bin/python3 -B presets/foo/desktop/build.py --out presets/foo/desktop/out', ALLOW, 'builder with args'),
    ('builder', 'bash', './theme.sh plan', ALLOW, 'theme plan is a preview'),
    ('builder', 'bash', './apply.sh presets/foo', ALLOW, 'apply.sh without --commit previews'),
    ('reviewer', 'bash', 'gsettings get org.cinnamon.desktop.interface gtk-theme', ALLOW, 'gsettings get'),
    ('reviewer', 'bash', 'dconf read /org/cinnamon/theme/name', ALLOW, 'dconf read'),
    ('reviewer', 'bash', 'ls -la ~/.themes ~/.config/gtk-3.0', ALLOW, 'listing live dirs is a read'),
    ('builder', 'bash', 'python3 -c "print(1+1)"', ALLOW, 'harmless inline python'),
    ('builder', 'bash', 'echo hi > {T}/out.txt 2>&1', ALLOW, 'redirect into tmp'),
    ('builder', 'bash', 'ls >/dev/null 2>&1', ALLOW, '/dev/null + fd dup'),
    ('builder', 'bash', 'cp -r presets/_template presets/newid', ALLOW, 'copy template into new preset'),
    ('builder', 'bash', 'cd presets/foo && ls && git status', ALLOW, 'cd + read'),
    ('builder', 'bash', 'theme', ALLOW, 'bare theme shortcut only lists'),
    ('builder', 'bash', 'rm -rf presets/foo/build', ALLOW, 'rm of a scratch dir without records'),
    ('builder', 'bash', 'cat > {T}/x.sh <<\'EOF\'\ngsettings set a b c\nEOF', ALLOW, 'heredoc body is data for cat'),
    ('builder', 'bash', 'xargs -I{} cp {} {T}/', ALLOW, 'xargs with literal tmp dest'),
    ('builder', 'bash', 'bash {T}/does-not-exist.sh', DENY, 'script outside studio that cannot be inspected yet'),
    ('builder', 'bash', 'python3 {T}/benign.py', ALLOW, 'benign tmp script'),

    # ---- Bash denials, direct
    ('builder', 'bash', 'gsettings set org.cinnamon.desktop.interface gtk-theme X', DENY, 'gsettings set'),
    ('builder', 'bash', 'gsettings reset-recursively org.cinnamon', DENY, 'gsettings reset-recursively'),
    ('builder', 'bash', 'dconf load /org/cinnamon/ < dump.ini', DENY, 'dconf load'),
    ('builder', 'bash', 'sudo true', DENY, 'sudo'),
    ('builder', 'bash', 'pkexec true', DENY, 'pkexec'),
    ('builder', 'bash', 'systemctl --user restart foo', DENY, 'systemctl'),
    ('builder', 'bash', 'cinnamon --replace &', DENY, 'cinnamon --replace'),
    ('builder', 'bash', 'pkill -f eww', DENY, 'pkill'),
    ('builder', 'bash', 'killall nemo', DENY, 'killall'),
    ('builder', 'bash', 'kill -9 1234', DENY, 'kill'),
    ('builder', 'bash', './theme.sh apply', DENY, 'theme apply'),
    ('builder', 'bash', 'presets/foo/theme.sh keep', DENY, 'theme keep'),
    ('builder', 'bash', 'sh theme.sh restore', DENY, 'theme restore via sh'),
    ('builder', 'bash', '/usr/bin/python3 isolate.py', DENY, 'isolate.py'),
    ('builder', 'bash', 'python3 studio.py apply presets/foo --commit', DENY, '--commit'),
    ('builder', 'bash', 'python3 presets/foo/system/system.py apply --comm', DENY, 'argparse abbreviation of --commit'),
    ('builder', 'bash', 'update-initramfs -u', DENY, 'update-initramfs'),
    ('builder', 'bash', 'update-grub', DENY, 'update-grub'),
    ('builder', 'bash', 'plymouth-set-default-theme -R x', DENY, 'plymouth-set-default-theme'),
    ('builder', 'bash', 'python3 repairs/nim-nitch/transaction/repair_installer.py', DENY, 'repair_installer'),
    ('builder', 'bash', 'python3 publish_sources.py publish', DENY, 'publish_sources publish'),
    ('builder', 'bash', 'python3 publish_sources.py plan', ALLOW, 'publish_sources plan'),
    ('builder', 'bash', './restore.sh 20260101', DENY, 'restore.sh'),
    ('builder', 'bash', 'python3 studio.py snapshot', DENY, 'studio snapshot'),
    ('builder', 'bash', 'python3 studio.py check', ALLOW, 'studio check'),
    ('builder', 'bash', 'theme graphite-brass', DENY, 'theme shortcut with arg'),
    ('builder', 'bash', 'ls; theme restore', DENY, 'theme shortcut after ;'),
    ('builder', 'bash', '~/.local/bin/theme status', DENY, 'theme shortcut by path'),
    ('builder', 'bash', 'xdotool key super', DENY, 'xdotool'),
    ('builder', 'bash', 'cinnamon-settings themes', DENY, 'cinnamon-settings'),
    ('builder', 'bash', 'rm -rf ~', DENY, 'rm -rf ~'),
    ('builder', 'bash', 'rm -rf "$HOME"/', DENY, 'rm -rf $HOME'),
    ('builder', 'bash', 'rm -rf /', DENY, 'rm -rf /'),
    ('builder', 'bash', 'rm -rf presets/foo', DENY, 'rm of preset with state/ + verification/'),
    ('builder', 'bash', 'rm -f ~/.local/share/Trash/files/x', DENY, 'Trash is still protected'),
    ('builder', 'bash', 'echo x > ~/.config/gtk-3.0/gtk.css', DENY, 'redirect into ~/.config'),
    ('builder', 'bash', 'echo alias >> ~/.bashrc', DENY, 'append to .bashrc'),
    ('builder', 'bash', 'cp a ~/.themes/', DENY, 'cp into ~/.themes'),
    ('builder', 'bash', 'cp -t ~/.icons a b', DENY, 'cp -t into ~/.icons'),
    ('builder', 'bash', 'mv ~/.themes/Old {T}/', DENY, 'mv out of ~/.themes (source destroyed)'),
    ('builder', 'bash', 'ln -sf $PWD/x ~/.local/bin/x', DENY, 'ln into ~/.local/bin'),
    ('builder', 'bash', 'rsync -a out/ ~/.local/share/themes/X/', DENY, 'rsync into ~/.local/share'),
    ('builder', 'bash', 'install -m644 a /usr/share/themes/X/a', DENY, 'install into /usr'),
    ('builder', 'bash', 'echo x | tee -a /etc/plymouth/plymouthd.conf', DENY, 'tee into /etc'),
    ('builder', 'bash', 'sed -i s/a/b/ ~/.config/gtk-3.0/settings.ini', DENY, 'sed -i on live file'),
    ('builder', 'bash', 'tar -xzf a.tgz -C ~/.themes', DENY, 'tar extract into ~/.themes'),
    ('builder', 'bash', 'cp -r out ~', DENY, 'recursive copy into $HOME itself'),
    ('builder', 'bash', 'echo x > ~/.claude/agents/me.md', DENY, 'agents may not edit their own definitions'),
    ('builder', 'bash', 'echo {} > presets/foo/state/latest.json', DENY, 'studio record via redirect'),
    ('builder', 'bash', 'cp x ~/.the*', DENY, 'glob that can hit ~/.themes'),
    ('builder', 'bash', 'cp x ~/.{themes,foo}/', DENY, 'brace expansion into ~/.themes'),

    # ---- obfuscations
    ('builder', 'bash', "bash -c 'gsettings set org.x k v'", DENY, 'bash -c'),
    ('builder', 'bash', 'sh -c "cd /tmp && dconf write /a 1"', DENY, 'sh -c with &&'),
    ('builder', 'bash', 'FOO=1 sudo x', DENY, 'env-prefixed sudo'),
    ('builder', 'bash', 'env -i PATH=/usr/bin gsettings set a b c', DENY, 'env wrapper'),
    ('builder', 'bash', 'nice -n 5 timeout 10 gsettings reset a b', DENY, 'nested wrappers'),
    ('builder', 'bash', 'echo $(gsettings set a b c)', DENY, 'command substitution'),
    ('builder', 'bash', 'echo `dconf reset -f /org/`', DENY, 'backticks'),
    ('builder', 'bash', 'true && { ls; gsettings set a b c; }', DENY, 'brace group after &&'),
    ('builder', 'bash', 'ls | xargs pkill', DENY, 'xargs pkill'),
    ('builder', 'bash', 'find . -name x -exec gsettings set a b c \\;', DENY, 'find -exec'),
    ('builder', 'bash', 'g\\settings set a b c', DENY, 'backslash-split name'),
    ('builder', 'bash', "g''settings set a b c", DENY, 'empty-quote-split name'),
    ('builder', 'bash', "$'\\x67settings' set a b c", DENY, "ANSI-C $'\\x..' name"),
    ('builder', 'bash', 'X=gsettings; $X set a b c', DENY, 'variable as command (tracked)'),
    ('builder', 'bash', '$(echo gsettings) set a b c', DENY, 'computed command name'),
    ('builder', 'bash', 'eval "gsettings set a b c"', DENY, 'eval'),
    ('builder', 'bash', 'echo "gsettings set a b c" | bash', DENY, 'pipe into shell'),
    ('builder', 'bash', 'bash <<EOF\ndconf write /a 1\nEOF', DENY, 'heredoc into shell'),
    ('builder', 'bash', "python3 -c 'import subprocess; subprocess.run([\"gsettings\",\"set\",\"a\",\"b\",\"c\"])'", DENY, 'python -c subprocess'),
    ('builder', 'bash', "python3 -c \"from gi.repository import Gio; Gio.Settings.new('org.x').set_string('k','v')\"", DENY, 'python -c Gio.Settings'),
    ('builder', 'bash', "python3 -c \"open('$HOME/.config/x','w').write('1')\"", DENY, 'python -c writing ~/.config'),
    ('builder', 'bash', 'bash {T}/payload.sh', DENY, 'tmp shell payload scanned'),
    ('builder', 'bash', 'python3 {T}/payload.py', DENY, 'tmp python payload scanned'),
    ('builder', 'bash', 'D=~/.config; cp a "$D/gtk-3.0/"', DENY, 'tracked variable in target'),
    ('builder', 'bash', 'cd ~/.config && echo x > gtk.css', DENY, 'cd then relative redirect'),
    ('builder', 'bash', 'cp a "$(cat {T}/dest)"', DENY, 'computed write target'),
    ('builder', 'bash', 'echo "unterminated', DENY, 'unparseable command fails closed'),
    ('builder', 'bash', 'xdg-mime default foo.desktop text/plain', DENY, 'desktop default change'),
    ('builder', 'bash', 'gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.Cinnamon.Eval 1', DENY, 'gdbus call'),
    ('builder', 'bash', 'cp x {T}/cfglink/gtk-3.0/gtk.css', DENY, 'symlink escape via /tmp link'),
    ('builder', 'bash', "{T}/mybash -c 'gsettings set a b c'", DENY, 'symlinked interpreter resolved by realpath'),
    ('builder', 'bash', 'X="gsettings set a b c"; $X', DENY, 'unquoted variable word-splits into a command'),
    ('builder', 'bash', 'bash <<< "gsettings set a b c"', DENY, 'here-string into shell'),
    ('builder', 'bash', '. /dev/stdin <<< "dconf reset -f /"', DENY, 'source from /dev/stdin'),
    ('builder', 'bash', 'source <(echo gsettings set a b c)', DENY, 'source from process substitution'),
    ('builder', 'bash', 'f(){ gsettings "$@"; }; f set a b c', DENY, 'function wrapper'),
    ('builder', 'bash', 'cat <<EOF\n$(dconf write /a 1)\nEOF', DENY, 'substitution inside unquoted heredoc'),
    ('builder', 'bash', 'python3 presets/foo/desktop/build.py --out ~/.themes/X', DENY, 'script output flag into live path'),
    ('builder', 'bash', 'cd ~/.themes && python3 {S}/presets/foo/desktop/build.py', DENY, 'script run with live cwd'),
    ('builder', 'bash', "trap 'gsettings set a b c' EXIT; true", DENY, 'trap command string'),
    ('builder', 'bash', 'ssh localhost gsettings set a b c', DENY, 'ssh re-entry'),
    ('builder', 'bash', 'f=$(mktemp); echo x > "$f"', ALLOW, 'mktemp variable resolves under TMPDIR'),
    ('builder', 'bash', 'TMPDIR=~/.config; f=$(mktemp); echo x > "$f"', DENY, 'mktemp with hostile TMPDIR'),
    ('builder', 'bash', 'ln -s /usr/bin/gsettings {T}/gs; {T}/gs set a b c', DENY, 'program created in the same command is not trusted'),
    ('builder', 'bash', '{T}/gs2 set a b c', DENY, 'pre-existing renamed symlink resolved to gsettings'),

    # ---- reviewer is read-only even in Bash
    ('reviewer', 'bash', 'echo x > presets/foo/README.md', DENY, 'reviewer redirect into studio'),
    ('reviewer', 'bash', 'echo x > {T}/notes.txt', ALLOW, 'reviewer scratch in tmp'),
    ('reviewer', 'bash', 'touch presets/foo/x', DENY, 'reviewer touch in studio'),

    # ---- Write / Edit allow-lists by role
    ('designer', 'write', '{S}/presets/foo/design.json', ALLOW, 'designer design.json'),
    ('designer', 'edit', '{S}/presets/foo/BRIEF.md', ALLOW, 'designer BRIEF'),
    ('designer', 'write', '{S}/presets/foo/identity/palette/notes.md', ALLOW, 'designer identity/**'),
    ('designer', 'write', '{S}/presets/foo/desktop/build.py', DENY, 'designer no desktop/'),
    ('designer', 'write', '{S}/presets/_template/design.json', DENY, 'designer no _template'),
    ('designer', 'write', '{S}/presets/red-panda-overtime-x/design.json', DENY, 'designer no red-panda-overtime*'),
    ('designer', 'write', '{S}/presets/example/BRIEF.md', DENY, 'designer no example'),
    ('builder', 'write', '{S}/presets/foo/applications/adapter.py', ALLOW, 'builder anywhere in preset'),
    ('builder', 'write', '{S}/presets/foo/state/receipt.json', DENY, 'builder no state/'),
    ('builder', 'write', '{S}/presets/foo/verification/x.png', DENY, 'builder no verification/'),
    ('builder', 'write', '{S}/presets/foo/desktop/latest.json', DENY, 'latest.json anywhere'),
    ('builder', 'write', '{S}/studio.py', DENY, 'builder no studio root files'),
    ('builder-desktop', 'write', '{S}/presets/foo/desktop/gtk.css', ALLOW, 'desktop builder desktop/'),
    ('builder-desktop', 'write', '{S}/presets/foo/artwork/wall.svg', ALLOW, 'desktop builder artwork/'),
    ('builder-desktop', 'write', '{S}/presets/foo/applications/x', DENY, 'desktop builder no applications/'),
    ('builder-apps', 'write', '{S}/presets/foo/applications/kitty.conf', ALLOW, 'apps builder applications/'),
    ('builder-apps', 'write', '{S}/presets/foo/system/grub.cfg', DENY, 'apps builder no system/'),
    ('builder-boot', 'write', '{S}/presets/foo/system/plymouth/theme.script', ALLOW, 'boot builder system/'),
    ('builder-boot', 'write', '{S}/presets/foo/design.json', DENY, 'boot builder no design.json'),
    ('integrator', 'write', '{S}/refinements/cinnamon-current-collection/profiles.json', ALLOW, 'integrator profiles.json'),
    ('integrator', 'write', '{S}/refinements/cinnamon-current-collection/contributions/foo.json', ALLOW, 'integrator contributions/'),
    ('integrator', 'write', '{S}/repairs/nim-nitch/catalog/foo.json', ALLOW, 'integrator nim catalog'),
    ('integrator', 'write', '{S}/refinements/cinnamon-current-collection/state/x.json', DENY, 'integrator never state/'),
    ('integrator', 'write', '{S}/refinements/cinnamon-current-collection/live.py', DENY, 'integrator not live.py'),
    ('integrator', 'write', '{S}/presets/foo/design.json', DENY, 'integrator not presets'),
    ('reviewer', 'write', '{S}/presets/foo/README.md', DENY, 'reviewer nothing in studio'),
    ('reviewer', 'write', '{T}/review-notes.md', ALLOW, 'reviewer tmp'),
    ('designer', 'write', '{T}/scratch.json', ALLOW, 'tmp for everyone'),
    ('designer', 'write', '{H}/Documents/other/x.md', DENY, 'outside studio and tmp'),
    ('builder', 'write', '~/.config/gtk-3.0/gtk.css', DENY, 'tilde to live path'),
    ('builder', 'write', '$HOME/.themes/X/index.theme', DENY, '$HOME to live path'),
    ('builder', 'write', '/etc/plymouth/plymouthd.conf', DENY, '/etc'),
    ('builder', 'write', '{H}/.claude/agents/cinnamon-theme-builder.md', DENY, 'own agent definition'),
    ('builder', 'write', 'presets/foo/desktop/../../../../../.bashrc', DENY, 'relative .. escape'),
    ('builder', 'write', 'presets/foo/desktop/new.css', ALLOW, 'relative path against cwd'),
    ('builder', 'write', '{S}/presets/foo/desktop/escape/X/index.theme', DENY, 'symlink escape into ~/.themes'),
    ('builder', 'write', '{T}/into-studio/x.css', ALLOW, 'tmp symlink into allowed preset dir'),
    ('builder-apps', 'write', '{T}/into-studio/x.css', DENY, 'tmp symlink into a dir the role does not own'),
    ('builder', 'write', '{T}/cfglink/gtk-3.0/gtk.css', DENY, 'tmp symlink into ~/.config'),
    ('builder', 'nb', '{S}/presets/foo/desktop/notes.ipynb', ALLOW, 'NotebookEdit path field'),

    # ---- Bash write targets inside the studio pass the same role gate (reviewer gap 7)
    ('builder-desktop', 'bash', 'echo hi > presets/tangerine-graphite/applications/foo.txt', DENY, 'gap7: redirect into applications/'),
    ('builder-desktop', 'bash', 'echo hi > presets/X/system/plymouth/x.txt', DENY, 'gap7: redirect into system/'),
    ('builder-desktop', 'bash', 'echo hi > presets/X/design.json', DENY, 'gap7: redirect onto design.json'),
    ('builder-desktop', 'bash', 'echo hi >> presets/X/BRIEF.md', DENY, 'gap7: append to BRIEF.md'),
    ('builder-desktop', 'bash', 'echo pwned > presets/pastel-leather/design.json', DENY, 'gap7: overwrite another design.json'),
    ('builder-desktop', 'bash', 'rm -rf presets/moonstone-stereo/desktop', DENY, 'gap7: rm of an area root'),
    ('designer', 'bash', 'echo pwned > presets/tangerine-graphite/desktop/build.py', DENY, 'gap7: designer into desktop/'),
    ('builder-apps', 'bash', 'cp x.css presets/new-theme/desktop/gtk.css', DENY, 'cp into another role subtree'),
    ('builder-desktop', 'bash', 'cp x.css presets/new-theme/desktop/gtk.css', ALLOW, 'cp into own subtree'),
    ('builder-desktop', 'bash', 'sed -i s/a/b/ presets/new-theme/system/grub.cfg', DENY, 'sed -i into another role subtree'),
    ('builder-boot', 'bash', 'sed -i s/a/b/ presets/new-theme/system/grub.cfg', ALLOW, 'sed -i in own subtree'),
    ('builder-desktop', 'bash', 'tar -xf a.tar -C presets/new-theme/applications', DENY, 'tar -x into another role subtree'),
    ('builder-apps', 'bash', 'tar -xf a.tar -C presets/new-theme/applications', ALLOW, 'tar -x into own area root'),
    ('builder-boot', 'bash', 'rm -r presets/new-theme/desktop/build', DENY, 'rm -r in another role subtree'),
    ('builder-desktop', 'bash', 'rm -r presets/new-theme/desktop/build', ALLOW, 'rm -r inside own subtree'),
    ('builder-desktop', 'bash', 'mkdir -p presets/new-theme/desktop', ALLOW, 'create own area root'),
    ('builder-desktop', 'bash', 'find presets/new-theme/desktop -name "*.pyc" -delete', ALLOW, 'find -delete within own area'),
    ('builder-apps', 'bash', 'find presets/new-theme/desktop -name "*.pyc" -delete', DENY, 'find -delete in another area'),
    ('builder-apps', 'bash', 'find presets/new-theme/desktop -name x -exec rm {} +', DENY, 'find -exec rm in another area'),
    ('builder-apps', 'bash', '/usr/bin/convert a.png presets/new-theme/artwork/x.png', DENY, 'convert via alternatives symlink still gated'),
    ('builder-desktop', 'bash', 'echo x > {T}/in-studio-link/x', DENY, 'tmp symlink into another role area'),
    ('integrator', 'bash', 'cp x presets/new-theme/desktop/', DENY, 'integrator not presets'),
    ('integrator', 'bash', 'echo x > refinements/cinnamon-current-collection/contributions/x.json', ALLOW, 'integrator contributions'),
    ('integrator', 'bash', 'echo x > refinements/cinnamon-current-collection/generated/x', ALLOW, 'integrator generated'),
    ('integrator', 'bash', 'echo x > refinements/cinnamon-current-collection/runtime-quality/x', ALLOW, 'integrator runtime-quality'),
    ('integrator', 'bash', 'cp t.py refinements/cinnamon-current-collection/tests/', ALLOW, 'integrator tests (area root)'),
    ('integrator', 'bash', 'echo x > refinements/cinnamon-current-collection/profiles.json', ALLOW, 'integrator profiles.json'),
    ('integrator', 'bash', 'echo x > repairs/nim-nitch/catalog/a.json', ALLOW, 'integrator nim catalog'),
    ('integrator', 'bash', 'echo x > refinements/cinnamon-current-collection/state/a.json', DENY, 'integrator never state/'),
    ('integrator', 'bash', 'echo x > refinements/cinnamon-current-collection/live.py', DENY, 'integrator not controller code'),
    ('designer', 'bash', 'cp -r presets/_template presets/brand-new', ALLOW, 'designer creates a new preset'),
    ('designer', 'bash', 'cp -r presets/_template/. presets/pastel-leather/', DENY, 'designer may not overlay an existing preset'),
    ('builder-desktop', 'bash', 'cp -r presets/_template presets/brand-new2', DENY, 'area builders do not create presets'),
    ('builder', 'bash', 'rm -rf presets/new-theme', DENY, 'whole-preset rm (builder)'),
    ('designer', 'bash', 'rm -rf presets/new-theme', DENY, 'whole-preset rm (designer)'),
    ('builder-desktop', 'bash', 'rm -r presets/new-theme/', DENY, 'whole-preset rm (builder-desktop)'),
    ('builder', 'bash', 'mv presets/new-theme presets/old-theme', DENY, 'whole-preset mv'),
    ('builder', 'bash', 'git add -A && git commit -m x', DENY, 'git writes at studio root are coordinator-only'),
    ('reviewer', 'bash', 'rm -rf {T}/scratch', ALLOW, 'reviewer cleans its tmp'),

    # ---- --preset restricts preset roles to one preset (6th column)
    ('builder-desktop', 'bash', 'echo x > presets/new-theme/desktop/a.css', ALLOW, '--preset own preset', 'new-theme'),
    ('builder-desktop', 'bash', 'echo x > presets/pastel-leather/desktop/a.css', DENY, '--preset other preset (bash)', 'new-theme'),
    ('builder-desktop', 'write', '{S}/presets/pastel-leather/desktop/a.css', DENY, '--preset other preset (Write)', 'new-theme'),
    ('builder', 'bash', 'cp a presets/moonstone-stereo/artwork/', DENY, '--preset builder cp into other preset', 'new-theme'),
    ('builder', 'bash', 'rm -r presets/moonstone-stereo/desktop/build', DENY, '--preset builder rm in other preset', 'new-theme'),
    ('designer', 'write', '{S}/presets/pastel-leather/design.json', DENY, '--preset designer other preset', 'new-theme'),
    ('designer', 'write', '{S}/presets/new-theme/design.json', ALLOW, '--preset designer own preset', 'new-theme'),

    # ---- must stay ALLOW (normal work)
    ('builder-desktop', 'bash', '/usr/bin/python3 -B presets/new-theme/desktop/build.py', ALLOW, 'keep: desktop build'),
    ('builder-desktop', 'bash', 'cd presets/new-theme/desktop && /usr/bin/python3 -B build.py && /usr/bin/python3 -B validate.py', ALLOW, 'keep: cd + build + validate'),
    ('builder-desktop', 'bash', '/usr/bin/python3 -B -m unittest discover -s presets/new-theme/desktop', ALLOW, 'keep: unittest in area'),
    ('builder-desktop', 'bash', '/usr/bin/convert in.png -resize 50% presets/new-theme/artwork/x.png', ALLOW, 'keep: convert into artwork'),
    ('builder-desktop', 'bash', 'echo x > {T}/notes.txt', ALLOW, 'keep: tmp'),
    ('designer', 'bash', '/usr/bin/convert a.png b.png +append presets/new-theme/identity/palette-sheet.png', ALLOW, 'keep: designer identity sheet'),
    ('designer', 'bash', '/usr/bin/python3 -B tools/design_lint.py new-theme --write', ALLOW, 'keep: design_lint --write'),
    ('builder-boot', 'bash', '/usr/bin/python3 -B presets/new-theme/system/system.py preview', ALLOW, 'keep: system preview'),
    ('builder-boot', 'bash', 'cd presets/new-theme && /usr/bin/python3 -B system/system.py new-theme preview', ALLOW, 'keep: system preview from preset dir'),
    ('reviewer', 'bash', './check.sh', ALLOW, 'keep: reviewer check.sh'),
    ('reviewer', 'bash', './theme.sh plan', ALLOW, 'keep: reviewer theme plan'),
    ('reviewer', 'bash', './apply.sh presets/new-theme', ALLOW, 'keep: reviewer apply preview'),
    ('reviewer', 'bash', '/usr/bin/python3 -B tools/containment.py verify', ALLOW, 'keep: reviewer containment verify'),
]


class GuardTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.home, cls.studio, cls.tmp = build_fixture()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def fill(self, text):
        return text.replace('{H}', self.home).replace('{S}', self.studio).replace('{T}', self.tmp)

    def payload(self, tool, value):
        value = self.fill(value)
        if tool == 'bash':
            return {'tool_name': 'Bash', 'tool_input': {'command': value}, 'cwd': self.studio}
        if tool == 'nb':
            return {'tool_name': 'NotebookEdit', 'tool_input': {'notebook_path': value}, 'cwd': self.studio}
        name = {'write': 'Write', 'edit': 'Edit'}[tool]
        return {'tool_name': name, 'tool_input': {'file_path': value, 'content': 'x'}, 'cwd': self.studio}

    def guard(self, role, preset=None):
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
        return guard_live.Guard(role, studio_root=self.studio, home=self.home, tmp_roots=[self.tmp],
                                environ=env, preset=preset)

    def test_table(self):
        self.assertGreaterEqual(len(CASES), 40)
        for role, tool, value, expected, note, *extra in CASES:
            with self.subTest(note=note, role=role, value=value):
                ok, reason = self.guard(role, *extra).decide(self.payload(tool, value))
                self.assertEqual(ok, expected, '%s -> %s' % (note, reason or 'ALLOW'))
                if not ok:
                    self.assertTrue(reason and '\n' not in reason)

    def test_other_tools_pass(self):
        ok, _ = self.guard('reviewer').decide({'tool_name': 'Read', 'tool_input': {'file_path': '/etc/passwd'}})
        self.assertTrue(ok)

    def test_lineage_shim_follows_library_for_commit(self):
        # Phase 6 shims hold no argument handling; the guard must scan lib/lineage/<module>.
        shim = "import os\nexec(_lineage_code('%s'))\n"
        desktop = os.path.join(self.studio, 'presets', 'shimtest', 'desktop')
        lineage = os.path.join(self.studio, 'lib', 'lineage')
        os.makedirs(desktop, exist_ok=True)
        os.makedirs(lineage, exist_ok=True)
        with open(os.path.join(lineage, 'livelib.py'), 'w') as fh:
            fh.write("import argparse\np=argparse.ArgumentParser()\np.add_argument('--commit')\n")
        with open(os.path.join(lineage, 'quietlib.py'), 'w') as fh:
            fh.write("print('preview only')\n")
        for name, module in (('live.py', 'livelib.py'), ('quiet.py', 'quietlib.py'),
                             ('missing.py', 'absent.py'), ('odd.py', '../escape.py')):
            with open(os.path.join(desktop, name), 'w') as fh:
                fh.write(shim % module)
        g = self.guard('builder')
        run = '/usr/bin/python3 -B {S}/presets/shimtest/desktop/%s apply "$FLAG"'
        for script, expected in (('live.py', False), ('quiet.py', True), ('missing.py', False), ('odd.py', False)):
            with self.subTest(script=script):
                ok, reason = g.decide(self.payload('bash', run % script))
                self.assertEqual(ok, expected, reason or 'ALLOW')

    def test_publisher_apply_is_denied(self):
        g = self.guard('builder')
        for verb in ('apply', 'publish', 'restore x'):
            with self.subTest(verb=verb):
                ok, _ = g.decide(self.payload('bash', '/usr/bin/python3 -B {S}/repairs/x/publish_sources.py ' + verb))
                self.assertFalse(ok)
        ok, _ = g.decide(self.payload('bash', '/usr/bin/python3 -B {S}/repairs/x/publish_sources.py check'))
        self.assertTrue(ok)

    def test_missing_fields_fail_closed(self):
        g = self.guard('builder')
        for bad in ({}, [], {'tool_name': 'Bash'}, {'tool_name': 'Bash', 'tool_input': {}},
                    {'tool_name': 'Write', 'tool_input': {'content': 'x'}},
                    {'tool_name': 3, 'tool_input': {}}):
            with self.subTest(bad=bad):
                self.assertFalse(g.decide(bad)[0])


class GuardCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.home, cls.studio, cls.tmp = build_fixture()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def run_hook(self, stdin, role='builder'):
        return subprocess.run(['/usr/bin/python3', '-B', SCRIPT, '--role', role,
                               '--home', self.home, '--studio-root', self.studio],
                              input=stdin, capture_output=True, text=True, timeout=30)

    def test_malformed_stdin(self):
        for raw in ('', 'not json', '{"tool_name": "Bash"', '[1,2]', 'null'):
            with self.subTest(raw=raw):
                r = self.run_hook(raw)
                self.assertEqual(r.returncode, 2)
                self.assertIn('guard_live', r.stderr)
        r = self.run_hook('not json')
        self.assertEqual(r.stderr.strip(), 'guard_live: unreadable hook input')

    def test_allow_is_silent(self):
        r = self.run_hook(json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}, 'cwd': self.studio}))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''))

    def test_deny_one_line(self):
        r = self.run_hook(json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'sudo ls'}, 'cwd': self.studio}))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertTrue(r.stderr.startswith('guard_live[builder]:'))

    def test_reserved_preset_flag_fails(self):
        with self.assertRaises(ValueError):
            guard_live.Guard('builder', preset='_template')
        r = subprocess.run(['/usr/bin/python3', '-B', SCRIPT, '--role', 'builder', '--preset', '../x'],
                           input='{}', capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)

    def test_unknown_role_fails(self):
        r = subprocess.run(['/usr/bin/python3', '-B', SCRIPT, '--role', 'coordinator'],
                           input='{}', capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)

    def test_explain(self):
        base = ['/usr/bin/python3', '-B', SCRIPT, '--home', self.home, '--studio-root', self.studio,
                '--cwd', self.studio]
        r = subprocess.run(base + ['--role', 'builder', '--explain', './theme.sh plan'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'ALLOW'))
        r = subprocess.run(base + ['--role', 'builder', '--explain', "bash -c 'gsettings set a b c'"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stdout.startswith('DENY'))
        r = subprocess.run(base + ['--role', 'designer', '--explain-write', 'presets/foo/desktop/x'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
