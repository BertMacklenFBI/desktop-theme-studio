#!/usr/bin/env python3
"""Optional receipt-backed native Cinnamon workspace picker. No workspace settings change.

plan; apply --state P [--commit]; check --state P; restore --state P [--commit].
Only the coordinator invokes committed operations, including private previews.
"""
import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from gi.repository import Gio, GLib

UUID = 'workspace-switcher@cinnamon.org'
SCHEMA = 'org.cinnamon'
KEY = 'enabled-applets'
VALUES = {'display-type': 'buttons', 'scroll-behavior': 'normal'}
ENTRY = re.compile(r'^panel\d+:(?:left|center|right):\d+:([^:]+):(\d+)(?::orient)?$')


def guard(path):
    """Inspect lexical ancestors: resolving first would hide redirected config paths."""
    path = Path(path).absolute()
    for item in reversed((path, *path.parents)):
        try: mode = item.lstat().st_mode
        except FileNotFoundError: continue
        if stat.S_ISLNK(mode): raise RuntimeError('Symlink configuration path: ' + str(item))
        if item != path and not stat.S_ISDIR(mode): raise RuntimeError('Non-directory ancestor: ' + str(item))
        if item == path and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RuntimeError('Nonregular configuration path: ' + str(item))
    return path


def config_paths(ident):
    return [Path(GLib.get_user_config_dir()) / 'cinnamon/spices' / UUID / f'{ident}.json',
            Path(GLib.get_home_dir()) / '.cinnamon/configs' / UUID / f'{ident}.json']


def get(key): return Gio.Settings.new(SCHEMA).get_value(key).print_(True)


def set_checked(key, before, after):
    if get(key) != before: raise RuntimeError('Concurrent GSettings change: ' + key)
    obj = Gio.Settings.new(SCHEMA)
    if not obj.set_value(key, GLib.Variant.parse(None, after, None, None)):
        raise RuntimeError('Setting rejected: ' + key)
    Gio.Settings.sync()
    if get(key) != after: raise RuntimeError('GSettings write did not persist: ' + key)


def entries(raw):
    values = GLib.Variant.parse(GLib.VariantType.new('as'), raw, None, None).unpack()
    seen = set()
    for entry in values:
        match = ENTRY.fullmatch(entry)
        if not match: raise RuntimeError('Unsupported applet definition: ' + entry)
        ident = int(match[2])
        if ident in seen: raise RuntimeError('Duplicate applet instance: ' + str(ident))
        seen.add(ident)
    return values


def serialize_entries(values): return GLib.Variant('as', values).print_(True)


def template():
    for base in [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]:
        folder = Path(base) / 'cinnamon/applets' / UUID
        if not folder.exists(): continue
        # Applet assets may be installed as symlinks; they are read-only inputs.
        folder = folder.resolve(strict=True)
        schema = guard(folder / 'settings-schema.json')
        meta = json.loads(guard(folder / 'metadata.json').read_text())
        if meta.get('multiversion') or (folder / 'settings-override.json').exists():
            raise RuntimeError('Unsupported workspace applet schema override or multiversion')
        raw = schema.read_bytes(); data = json.loads(raw)
        for props in data.values():
            if isinstance(props, dict) and 'type' in props and 'default' in props:
                props['value'] = props['default']
        for key, value in VALUES.items():
            if value not in data.get(key, {}).get('options', {}).values():
                raise RuntimeError('Workspace schema does not support ' + key)
            data[key]['value'] = value
        data['__md5__'] = hashlib.md5(raw, usedforsecurity=False).hexdigest()
        return data, str(schema), hashlib.sha256(raw).hexdigest()
    raise RuntimeError('Native workspace switcher is not installed')


def atomic(path, data, fresh=False):
    path = guard(path); path.parent.mkdir(parents=True, exist_ok=True); guard(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=4); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        guard(path)
        if fresh:
            os.link(name, path)  # O_EXCL semantics; never replace a concurrent new profile.
            os.unlink(name)
        else: os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)


def save(path, state): atomic(path, state)


def plan():
    before = get(KEY); current = entries(before)
    if any(ENTRY.fullmatch(x)[1] == UUID for x in current):
        raise RuntimeError('Workspace picker already enabled; refusing a duplicate')
    if any(x.startswith('panel2:center:0:') for x in current):
        raise RuntimeError('Bottom dock center slot is occupied')
    if '2:0:bottom' not in ast.literal_eval(get('panels-enabled')):
        raise RuntimeError('Expected bottom panel 2 is unavailable')
    counter = int(get('next-applet-id'))
    if counter <= 36 or any(int(ENTRY.fullmatch(x)[2]) >= counter for x in current):
        raise RuntimeError('next-applet-id is not fresh; refusing instance reuse')
    paths = config_paths(counter)
    for path in paths:
        guard(path)
        if path.exists(): raise RuntimeError('Applet config collision: ' + str(path))
    data, source, digest = template()
    entry = f'panel2:center:0:{UUID}:{counter}'
    action = dict(kind='gsetting', schema=SCHEMA, key=KEY, before=before,
                  after=serialize_entries(current + [entry]), attempted=False, applied=False)
    return dict(version=1, id='gnu-darwin-workstation', stage='workspace-picker',
                created_at=datetime.now(timezone.utc).isoformat(), status='planned', phase='planned',
                uuid=UUID, instance=counter, entry=entry, config=str(paths[0]), legacy_config=str(paths[1]),
                config_data=data, schema_source=source, schema_sha256=digest,
                counter_before=counter, counter_after=counter+1, actions=[action],
                touched_paths=[str(paths[0])], gsettings=[{'schema':SCHEMA, 'key':KEY},
                {'schema':SCHEMA, 'key':'next-applet-id'}], dconf=[], skipped=[], writes=0)


def validate_state(state):
    ident = state['instance']
    if type(ident) is not int or ident <= 36 or state.get('uuid') != UUID:
        raise RuntimeError('Invalid workspace receipt identity')
    if state['entry'] != f'panel2:center:0:{UUID}:{ident}': raise RuntimeError('Invalid picker entry')
    if [state['config'], state['legacy_config']] != [str(x) for x in config_paths(ident)]:
        raise RuntimeError('Receipt configuration paths do not match current HOME/XDG')
    if state['counter_before'] != ident or state['counter_after'] != ident+1:
        raise RuntimeError('Invalid counter journal')
    action, = state['actions']
    if (action['kind'], action['schema'], action['key']) != ('gsetting', SCHEMA, KEY):
        raise RuntimeError('Invalid workspace receipt action')
    if entries(action['after']) != entries(action['before']) + [state['entry']]:
        raise RuntimeError('Invalid workspace receipt transition')
    for path in config_paths(ident): guard(path)
    return action


def config_ok(state, allow_missing=False):
    path = guard(state['config'])
    if not path.exists(): return allow_missing
    if not path.is_file(): raise RuntimeError('Applet settings is not a regular file')
    data = json.loads(path.read_text())
    # Cinnamon may normalize metadata and formatting; appearance values are the contract.
    return all(data.get(key, {}).get('value') == value for key, value in VALUES.items())


def wait_unloaded(ident, timeout=10):
    code = f"(function(){{let d=imports.ui.appletManager.getAppletDefinition({{applet_id:'{ident}'}});return !d || !d.applet;}})()"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(['gdbus','call','--session','--dest','org.Cinnamon','--object-path',
            '/org/Cinnamon','--method','org.Cinnamon.Eval',code], capture_output=True,text=True,timeout=3)
        if result.returncode == 0:
            value = result.stdout.strip().replace('(true,', '(True,', 1).replace('(false,', '(False,', 1)
            ok, answer = ast.literal_eval(value)
            if ok and json.loads(answer) is True: return
        time.sleep(.1)
    raise RuntimeError('Cinnamon did not confirm picker unload; retaining settings for recovery')


def create_config(state, receipt):
    target=guard(state['config']); target.parent.mkdir(parents=True,exist_ok=True); guard(target)
    fd, name=tempfile.mkstemp(prefix=f'.workspace-{state["instance"]}-',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(state['config_data'],stream,indent=4); stream.flush(); os.fsync(stream.fileno())
        info=Path(name).stat()
        state['config_creation']={'temporary':name,'device':info.st_dev,'inode':info.st_ino}
        save(receipt,state) # ownership identity is durable before the exclusive link
        guard(target); os.link(name,target)
    finally:
        if os.path.exists(name): os.unlink(name)


def owns_config(state):
    if state.get('config_created'): return True
    creation=state.get('config_creation')
    target=guard(state['config'])
    if not creation or not target.exists(): return False
    info=target.stat()
    return (info.st_dev,info.st_ino)==(creation['device'],creation['inode'])


def commit(state, path):
    action = validate_state(state)
    if Path(path).exists(): raise RuntimeError('Use a fresh state path')
    state['status'] = 'applying'; state['phase'] = 'reserve-counter'; atomic(path,state,fresh=True)
    try:
        if hashlib.sha256(guard(state['schema_source']).read_bytes()).hexdigest() != state['schema_sha256']:
            raise RuntimeError('Workspace settings schema changed since plan')
        if get(KEY) != action['before']: raise RuntimeError('Applet list changed since plan')
        for item in config_paths(state['instance']):
            guard(item)
            if item.exists(): raise RuntimeError('Applet config collision: ' + str(item))
        set_checked('next-applet-id', str(state['counter_before']), str(state['counter_after']))
        state['phase'] = 'create-config'; save(path,state)
        create_config(state,path)
        state['config_created'] = True; state['phase'] = 'enable'; action['attempted'] = True; save(path,state)
        set_checked(KEY,action['before'],action['after'])
        action['applied'] = True; state['status'] = 'applied'; state['phase'] = 'complete'; save(path,state)
    except Exception as exc:
        state['error'] = str(exc); state['status'] = 'recovery-required'; save(path,state)
        raise
    return check(state)


def restore(state, path):
    action = validate_state(state)
    if state['status'] == 'restored': return check(state)
    current = entries(get(KEY)); matching = [x for x in current if int(ENTRY.fullmatch(x)[2]) == state['instance']]
    if matching not in ([], [state['entry']]): raise RuntimeError('Picker moved or instance reused; refusing restore')
    if Path(state['legacy_config']).exists(): raise RuntimeError('Unexpected legacy picker settings; refusing deletion')
    if not config_ok(state, allow_missing=True): raise RuntimeError('Picker preferences changed; refusing deletion')
    # A pre-config failure must never remove a competing file that caused a collision.
    owned_config = owns_config(state)
    if not owned_config and Path(state['config']).exists():
        raise RuntimeError('Unowned picker settings present; refusing deletion')
    needs_unload = action.get('attempted') or bool(matching)
    state['status']='restoring'; state['phase']='disable'; save(path,state)
    if matching:
        before = get(KEY); items=entries(before)
        if [x for x in items if int(ENTRY.fullmatch(x)[2]) == state['instance']] != [state['entry']]:
            raise RuntimeError('Picker changed during restore')
        # Remove only our entry, preserving every concurrent unrelated applet.
        set_checked(KEY,before,serialize_entries([x for x in items if x != state['entry']]))
    if needs_unload:
        state['phase']='unload'; save(path,state); wait_unloaded(state['instance'])
    state['phase']='delete-config'; save(path,state)
    if owned_config:
        if not config_ok(state,allow_missing=True): raise RuntimeError('Picker preferences changed during unload')
        guard(state['config']).unlink(missing_ok=True)
    creation=state.get('config_creation')
    if creation:
        temporary=guard(creation['temporary'])
        if temporary.parent != Path(state['config']).parent or not temporary.name.startswith(f'.workspace-{state["instance"]}-'):
            raise RuntimeError('Invalid temporary settings path')
        if temporary.exists():
            info=temporary.stat()
            if (info.st_dev,info.st_ino)!=(creation['device'],creation['inode']):
                raise RuntimeError('Temporary settings ownership changed')
            temporary.unlink()
    action['applied']=False; action['attempted']=False
    state['counter_at_restore']=int(get('next-applet-id')) # deliberately never decremented
    state['status']='restored'; state['phase']='restored'; save(path,state)
    return check(state)


def check(state):
    validate_state(state); problems=[]
    current=entries(get(KEY)); matching=[x for x in current if int(ENTRY.fullmatch(x)[2]) == state['instance']]
    restored=state['status']=='restored'
    if matching != ([] if restored else [state['entry']]): problems.append('picker entry')
    if restored:
        if Path(state['config']).exists(): problems.append('picker settings left behind')
    else:
        if not config_ok(state): problems.append('picker appearance preferences')
        if int(get('next-applet-id')) < state['counter_after']: problems.append('counter regression')
    return dict(ok=not problems, status=state['status'], instance=state['instance'], problems=problems,
                visual_verified=False, workspace_settings_changed=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','apply','check','restore'])
    parser.add_argument('--state',type=Path); parser.add_argument('--commit',action='store_true')
    args=parser.parse_args()
    if args.command=='plan': result=plan()
    elif not args.state: parser.error('--state required')
    elif args.command=='apply':
        state=plan(); result=commit(state,args.state) if args.commit else dict(preview=True,**state)
    else:
        state=json.loads(args.state.read_text())
        result=check(state) if args.command=='check' else restore(state,args.state) if args.commit else dict(preview=True,target='remove picker only')
    print(json.dumps(result,indent=2)); return 0 if result.get('ok',True) else 1

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc: print(json.dumps(dict(ok=False,error=str(exc)),indent=2)); sys.exit(2)
