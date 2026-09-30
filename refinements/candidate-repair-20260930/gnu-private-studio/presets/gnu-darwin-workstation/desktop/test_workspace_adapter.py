"""No display or live settings: transactions against temp profiles and mocked backend."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import workspace_adapter as w


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name); self.receipt=self.home/'state.json'
        self.paths=[self.home/'xdg/cinnamon/spices'/w.UUID/'50.json',self.home/'.cinnamon/configs'/w.UUID/'50.json']
        self.initial=['panel2:left:0:menu@cinnamon.org:17']
        self.values={w.KEY:w.serialize_entries(self.initial),'next-applet-id':'50','panels-enabled':"['2:0:bottom']"}
        self.events=[]
        self.addCleanup(patch.stopall)
        patch.object(w,'config_paths',lambda ident:[p.with_name(f'{ident}.json') for p in self.paths]).start()
        patch.object(w,'get',lambda key:self.values[key]).start()
        patch.object(w,'set_checked',self.set_value).start()
        patch.object(w,'wait_unloaded',self.unload).start()

    def set_value(self,key,before,after):
        if self.values[key]!=before: raise RuntimeError('Concurrent GSettings change')
        journal=json.loads(self.receipt.read_text())
        self.events.append((key,journal['phase']))
        if key==w.KEY and w.UUID in after:
            self.assertTrue(self.paths[0].exists())
            self.assertEqual(journal['phase'],'enable')
        self.values[key]=after

    def unload(self,ident):
        self.assertNotIn(w.UUID,self.values[w.KEY]); self.events.append(('unload',ident))

    def apply(self):
        state=w.plan(); self.assertTrue(w.commit(state,self.receipt)['ok']); return state

    def test_plan_readonly_and_reserves_fresh_instance(self):
        state=w.plan(); self.assertEqual(state['instance'],50)
        self.assertEqual(list(self.home.iterdir()),[])
        self.assertEqual(state['entry'],f'panel2:center:0:{w.UUID}:50')
        self.assertEqual(state['config_data']['display-type']['value'],'buttons')

    def test_roundtrip_retains_counter_and_restores_only_picker(self):
        state=self.apply()
        self.assertEqual(self.events[:2],[('next-applet-id','reserve-counter'),(w.KEY,'enable')])
        other='panel2:right:9:other@fixture:51'
        self.values[w.KEY]=w.serialize_entries(w.entries(self.values[w.KEY])+[other])
        self.values['next-applet-id']='52'
        self.assertTrue(w.restore(state,self.receipt)['ok'])
        self.assertEqual(w.entries(self.values[w.KEY]),self.initial+[other])
        self.assertEqual(self.values['next-applet-id'],'52')
        self.assertFalse(self.paths[0].exists()); self.assertIn(('unload',50),self.events)

    def test_cinnamon_metadata_normalization_allowed(self):
        state=self.apply(); data=json.loads(self.paths[0].read_text())
        data['__md5__']='normalized'; self.paths[0].write_text(json.dumps(data))
        self.assertTrue(w.check(state)['ok']); self.assertTrue(w.restore(state,self.receipt)['ok'])

    def test_user_preference_drift_fails_before_disable(self):
        state=self.apply(); data=json.loads(self.paths[0].read_text())
        data['display-type']['value']='visual'; self.paths[0].write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError,'preferences changed'): w.restore(state,self.receipt)
        self.assertIn(w.UUID,self.values[w.KEY]); self.assertTrue(self.paths[0].exists())

    def test_moved_picker_fails_closed(self):
        state=self.apply(); self.values[w.KEY]=self.values[w.KEY].replace('panel2:center:0:'+w.UUID,'panel2:right:9:'+w.UUID)
        with self.assertRaisesRegex(RuntimeError,'moved'): w.restore(state,self.receipt)
        self.assertTrue(self.paths[0].exists())

    def test_instance_collisions_counter_regression_and_slot(self):
        self.paths[0].parent.mkdir(parents=True); self.paths[0].write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'collision'): w.plan()
        self.paths[0].unlink(); self.values['next-applet-id']='36'
        with self.assertRaisesRegex(RuntimeError,'fresh'): w.plan()
        self.values['next-applet-id']='50'
        self.values[w.KEY]=w.serialize_entries(self.initial+['panel2:center:0:other@fixture:42'])
        with self.assertRaisesRegex(RuntimeError,'occupied'): w.plan()

    def test_symlink_ancestor_rejected(self):
        destination=self.home/'elsewhere'; destination.mkdir()
        (self.home/'xdg').symlink_to(destination,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'Symlink'): w.plan()
        self.assertEqual(list(destination.iterdir()),[])

    def test_crash_after_config_link_before_created_flag_recovers(self):
        state=w.plan(); real_save=w.save
        def fail_after_link(path,value):
            if value.get('config_created'): raise RuntimeError('simulated crash')
            return real_save(path,value)
        with patch.object(w,'save',fail_after_link):
            with self.assertRaisesRegex(RuntimeError,'simulated crash'): w.commit(state,self.receipt)
        recovered=json.loads(self.receipt.read_text())
        self.assertNotIn('config_created',recovered)
        self.assertTrue(self.paths[0].exists())
        self.assertTrue(w.restore(recovered,self.receipt)['ok']); self.assertFalse(self.paths[0].exists())

    def test_failed_reservation_never_deletes_concurrent_identical_config(self):
        state=w.plan(); self.paths[0].parent.mkdir(parents=True)
        self.paths[0].write_text(json.dumps(state['config_data']))
        with self.assertRaisesRegex(RuntimeError,'collision'): w.commit(state,self.receipt)
        recovered=json.loads(self.receipt.read_text())
        with self.assertRaisesRegex(RuntimeError,'Unowned'): w.restore(recovered,self.receipt)
        self.assertTrue(self.paths[0].exists())

    def test_unload_failure_retains_file_and_recovers(self):
        state=self.apply()
        with patch.object(w,'wait_unloaded',side_effect=RuntimeError('unload timeout')):
            with self.assertRaisesRegex(RuntimeError,'unload timeout'): w.restore(state,self.receipt)
        self.assertTrue(self.paths[0].exists()); self.assertNotIn(w.UUID,self.values[w.KEY])
        self.assertTrue(w.restore(json.loads(self.receipt.read_text()),self.receipt)['ok'])

    def test_crash_after_enable_journal_recovers(self):
        state=self.apply(); recovered=json.loads(self.receipt.read_text())
        recovered['actions'][0]['applied']=False; recovered['status']='applying'; recovered['phase']='enable'
        self.assertTrue(w.restore(recovered,self.receipt)['ok'])

    def test_xdg_path_uses_glib_config_dir(self):
        patch.stopall()
        with patch.object(w.GLib,'get_user_config_dir',return_value=str(self.home/'alternate')):
            self.assertEqual(w.config_paths(50)[0],self.home/'alternate/cinnamon/spices'/w.UUID/'50.json')

if __name__=='__main__': unittest.main()
