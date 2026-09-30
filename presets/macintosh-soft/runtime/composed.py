"""Immutable, check-only receipt projections through ordered applied layers.

No live configuration is read or written here. Expectations come exclusively
from recorded before/after values, with overlap continuity checked. Call normal
adapter checks on the yielded copies; never use copies for apply or restore.
"""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile

MISSING = {'__macintosh_soft_missing__': True}


def lookup(obj, keys):
    try:
        for key in keys: obj = obj[key]
        return obj
    except (KeyError, IndexError, TypeError): return deepcopy(MISSING)


def put(obj, keys, value):
    for key in keys[:-1]:
        obj = obj.setdefault(key, {}) if isinstance(obj, dict) else obj[key]
    if value == MISSING:
        if not isinstance(obj, dict): raise RuntimeError('Cannot project removed array element')
        obj.pop(keys[-1], None)
    else: obj[keys[-1]] = deepcopy(value)


def ini_get(text, section, key):
    head = re.search(r'^\[' + re.escape(section) + r'\]\s*$', text, re.M)
    if not head: return deepcopy(MISSING)
    tail = re.search(r'^\[', text[head.end():], re.M)
    end = head.end() + tail.start() if tail else len(text)
    match = re.search(r'^' + re.escape(key) + r'\s*=(.*)$', text[head.end():end], re.M)
    return match.group(1).strip() if match else deepcopy(MISSING)


def ini_set(text, section, key, value):
    # Match applications/adapter.py serialization, including retained spacing.
    head = re.search(r'^\[' + re.escape(section) + r'\]\s*$', text, re.M)
    if not head:
        return text if value == MISSING else text.rstrip() + '\n\n[' + section + ']\n' + key + '=' + value + '\n'
    tail = re.search(r'^\[', text[head.end():], re.M)
    end = head.end() + tail.start() if tail else len(text)
    middle = text[head.end():end]
    pattern = r'^' + re.escape(key) + r'\s*=.*(?:\n|$)'
    if re.search(pattern, middle, re.M):
        middle = re.sub(pattern, '' if value == MISSING else key + '=' + value + '\n', middle, count=1, flags=re.M)
    elif value != MISSING: middle = middle.rstrip() + '\n' + key + '=' + value + '\n\n'
    return text[:head.end()] + middle + text[end:]


def decode(value):
    if value == MISSING: raise RuntimeError('Missing-file projection is unsupported')
    return base64.b64decode(value, validate=True)


def encode(value): return base64.b64encode(value).decode()


def require(actual, expected, action):
    if actual != expected:
        raise RuntimeError('Receipt overlap is discontinuous: ' + str(action.get('path', action.get('key'))))


def read_bytes(action, raw):
    kind = action['kind']
    if kind == 'file': return encode(raw)
    if kind == 'json': return lookup(json.loads(raw), action['keys'])
    text = raw.decode()
    if kind == 'ini': return ini_get(text, action['section'], action['key'])
    if kind == 'suffix':
        if text.endswith(action['after']): return action['after']
        if action['after'] not in text: return ''
        raise RuntimeError('Suffix moved in receipt projection')
    if kind == 'text':
        if action['after'] and text.count(action['after']) == action['count']: return action['after']
        if action['before'] and text.count(action['before']) == action['count']: return action['before']
        if not action['before'] and action['after'] not in text: return ''
        raise RuntimeError('Text expectation not found in recorded bytes')
    raise RuntimeError('Unsupported byte projection: ' + kind)


def apply_bytes(raw, later):
    kind = later['kind']
    # Read the later action at its recorded before state, without consulting live.
    if kind == 'file':
        require(encode(raw), later['before'], later)
        return decode(later['after'])
    if kind == 'json':
        data = json.loads(raw)
        require(lookup(data, later['keys']), later['before'], later)
        put(data, later['keys'], later['after'])
        return (json.dumps(data, indent=2) + '\n').encode()
    text = raw.decode()
    if kind == 'ini':
        require(ini_get(text, later['section'], later['key']), later['before'], later)
        return ini_set(text, later['section'], later['key'], later['after']).encode()
    if kind == 'text':
        before = later['before']
        if before:
            require(text.count(before), later['count'], later)
            return text.replace(before, later['after']).encode()
        require(later['after'] in text, False, later)
        return (text + later['after']).encode()
    if kind == 'suffix':
        require(later['before'], '', later)
        require(later['after'] in text, False, later)
        return (text + later['after']).encode()
    raise RuntimeError('Unsupported whole-file overlay kind: ' + kind)


def target(action):
    if 'path' in action: return ('path', action['path'])
    return (action['kind'], action.get('schema'), action.get('key'))


def project_action(original, later):
    result = deepcopy(original)
    if target(result) != target(later): return result
    old_kind, kind = result['kind'], later['kind']
    if old_kind == 'file':
        result['after'] = encode(apply_bytes(decode(result['after']), later))
    elif kind == 'file':
        before, after = decode(later['before']), decode(later['after'])
        require(read_bytes(result, before), result['after'], later)
        # A full-file replacement has a full-file expectation. Promote this
        # check-only action to that stricter scope; preserve original before.
        result.update(kind='file', after=encode(after))
    elif old_kind == kind == 'json':
        old_keys, keys = result['keys'], later['keys']
        common = min(len(old_keys), len(keys))
        if old_keys[:common] != keys[:common]: return result
        if len(old_keys) >= len(keys):
            tail = old_keys[len(keys):]
            require(lookup(later['before'], tail), result['after'], later)
            result['after'] = deepcopy(lookup(later['after'], tail))
        else:
            tail = keys[len(old_keys):]
            require(lookup(result['after'], tail), later['before'], later)
            put(result['after'], tail, later['after'])
    elif old_kind == kind == 'ini':
        if (result['section'], result['key']) != (later['section'], later['key']): return result
        require(result['after'], later['before'], later)
        result['after'] = deepcopy(later['after'])
    elif old_kind == kind and kind in {'gsetting', 'dconf'}:
        require(result['after'], later['before'], later)
        result['after'] = deepcopy(later['after'])
    elif old_kind in {'text', 'suffix'} and kind == 'text':
        before = later['before']
        if before and before in result['after']:
            require(result['after'].count(before), later['count'], later)
            result['after'] = result['after'].replace(before, later['after'])
        elif before and result['after'] and result['after'] in before:
            raise RuntimeError('Text overlap needs a full-file receipt: ' + result['path'])
        # Disjoint literal changes leave the older literal expectation intact.
    else:
        raise RuntimeError('Unsupported overlapping receipt kinds: ' + old_kind + '/' + kind)
    return result


def actions(state):
    return [action for action in state.get('actions', []) if action.get('applied')]


def project_state(state, later_states, allowed_protected=()):
    out = deepcopy(state)
    allowed = {str(Path(path)) for path in allowed_protected}
    for later in later_states:
        for action in actions(later):
            out['actions'] = [project_action(old, action) if old.get('applied') else old for old in out.get('actions', [])]
            # Core controller records exact bytes plus mode rather than actions.
            if isinstance(out.get('files'), dict) and action.get('path') in out['files']:
                record = out['files'][action['path']]['after']
                if record.get('kind') != 'file': raise RuntimeError('Core overlay requires a regular file')
                synthetic = {'kind':'file','path':action['path'],'after':record['data']}
                record['data'] = project_action(synthetic, action)['after']
            # Core GSettings use user (before) / after records.
            for record in out.get('settings', []):
                if action['kind'] == 'gsetting' and (record['schema'], record['key']) == (action['schema'], action['key']):
                    require(record['after'], action['before'], action)
                    record['after'] = deepcopy(action['after'])
        for edit in later.get('protected_edits', []):
            path = edit['path']
            protected_hash = path in out.get('protected_hashes', {})
            prompt = out.get('protected_prompt')
            protected_prompt = bool(prompt and prompt.get('path') == path)
            if not protected_hash and not protected_prompt: continue
            if path not in allowed: raise RuntimeError('Protected override not explicitly authorized: ' + path)
            writes = [action for action in actions(later) if action.get('path') == path]
            if not writes: raise RuntimeError('Protected hash declaration has no applied action: ' + path)
            if protected_hash: require(out['protected_hashes'][path], edit['before_sha256'], edit)
            # Full-file receipts provide a self-contained declaration proof.
            full = [action for action in writes if action['kind'] == 'file']
            if protected_prompt and (not full or writes[0]['kind'] != 'file'):
                raise RuntimeError('Protected prompt projection requires a leading full-file receipt: ' + path)
            if full:
                require(hashlib.sha256(decode(full[0]['before'])).hexdigest(), edit['before_sha256'], edit)
                raw = decode(full[0]['before'])
                if protected_prompt:
                    suffix = prompt['suffix'].encode()
                    if not suffix or not raw.endswith(suffix):
                        raise RuntimeError('Protected prompt suffix missing from recorded before bytes: ' + path)
                    require(hashlib.sha256(raw.removesuffix(suffix)).hexdigest(), prompt['sha256'], edit)
                for action in writes: raw = apply_bytes(raw, action)
                require(hashlib.sha256(raw).hexdigest(), edit['after_sha256'], edit)
            else:
                # Narrow text receipts must provide full snapshots solely for
                # proving their hash transition; no live hash rebasing allowed.
                raw = decode(edit['before_base64'])
                require(hashlib.sha256(raw).hexdigest(), edit['before_sha256'], edit)
                for action in writes: raw = apply_bytes(raw, action)
                require(hashlib.sha256(raw).hexdigest(), edit['after_sha256'], edit)
            if protected_hash: out['protected_hashes'][path] = edit['after_sha256']
            if protected_prompt:
                if not raw.endswith(suffix):
                    raise RuntimeError('Protected prompt suffix must remain at the end: ' + path)
                prompt['sha256'] = hashlib.sha256(raw.removesuffix(suffix)).hexdigest()
    return out


@contextmanager
def receipts(paths, allowed_protected=()):
    """Yield {original Path: temporary check-only Path}; paths are apply order.

    Normal checks of every yielded receipt are required, including the final
    layer, to establish topmost live values. No restore may use these copies.
    """
    ordered = [Path(path) for path in paths if Path(path).exists()]
    if len(set(ordered)) != len(ordered): raise RuntimeError('Duplicate composed receipt')
    originals = [path.read_bytes() for path in ordered]
    states = [json.loads(raw) for raw in originals]
    with tempfile.TemporaryDirectory(prefix='plum-check-only-') as directory:
        result = {}
        for index, (path, state) in enumerate(zip(ordered, states)):
            target_path = Path(directory) / (str(index) + '-' + path.name)
            projected = project_state(state, states[index + 1:], allowed_protected)
            target_path.write_text(json.dumps(projected, indent=2) + '\n')
            target_path.chmod(0o600)
            result[path] = target_path
        yield result
        for path, raw in zip(ordered, originals):
            if path.read_bytes() != raw: raise RuntimeError('Original receipt changed during composed check: ' + str(path))
