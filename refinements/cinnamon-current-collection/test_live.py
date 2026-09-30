"""Exercise actual collection rollback against temporary files, never the desktop."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('collection_live',Path(__file__).with_name('live.py'))
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)

class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        commands=patch.object(live.base,'run',return_value='')
        self.commands=commands.start();self.addCleanup(commands.stop)
        self.root=Path(self.temp.name);self.path=self.root/'receipt'/'trial'/'receipt.json';self.path.parent.mkdir(parents=True)
        self.source=self.root/'source';self.target=self.root/'target'
        self.source.mkdir();self.target.mkdir()
        (self.source/'style.css').write_text('new');(self.target/'style.css').write_text('old')
        self.config=self.root/'preferences.ini';self.config.write_text('[Appearance]\nColor=old\n[Behavior]\nKeep=personal\n')
        self.original=self.config.read_text()
        self.data={'id':'fixture','status':'applying','actions':[], 'protected_hashes':{},'required_assets':{},'panel_before':{},
                   'trees':[{'source':str(self.source),'target':str(self.target),'sha':live.base.tree(self.source),
                             'before_sha':live.base.tree(self.target)}]}
        live.base.tree_paths(self.path,self.data)
        live.ini_action(self.data,self.config,'Appearance','Color','new')

    def env(self):
        return patch.multiple(live,refresh=lambda d:None,validate=lambda d:None,journal=lambda p,d:live.E.journal(p,d))

    def apply(self):
        with patch.object(live.base,'journal',lambda p,d:live.E.journal(p,d)):
            live.base.install_trees(self.path,self.data)
        a=self.data['actions'][0];a['attempted']=True;live.E.write(a,a['after']);a['applied']=True

    def restore(self):
        with self.env(),patch.object(live.watcher,'pause',lambda p:None),patch.object(live.watcher,'resume',lambda p:None):
            live.restore(self.path,self.data)

    def test_complete_roundtrip_retains_unrelated_preferences(self):
        self.apply();self.restore()
        self.assertEqual(self.config.read_text(),self.original)
        self.assertEqual((self.target/'style.css').read_text(),'old')
        self.assertEqual(self.data['status'],'restored')

    def test_executable_widget_helpers_restore_content_and_mode(self):
        helper=self.root/'rpo-flow.py';helper.write_text('before\n');helper.chmod(0o640)
        action={'kind':'file','path':str(helper),
                'before':base64.b64encode(b'before\n').decode(),
                'after':base64.b64encode(b'after\n').decode(),
                'before_mode':0o640,'after_mode':0o755}
        live.E.write(action,action['after'])
        self.assertEqual(helper.read_text(),'after\n');self.assertEqual(helper.stat().st_mode & 0o777,0o755)
        live.E.write(action,action['before'])
        self.assertEqual(helper.read_text(),'before\n');self.assertEqual(helper.stat().st_mode & 0o777,0o640)

    def test_eww_managed_blocks_use_valid_comments_and_repair_legacy_markers(self):
        scss=self.root/'eww.scss';yuck=self.root/'eww.yuck'
        before,after=live.eww_managed_block(scss,'','Red Panda Overtime','@import "themes/red-panda-overtime";')
        self.assertEqual(before,'');self.assertIn('// BEGIN Red Panda Overtime',after)
        before,after=live.eww_managed_block(yuck,'','Red Panda Overtime','(include "red-panda-overtime.yuck")')
        self.assertEqual(before,'');self.assertIn(';; BEGIN Red Panda Overtime',after)
        legacy='# BEGIN Red Panda Overtime\nold\n# END Red Panda Overtime\n'
        before,after=live.eww_managed_block(yuck,legacy,'Red Panda Overtime','(include "red-panda-overtime.yuck")')
        self.assertIn('# BEGIN Red Panda Overtime',before)
        self.assertIn(';; BEGIN Red Panda Overtime',after)

    def test_extension_refresh_observes_restored_application_colors(self):
        self.apply();self.data['actions'][0]['stage']='applications'
        def observe(*args):
            self.assertIn('org.Cinnamon.ReloadTheme',args)
            self.assertEqual(self.config.read_text(),self.original)
            self.assertEqual((self.target/'style.css').read_text(),'old')
        self.commands.side_effect=observe
        self.restore();self.commands.assert_called_once()

    def test_failed_final_theme_refresh_keeps_recovery_required(self):
        self.apply();self.commands.side_effect=RuntimeError('theme reload failed')
        with self.assertRaisesRegex(RuntimeError,'theme reload failed'):self.restore()
        self.assertEqual(self.config.read_text(),self.original)
        self.assertEqual(self.data['status'],'recovery-required')

    def test_foreign_preference_edit_refuses_before_any_rollback(self):
        self.apply();self.config.write_text(self.config.read_text().replace('Color=new','Color=external'))
        with self.assertRaisesRegex(RuntimeError,'Restore conflict'):self.restore()
        self.assertEqual((self.target/'style.css').read_text(),'new')
        self.assertIn('Color=external',self.config.read_text())

    def test_interrupted_action_write_recovers(self):
        self.apply();self.data['actions'][0]['applied']=False
        self.restore();self.assertEqual(self.config.read_text(),self.original)

    def test_unrelated_preference_change_is_preserved(self):
        self.apply();self.config.write_text(self.config.read_text().replace('Keep=personal','Keep=updated'))
        self.restore();self.assertIn('Keep=updated',self.config.read_text())
        self.assertIn('Color=old',self.config.read_text())

    def test_restore_resumes_watcher_even_on_conflict(self):
        self.apply();(self.target/'style.css').write_text('external')
        with self.env(),patch.object(live.watcher,'pause'),patch.object(live.watcher,'resume') as resume:
            with self.assertRaisesRegex(RuntimeError,'conflict'):live.restore(self.path,self.data)
            resume.assert_called_once()

    def test_reopened_application_does_not_block_desktop_recovery(self):
        app=self.root/'app.ini';app.write_text('[Appearance]\nColor=before\n')
        live.ini_action(self.data,app,'Appearance','Color','after')
        a=self.data['actions'][-1];a.update(stage='applications',requires_closed='ptyxis',attempted=True,applied=True)
        live.E.write({k:v for k,v in a.items() if k!='requires_closed'},a['after'])
        self.apply()
        with patch.object(live.base.apps,'ensure_closed',side_effect=RuntimeError('ptyxis is running')):
            with self.assertRaisesRegex(RuntimeError,'Desktop restored'):self.restore()
        self.assertEqual((self.target/'style.css').read_text(),'old')
        self.assertEqual(self.config.read_text(),self.original)
        self.assertIn('Color=after',app.read_text())
        self.assertTrue(self.data['desktop_restored'])

    def test_application_conflict_does_not_block_desktop_recovery(self):
        self.apply();a=self.data['actions'][0];a['stage']='applications'
        self.config.write_text(self.config.read_text().replace('Color=new','Color=external'))
        with self.assertRaisesRegex(RuntimeError,'Desktop restored'):self.restore()
        self.assertEqual((self.target/'style.css').read_text(),'old')
        self.assertIn('Color=external',self.config.read_text())

    def test_keep_resume_failure_remains_pending(self):
        self.data.update(status='pending',unit='fixture')
        with self.env(),patch.object(live.watcher,'resume',side_effect=OSError('injected resume failure')):
            with self.assertRaises(OSError):live.keep(self.path,self.data)
        self.assertEqual(self.data['status'],'pending')

    def test_keep_timer_cleanup_failure_is_safe_after_watcher_resumes(self):
        self.data.update(status='pending',unit='fixture')
        with self.env(),patch.object(live.watcher,'resume') as resume,patch.object(live.base,'run',side_effect=OSError('timer cleanup')):
            live.keep(self.path,self.data)
            resume.assert_called_once()
        self.assertEqual(self.data['status'],'kept')
        self.assertIn('timer_cleanup_warning',self.data)

    def test_successful_desktop_restore_with_failed_watcher_is_not_complete(self):
        self.apply()
        with self.env(),patch.object(live.watcher,'pause'),patch.object(live.watcher,'resume',side_effect=OSError('watcher resume')):
            with self.assertRaisesRegex(OSError,'watcher resume'):live.restore(self.path,self.data)
        self.assertEqual((self.target/'style.css').read_text(),'old')
        self.assertEqual(self.data['status'],'recovery-required')
        self.assertEqual(json.loads(self.path.read_text())['status'],'recovery-required')

if __name__=='__main__':unittest.main()
