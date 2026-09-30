"""GIMP mixed-preference fixtures: no real app or settings access."""
import importlib.util, json, tempfile, unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('adapter',Path(__file__).with_name('adapter.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class Gimp(unittest.TestCase):
    def test_scope_and_comments(self):
        text='# (theme "comment")\n(theme "Dark")\n(fill-options (theme "nested"))\n(icon-theme "Symbolic")\n(tool "quoted \\" theme")\n'
        patches=a.gimp_appearance_patches(text)
        self.assertEqual(patches,[('(theme "Dark")','(theme "System")'),('(icon-theme "Symbolic")','(icon-theme "Legacy")')])
    def test_refuse_duplicate_or_malformed(self):
        for text in ['(theme "Dark")\n(theme "Light")','(theme "Dark"','(theme foo)', '(nested (theme "Dark"))\n(theme "Dark")']:
            with self.subTest(text=text), self.assertRaises(RuntimeError):a.gimp_appearance_patches(text)
    def test_noop(self):
        self.assertEqual(a.gimp_appearance_patches('(theme "System")\n(icon-theme "Legacy")'),[])
    def test_restore_preserves_unrelated_edits(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'gimprc'; original='# keep\n(theme "Dark")\n(tile-cache-size 12345)\n'
            p.write_text(original);state={'actions':[],'protected_hashes':{}}
            for before,after in a.gimp_appearance_patches(original):
                row={'kind':'text','path':str(p),'before':before,'after':after,'count':1}
                self.assertEqual(a.current(row),before);a.write(row,after);row['applied']=True;state['actions'].append(row)
            p.write_text(p.read_text()+'(undo-levels 99)\n');a.restore(state,Path(td)/'receipt.json')
            self.assertEqual(p.read_text(),original+'(undo-levels 99)\n')
    def test_restore_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'gimprc';p.write_text('(theme "Dark")')
            row={'kind':'text','path':str(p),'before':'(theme "Dark")','after':'(theme "System")','count':1,'applied':True}
            p.write_text('(theme "Light")')
            with self.assertRaises(RuntimeError):a.restore({'actions':[row],'protected_hashes':{}},Path(td)/'receipt.json')
            self.assertEqual(p.read_text(),'(theme "Light")')
    def test_include_after_existing_user_rc_and_restore(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'gtkrc';original='# custom style\n';p.write_text(original)
            include=a.render()['gimp/gtkrc-include.conf']
            row={'kind':'text','path':str(p),'before':'','after':include,'count':1,'applied':True}
            a.write(row,include);p.write_text(p.read_text()+'# unrelated new preference\n')
            a.restore({'actions':[row],'protected_hashes':{}},Path(td)/'receipt.json')
            self.assertEqual(p.read_text(),original+'# unrelated new preference\n')

    def test_symlink_ancestor_refused_even_with_missing_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);real=root/'real';real.mkdir();link=root/'GIMP';link.symlink_to(real,target_is_directory=True)
            for leaf in ['gimprc','gtkrc']:
                with self.assertRaisesRegex(RuntimeError,'symlink'):
                    a.guard_gimp_path(link/'2.10'/leaf,allow_missing_ancestors=True)
    def test_profile_swap_blocks_read_write_and_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);profile=root/'2.10';profile.mkdir();target=profile/'gimprc';target.write_text('(theme "Dark")')
            action={'kind':'text','path':str(target),'before':'(theme "Dark")','after':'(theme "System")','count':1,'gimp_path_guard':True,'applied':True}
            self.assertEqual(a.current(action),action['before'])
            saved=root/'saved';profile.rename(saved);profile.symlink_to(saved,target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError,'symlink'):a.current(action)
            with self.assertRaisesRegex(RuntimeError,'symlink'):a.write(action,action['after'])
            with self.assertRaisesRegex(RuntimeError,'symlink'):a.restore({'actions':[action],'protected_hashes':{}},root/'receipt.json')
            self.assertEqual((saved/'gimprc').read_text(),'(theme "Dark")')
    def test_leaf_symlink_and_nonregular_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);target=root/'gimprc';target.symlink_to(root/'missing')
            with self.assertRaisesRegex(RuntimeError,'symlink'):a.guard_gimp_path(target)
            target.unlink();target.mkdir()
            with self.assertRaisesRegex(RuntimeError,'regular'):a.guard_gimp_path(target)

    def test_crlf_roundtrip_and_unrelated_edit_preserved_byte_exact(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'gimprc'
            original=b'# keep CRLF\r\n(theme\r\n "Dark")\r\n(tile-cache-size 12345)\r\n'
            p.write_bytes(original);state={'actions':[],'protected_hashes':{}}
            for before,after in a.gimp_appearance_patches(a.preserved_text(p)):
                row={'kind':'text','path':str(p),'before':before,'after':after,'count':1,'gimp_path_guard':True}
                self.assertEqual(a.current(row),before);a.write(row,after);row['applied']=True;state['actions'].append(row)
            unrelated=b'# concurrent edit\r\n(undo-levels 99)\r\n'
            p.write_bytes(p.read_bytes()+unrelated)
            a.restore(state,root/'receipt.json')
            self.assertEqual(p.read_bytes(),original+unrelated)
            rc=root/'gtkrc';initial=b'# custom GTK rc\r\n';rc.write_bytes(initial)
            include=a.render()['gimp/gtkrc-include.conf']
            row={'kind':'text','path':str(rc),'before':'','after':include,'count':1,'gimp_path_guard':True,'applied':True}
            a.write(row,include);self.assertTrue(rc.read_bytes().startswith(initial))
            a.restore({'actions':[row],'protected_hashes':{}},root/'rc-receipt.json')
            self.assertEqual(rc.read_bytes(),initial)

if __name__=='__main__':unittest.main()
