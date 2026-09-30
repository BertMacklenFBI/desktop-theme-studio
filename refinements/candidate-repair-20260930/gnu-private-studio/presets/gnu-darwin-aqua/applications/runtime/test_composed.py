from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import composed


def action(kind, before, after, **fields):
    return dict(kind=kind, before=before, after=after, applied=True, **fields)


class ComposedTests(unittest.TestCase):
    def test_json_ancestor_replacement_handles_removed_old_fields(self):
        old = action('json', 'old', 'rose', path='/config', keys=['modules', 1, 'keyColor'])
        later = action('json', [{'keyColor':'cream'},{'keyColor':'rose'}], [{'type':'os'}], path='/config', keys=['modules'])
        projected = composed.project_action(old, later)
        self.assertEqual(projected['after'], composed.MISSING)
        self.assertEqual(projected['before'], 'old')
        self.assertEqual(old['after'], 'rose')

    def test_json_descendant_preserves_unrelated_expected_fields(self):
        old = action('json', {}, {'width':30,'source':'original-art'}, path='/config', keys=['logo'])
        later = action('json', 30, 18, path='/config', keys=['logo','width'])
        projected = composed.project_action(old, later)
        self.assertEqual(projected['after'], {'width':18,'source':'original-art'})
        with self.assertRaises(RuntimeError):
            composed.project_action(old, dict(later, before=999))

    def test_whole_json_file_retains_unrelated_drift_detection(self):
        before = json.dumps({'logo':{'width':30},'protected':'preserved'}).encode()
        old = action('file', 'original', composed.encode(before), path='/config')
        later = action('json', 30, 18, path='/config', keys=['logo','width'])
        projected = composed.project_action(old, later)
        expected = composed.decode(projected['after'])
        self.assertEqual(json.loads(expected), {'logo':{'width':18},'protected':'preserved'})
        drifted = json.dumps({'logo':{'width':18},'protected':'altered'}).encode()
        self.assertNotEqual(composed.encode(drifted), projected['after'])
        self.assertEqual(projected['before'], 'original')

    def test_ini_file_overlay_preserves_other_sections_and_keys(self):
        raw = b'[Profile]\nfont=Fixture Mono 10\npalette=plum\n\n[Other]\nvalue=keep\n'
        old = action('file', 'old', composed.encode(raw), path='/keyfile')
        later = action('ini', 'Fixture Mono 10', 'Fixture Mono 11', path='/keyfile', section='Profile', key='font')
        expected = composed.decode(composed.project_action(old, later)['after']).decode()
        self.assertIn('font=Fixture Mono 11', expected)
        self.assertIn('palette=plum', expected)
        self.assertIn('[Other]\nvalue=keep', expected)

    def test_gsetting_overlap_is_precise(self):
        old = action('gsetting', 'default', "'Fixture Mono 10'", schema='profile', key='font')
        later = action('gsetting', "'Fixture Mono 10'", "'Fixture Mono 11'", schema='profile', key='font')
        self.assertEqual(composed.project_action(old,later)['after'], "'Fixture Mono 11'")
        self.assertEqual(composed.project_action(old,dict(later,key='unrelated')), old)

    def test_text_then_file_promotes_to_strict_full_content(self):
        old = action('text', 'old label', 'new label', path='/ui', count=1)
        later = action('file', composed.encode(b'new label\nother'), composed.encode(b'final label\nother'), path='/ui')
        projected = composed.project_action(old,later)
        self.assertEqual(projected['kind'], 'file')
        self.assertEqual(projected['after'], later['after'])
        self.assertEqual(projected['before'], 'old label')

    def test_file_then_text_preserves_unmodified_bytes(self):
        old = action('file', 'saved', composed.encode(b'old label\nother\n'), path='/ui')
        later = action('text', 'old label', 'new label', path='/ui', count=1)
        self.assertEqual(composed.decode(composed.project_action(old,later)['after']), b'new label\nother\n')
        with self.assertRaises(RuntimeError):
            composed.project_action(old,dict(later,count=2))

    def test_protected_hash_requires_allowlist_and_provable_transition(self):
        before, after = b'old label\nother\n', b'new label\nother\n'
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        old = {'protected_hashes':{'/ui':digest(before)}}
        later = {'actions':[action('text','old label','new label',path='/ui',count=1)],
                 'protected_edits':[{'path':'/ui','before_sha256':digest(before),'after_sha256':digest(after),'before_base64':composed.encode(before)}]}
        with self.assertRaises(RuntimeError): composed.project_state(old,[later])
        projected = composed.project_state(old,[later],['/ui'])
        self.assertEqual(projected['protected_hashes']['/ui'],digest(after))
        broken = deepcopy(later); broken['protected_edits'][0]['after_sha256'] = digest(b'unrelated edit')
        with self.assertRaises(RuntimeError): composed.project_state(old,[broken],['/ui'])
        self.assertEqual(old['protected_hashes']['/ui'],digest(before))

    def test_core_file_mode_and_before_preserved(self):
        old = {'files':{'/config':{'before':{'kind':'missing'},'after':{'kind':'file','data':composed.encode(b'{"width":30,"keep":1}'),'mode':420}}}}
        later = {'actions':[action('json',30,18,path='/config',keys=['width'])]}
        projected = composed.project_state(old,[later])
        self.assertEqual(projected['files']['/config']['before'], old['files']['/config']['before'])
        self.assertEqual(projected['files']['/config']['after']['mode'],420)
        self.assertEqual(json.loads(composed.decode(projected['files']['/config']['after']['data'])),{'width':18,'keep':1})

    def test_context_leaves_originals_immutable_and_removes_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            first, last = Path(directory)/'first.json', Path(directory)/'last.json'
            first.write_text(json.dumps({'actions':[action('json',10,30,path='/config',keys=['width'])]}))
            last.write_text(json.dumps({'actions':[action('json',30,18,path='/config',keys=['width'])]}))
            original = first.read_bytes()
            with composed.receipts([first,last]) as copies:
                temporary = copies[first]
                self.assertEqual(json.loads(temporary.read_text())['actions'][0]['after'],18)
                self.assertEqual(first.read_bytes(), original)
            self.assertFalse(temporary.exists())
            self.assertEqual(first.read_bytes(), original)

    def test_chained_layers_validate_each_transition(self):
        original = {'actions':[action('json',5,10,path='/config',keys=['width'])]}
        middle = {'actions':[action('json',10,30,path='/config',keys=['width'])]}
        final = {'actions':[action('json',30,18,path='/config',keys=['width'])]}
        self.assertEqual(composed.project_state(original,[middle,final])['actions'][0]['after'],18)
        with self.assertRaises(RuntimeError): composed.project_state(original,[final,middle])

    def prompt_fixture(self):
        suffix = '\n# Fixture Theme prompt\n. "$HOME/.config/fixture-theme/prompt.bash"\n'
        body = b'# existing shell\nfastfetch\n'
        changed = b'# existing shell\nfastfetch --short\n'
        before, after = body + suffix.encode(), changed + suffix.encode()
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        original = {'protected_prompt':{'path':'/shell','suffix':suffix,'sha256':digest(body)},
                    'actions':[action('suffix','',suffix,path='/shell')]}
        later = {'actions':[action('file',composed.encode(before),composed.encode(after),path='/shell')],
                 'protected_edits':[{'path':'/shell','before_sha256':digest(before),'after_sha256':digest(after)}]}
        return original,later,body,changed

    def test_prompt_projection_preserves_suffix_before_and_original_receipt(self):
        original,later,body,changed = self.prompt_fixture()
        saved = deepcopy(original)
        projected = composed.project_state(original,[later],['/shell'])
        self.assertEqual(projected['protected_prompt']['sha256'],hashlib.sha256(changed).hexdigest())
        self.assertEqual(projected['protected_prompt']['suffix'],original['protected_prompt']['suffix'])
        self.assertEqual(projected['actions'][0]['before'],'')
        self.assertEqual(original,saved)
        self.assertNotEqual(projected['protected_prompt']['sha256'],hashlib.sha256(changed+b'# drift').hexdigest())

    def test_prompt_projection_requires_exact_path_allowlist(self):
        original,later,_,_ = self.prompt_fixture()
        with self.assertRaisesRegex(RuntimeError,'not explicitly authorized'):
            composed.project_state(original,[later],['/other-shell'])

    def test_prompt_projection_rejects_before_body_or_declared_hash_drift(self):
        original,later,_,_ = self.prompt_fixture()
        original['protected_prompt']['sha256'] = hashlib.sha256(b'different original shell').hexdigest()
        with self.assertRaisesRegex(RuntimeError,'discontinuous'):
            composed.project_state(original,[later],['/shell'])
        for field in ['before_sha256','after_sha256']:
            original,later,_,_ = self.prompt_fixture()
            later['protected_edits'][0][field] = '0'*64
            with self.assertRaisesRegex(RuntimeError,'discontinuous'):
                composed.project_state(original,[later],['/shell'])

    def test_prompt_projection_rejects_removed_or_moved_suffix(self):
        for mode in ['removed','moved']:
            original,later,_,changed = self.prompt_fixture()
            after = changed if mode == 'removed' else original['protected_prompt']['suffix'].encode()+changed
            later['actions'][0]['after'] = composed.encode(after)
            later['protected_edits'][0]['after_sha256'] = hashlib.sha256(after).hexdigest()
            with self.assertRaisesRegex(RuntimeError,'suffix must remain'):
                composed.project_state(original,[later],['/shell'])

    def test_prompt_projection_rejects_narrow_action_without_full_file(self):
        original,later,_,_ = self.prompt_fixture()
        original['actions'] = []
        later['actions'] = [action('text','fastfetch','fastfetch --short',path='/shell',count=1)]
        with self.assertRaisesRegex(RuntimeError,'leading full-file'):
            composed.project_state(original,[later],['/shell'])


if __name__ == '__main__': unittest.main()
