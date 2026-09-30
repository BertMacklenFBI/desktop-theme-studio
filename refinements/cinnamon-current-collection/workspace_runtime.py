"""Receipt-owned native workspace runtime transitions, restricted to this host.

Planning is read-only. The existing metadata-aware tree engine performs every
copy/rename/restore. An epoch retains the user's actual original backup across
successive opted and non-opted themes. Foreign/absent baselines fail closed.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time

UUID = 'workspace-switcher@cinnamon.org'
POINTER = 'workspace-runtime-epoch.json'
SCHEMA = 1


def read_regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Workspace runtime requires a regular file: ' + str(path))
    return json.loads(path.read_text())


def require_receipt(slug, profile, root, loader):
    if profile.get('workspace_geometry') is None:
        return None
    receipt = Path(root) / 'verification/workspace-latest' / (slug + '.json')
    raw = read_regular(receipt)
    gate = loader('workspace_activation_gate', Path(root) / 'workspace_gate.py')
    result = gate.verify_workspace_receipt(slug, profile, raw, root)
    if not result['ok']:
        raise RuntimeError('Current workspace acceptance required: ' + result['reason'])
    return {'path': str(receipt), 'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(),
            'bindings': result['bindings']}


def require_activation(slug, profile, root, loader):
    result = require_receipt(slug, profile, root, loader)
    candidates = {'dusk-ribbons', 'macintosh-soft', 'macintosh-soft-evergreen',
                  'moonstone-stereo', 'ocean-silk', 'plum-afterglow', 'quiet-sage',
                  'gnu-darwin-aqua', 'gnu-darwin-workstation'}
    if slug in candidates:
        gate = loader('workspace_candidate_release', Path(root) / 'contributions/all-theme-plan/review_release.py')
        evidence = Path(root) / 'contributions/all-theme-plan/review-release' / slug
        value = read_regular(evidence / 'accepted.json')
        checked = gate.validate_receipt(slug, gate.package_fingerprint(slug)['sha256'], value, evidence)
        if not checked['release_eligible']:
            raise RuntimeError('Candidate release evidence is stale or incomplete: ' + '; '.join(checked['problems'][:5]))
    return result


def owner(root):
    path = Path(root) / 'state' / POINTER
    if not os.path.lexists(path):
        return None
    value = read_regular(path)
    if value.get('schema') != SCHEMA or value.get('phase') not in ('writing', 'pending', 'kept'):
        raise RuntimeError('Workspace runtime owner is incomplete or unsupported')
    receipt = Path(value.get('receipt', ''))
    if receipt.name != 'receipt.json' or receipt.parent.parent != Path(root) / 'state':
        raise RuntimeError('Foreign workspace runtime owner receipt')
    data = read_regular(receipt)
    section = data.get('workspace_runtime')
    if not isinstance(section, dict) or section.get('target') != value.get('target'):
        raise RuntimeError('Workspace runtime owner disagrees with its receipt')
    return value


def validate_baseline(record, root, engine):
    source = Path(record.get('source', ''))
    try:
        relative = source.relative_to(Path(root) / 'state')
    except ValueError:
        raise RuntimeError('Foreign workspace baseline backup') from None
    if len(relative.parts) != 3 or relative.parts[1] != 'trees' or not relative.parts[2].endswith('.before'):
        raise RuntimeError('Invalid workspace baseline backup path')
    if source.is_symlink() or not source.is_dir() or engine.tree_fingerprint(source) != record.get('fingerprint'):
        raise RuntimeError('Workspace original baseline backup changed or is missing')
    return source


def plan(slug, profile, root, home, engine):
    root, home = Path(root), Path(home)
    previous = owner(root)
    opted = profile.get('workspace_geometry') is not None
    if not opted and previous is None:
        return None, None
    target = home / '.local/share/cinnamon/applets' / UUID
    # Forward selection of absence is intentionally unsupported. No planner
    # creates a namespace or replaces another workstation's custom applet.
    if target.is_symlink() or not target.is_dir() or not target.parent.is_dir():
        raise RuntimeError('Workspace integration needs an existing regular user-local native runtime')
    for ancestor in [target.parent, *target.parent.parents]:
        if ancestor.is_symlink():
            raise RuntimeError('Workspace namespace has an unsupported symlink ancestor')
    if read_regular(target / 'metadata.json').get('uuid') != UUID:
        raise RuntimeError('Workspace baseline has a foreign native identity')
    baseline = None
    if previous is None:
        original = root / 'runtime' / UUID
        if engine.tree(target) != engine.tree(original):
            raise RuntimeError('Unowned workspace runtime differs from the reviewed host baseline')
    else:
        if previous['phase'] != 'kept' or previous['target'] != str(target):
            raise RuntimeError('Resolve the prior workspace runtime trial before switching')
        prior_data = read_regular(previous['receipt'])
        prior = prior_data['workspace_runtime']
        item = prior_data['trees'][prior['tree_index']]
        if prior_data.get('status') != 'kept' or engine.tree_fingerprint(target) != item.get('installed_fingerprint'):
            raise RuntimeError('Owned workspace runtime changed externally')
        baseline = prior['baseline']
        validate_baseline(baseline, root, engine)
    source = root / 'runtime/workspace-switcher-rounded' if opted else validate_baseline(baseline, root, engine)
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError('Reviewed workspace source is missing')
    if read_regular(source / 'metadata.json').get('uuid') != UUID:
        raise RuntimeError('Reviewed workspace source has a foreign native identity')
    row = {'source': str(source), 'target': str(target), 'sha': engine.tree(source),
           'before_sha': engine.tree_state(target), 'workspace_runtime': True}
    section = {'schema': SCHEMA, 'uuid': UUID, 'target': str(target), 'profile': slug,
               'operation': 'to-rounded' if opted else 'to-baseline',
               'previous_owner': previous, 'baseline': baseline, 'phase': 'planned',
               'before_applet_sha256': hashlib.sha256((target / 'applet.js').read_bytes()).hexdigest(),
               'after_applet_sha256': hashlib.sha256((source / 'applet.js').read_bytes()).hexdigest(),
               'portability': 'Existing reviewed regular host baseline only; absent/foreign baseline refused.'}
    return section, row


def assert_owner(path, data, root, engine=None):
    section = data.get('workspace_runtime')
    if not section:
        return
    observed = owner(root)
    if observed is not None and observed.get('receipt') == str(path):
        return
    if section.get('phase') == 'planned' and observed == section['previous_owner']:
        return
    item = data['trees'][section['tree_index']]
    if not item.get('attempted') and observed == section['previous_owner']:
        return
    if section.get('phase') == 'releasing' and observed == section['previous_owner']:
        if (engine is None or not item.get('restored')
                or engine.tree_fingerprint(section['target']) != item['before_fingerprint']
                or section.get('reload', {}).get('status') != 'passed'
                or not section['reload'].get('restoring')):
            raise RuntimeError('Workspace restore handoff evidence is incomplete or changed')
        return
    if (data.get('status') == 'restored' or section.get('phase') == 'restored') and observed == section['previous_owner']:
        return
    raise RuntimeError('Workspace runtime restore/order/owner conflict')


def begin(path, data, root, engine, journal):
    section = data.get('workspace_runtime')
    if not section:
        return
    if not data.get('guard_armed'):
        raise RuntimeError('Workspace runtime mutation requires armed recovery')
    assert_owner(path, data, root)
    item = data['trees'][section['tree_index']]
    if section['baseline'] is None:
        section['baseline'] = {'source': item['backup'], 'fingerprint': item['before_fingerprint'],
                               'content_sha256': item['before_sha']}
    section['phase'] = 'writing'
    journal(path, data)
    engine.E.journal(Path(root) / 'state' / POINTER,
                     {'schema': SCHEMA, 'receipt': str(path), 'target': section['target'], 'phase': 'writing'})


def snapshot_settings(home):
    """Only the native spice files and appearance-neutral workspace keys."""
    from gi.repository import Gio
    directory = Path(home) / '.config/cinnamon/spices' / UUID
    files = {str(p): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'mode': p.stat().st_mode & 0o777}
             for p in sorted(directory.glob('*.json')) if p.is_file() and not p.is_symlink()}
    keys = {}
    for schema, names in [('org.cinnamon', ('enabled-applets',)),
                          ('org.cinnamon.desktop.wm.preferences', ('num-workspaces', 'workspace-names'))]:
        settings = Gio.Settings.new(schema)
        for key in names:
            keys[schema + '/' + key] = settings.get_value(key).print_(True)
    enabled = Gio.Settings.new('org.cinnamon').get_strv('enabled-applets')
    expected_ids = sorted(int(row.split(':')[4]) for row in enabled
                          if len(row.split(':')) >= 5 and row.split(':')[3] == UUID)
    return {'files': files, 'settings': keys, 'expected_native_ids': expected_ids}


def evaluate(code):
    from gi.repository import Gio, GLib
    reply = Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
        'org.Cinnamon', '/org/Cinnamon', 'org.Cinnamon', 'Eval', GLib.Variant('(s)', (code,)),
        GLib.VariantType('(bs)'), Gio.DBusCallFlags.NONE, 4000, None).unpack()
    if not reply[0]:
        raise RuntimeError('Workspace native source inspection failed')
    return json.loads(reply[1])


def reload_checked(data, bus, home, *, evaluator=evaluate, snapshot=snapshot_settings,
                   monotonic=time.monotonic, sleep=time.sleep):
    section = data.get('workspace_runtime')
    if not section:
        return
    section.pop('reload', None)
    item = data['trees'][section['tree_index']]
    restoring = bool(data.get('workspace_restoring') or item.get('restored'))
    if restoring and not item.get('attempted'):
        section['reload'] = {'status': 'not-mutated', 'restoring': True}
        return
    expected = section['before_applet_sha256'] if restoring else section['after_applet_sha256']
    target = Path(section['target'])
    if hashlib.sha256((target / 'applet.js').read_bytes()).hexdigest() != expected:
        raise RuntimeError('Workspace source changed before native reload')
    before = snapshot(home)
    expected_ids = before.get('expected_native_ids')
    if not isinstance(expected_ids, list) or any(type(value) is not int for value in expected_ids):
        raise RuntimeError('Native workspace expected instance identities are absent')
    evaluator("(function(){global.__dtsWorkspaceBefore=imports.ui.appletManager.filterDefinitionsByUUID(" + json.dumps(UUID) + ").map(d=>d.applet);return JSON.stringify({ready:true});})()")
    bus('org.Cinnamon.ReloadXlet', UUID, 'APPLET')
    deadline = monotonic() + 8
    code = "(function(){const e=imports.ui.extension.getExtension(" + json.dumps(UUID) + ");const d=imports.ui.appletManager.filterDefinitionsByUUID(" + json.dumps(UUID) + ");return JSON.stringify({path:e&&e.dir.get_path(),instances:d.map(x=>({id:x.applet_id,ready:!!(x.applet&&x.applet.actor&&x.applet.actor.mapped),recreated:!!x.applet&&global.__dtsWorkspaceBefore.indexOf(x.applet)<0}))});})()"
    last = None
    while monotonic() < deadline:
        last = evaluator(code)
        instances = last.get('instances', [])
        ids_match = sorted(int(x['id']) for x in instances) == sorted(expected_ids)
        loaded = (last.get('path') == str(target) and bool(instances)
                  and all(x.get('ready') and x.get('recreated') for x in instances))
        absent = not expected_ids and not instances and last.get('path') in (None, str(target))
        if ids_match and (loaded or absent):
            if snapshot(home) != before:
                raise RuntimeError('Native reload changed workspace spice files/settings')
            if hashlib.sha256((target / 'applet.js').read_bytes()).hexdigest() != expected:
                raise RuntimeError('Workspace source changed during native reload')
            section['reload'] = {'status': 'passed', 'restoring': restoring, 'expected_applet_sha256': expected,
                                 'source': last, 'spice_settings_preserved': True,
                                 'expected_native_ids': expected_ids,
                                 'activation': 'recreated' if loaded else 'intentionally-absent'}
            return
        sleep(.1)
    raise RuntimeError('Workspace native reconstruction did not settle: ' + json.dumps(last))


def finish(path, data, root, engine, phase, journal):
    section = data.get('workspace_runtime')
    if not section:
        return
    assert_owner(path, data, root)
    if section.get('reload', {}).get('status') != 'passed' or section['reload'].get('restoring'):
        raise RuntimeError('Workspace runtime cannot commit without a verified native reload')
    item = data['trees'][section['tree_index']]
    if engine.tree_fingerprint(section['target']) != item.get('installed_fingerprint'):
        raise RuntimeError('Workspace runtime changed before commit')
    validate_baseline(section['baseline'], root, engine)
    section['phase'] = phase
    journal(path, data)
    engine.E.journal(Path(root) / 'state' / POINTER,
                     {'schema': SCHEMA, 'receipt': str(path), 'target': section['target'], 'phase': phase})


def restore_owner(path, data, root, engine, journal=None):
    section = data.get('workspace_runtime')
    if not section:
        return
    assert_owner(path, data, root, engine)
    item = data['trees'][section['tree_index']]
    if item.get('attempted'):
        if not item.get('restored') or engine.tree_fingerprint(section['target']) != item['before_fingerprint']:
            raise RuntimeError('Workspace runtime restoration is incomplete')
        if not section.get('reload', {}).get('restoring') or section['reload'].get('status') != 'passed':
            raise RuntimeError('Workspace restored native code has not settled')
    # Persist restored-tree proof before releasing the pointer. A crash after
    # pointer unlink/rename remains recoverable through this exact handoff.
    section['phase'] = 'releasing'
    (journal or engine.E.journal)(path, data)
    pointer = Path(root) / 'state' / POINTER
    if section['previous_owner'] is None:
        pointer.unlink(missing_ok=True)
        engine.sync_directory(pointer.parent)
    else:
        engine.E.journal(pointer, section['previous_owner'])
    section['phase'] = 'restored'
