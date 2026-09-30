import base64, copy, importlib.util, json, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import adapter, engine, render

class ScopedTransaction(unittest.TestCase):
    def fixture(self,root):
        baseline=adapter.plan(settings=False)
        sources=set(baseline['touched_paths'])|set(baseline['protected_hashes'])
        for path in sources:
            p=Path(path)
            if p.is_file() and p.is_relative_to(adapter.HOME):
                target=root/p.relative_to(adapter.HOME);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
        return adapter.plan(root,settings=False)
    def apply(self,state,journal):
        with patch.object(engine,'plan',return_value=state,create=True),patch.object(adapter,'ensure_closed'),patch.object(sys,'argv',['adapter.py','apply','--state',str(journal),'--commit']):engine.main()
    def test_all_scoped_actions_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=self.fixture(root);journal=root/'receipt.json'
            self.apply(state,journal);state=engine.load(journal)
            self.assertEqual(state['status'],'applied')
            for a in state['actions']:self.assertEqual(engine.current(a),a['after'],str(a.get('path')))
            engine.validate_protected(state)
            with patch.object(adapter,'ensure_closed'):engine.restore(state,journal)
            for a in state['actions']:self.assertEqual(engine.current(a),a['before'],str(a.get('path')))
    def test_write_completed_then_failed_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=self.fixture(root);journal=root/'receipt.json';original=engine.write;calls=[0]
            def fails(a,value):
                original(a,value);calls[0]+=1
                if calls[0]==4:raise OSError('fixture interruption after write')
            with patch.object(engine,'write',side_effect=fails):
                with self.assertRaises(OSError):self.apply(state,journal)
            restored=engine.load(journal);self.assertEqual(restored['status'],'rolled-back')
            for a in restored['actions']:self.assertEqual(engine.current(a),a['before'])
    def test_unrelated_ini_edit_survives_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=self.fixture(root);journal=root/'receipt.json';self.apply(state,journal)
            config=root/'.config/kritarc';config.write_text(config.read_text()+'\n[UserFixture]\nKeep=true\n')
            with patch.object(adapter,'ensure_closed'):engine.restore(engine.load(journal),journal)
            self.assertIn('[UserFixture]\nKeep=true',config.read_text())
    def test_protected_backend_drift_prevents_any_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=self.fixture(root);journal=root/'receipt.json';self.apply(state,journal)
            protected=root/adapter.DASH_REL/'backend/media.py';protected.write_text(protected.read_text()+'\n# unrelated drift\n')
            with self.assertRaisesRegex(RuntimeError,'Protected file changed'):engine.restore(engine.load(journal),journal)
            self.assertEqual(engine.current(state['actions'][0]),state['actions'][0]['after'])
    def test_palette_projection_is_runtime_stable_and_long_custom_ip_preserved(self):
        state=adapter.plan(settings=False);ff=adapter.HOME/'.config/fastfetch/config.jsonc';after=json.loads(adapter.project_bytes(ff.read_bytes(),[a for a in state['actions'] if a.get('path')==str(ff)]))
        colors=engine.load(adapter.PRESET/'design.json')['rainbow'];bands=[engine.rgb(c) for c in colors]
        spec=importlib.util.spec_from_file_location('hue_fixture',adapter.HOME/'.config/fastfetch/apply-hue-colors.py');hue=importlib.util.module_from_spec(spec);spec.loader.exec_module(hue)
        for item in after['modules']:
            if isinstance(item,dict) and item.get('type') in hue.GROUP_BAND_INDEX:self.assertEqual(item['keyColor'],hue.rgb_str(bands[hue.GROUP_BAND_INDEX[item['type']]]))
        spec=importlib.util.spec_from_file_location('modes_fixture',adapter.HOME/'.config/fastfetch/modes.py');modes=importlib.util.module_from_spec(spec);spec.loader.exec_module(modes)
        long=modes.build(after,engine.load(adapter.HOME/'.config/fastfetch/long.jsonc'),{'macintosh-soft':bands})
        self.assertIn({'type':'custom','key':'Local IP','format':'86.75-309','keyColor':hue.rgb_str(bands[1])},long['modules'])
        self.assertEqual(after['modules'][0]['format'],'Macintosh Soft')
    def test_opaque_graphite_terminals_and_readable_light_selection(self):
        d=engine.load(adapter.PRESET/'design.json');t=d['palette']
        self.assertIn('background #293238',render.kitty(t,d['terminal_ansi']))
        self.assertIn('Foreground=#EBE9E1',render.ptyxis(t,d['terminal_ansi']))
        self.assertEqual(render.kde(t)['Colors:Selection']['ForegroundNormal'],'#FBF8F1')
        self.assertIn('html,body { background:#EBE9E1 !important;',render.library(t))
    def test_insert_containing_anchor_recognizes_after_state(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'index.html';p.write_text('</head>')
            a={'kind':'text','path':str(p),'before':'</head>','after':'<link>\n</head>','count':1}
            engine.write(a,a['after']);self.assertEqual(engine.current(a),a['after'])
            engine.write(a,a['before']);self.assertEqual(p.read_text(),'</head>')
    def test_no_eww_or_shell_behavior_targets(self):
        state=adapter.plan(settings=False)
        self.assertTrue(all('eww-graphite-brass' not in a.get('path','') and not a.get('path','').endswith('/.bashrc') for a in state['actions']))
        self.assertTrue(all(a.get('path')!=str(adapter.HOME/'.config/fastfetch/long.jsonc') for a in state['actions']))
        self.assertTrue(any(p.endswith('/.bashrc') for p in state['protected_hashes']))

if __name__=='__main__':unittest.main()
