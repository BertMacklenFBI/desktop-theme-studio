"""Enhancement orchestration fixtures: private files, no desktop or systemd calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import enhance as e


class EnhanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.path=self.root/'kept/desktop.json'; self.path.parent.mkdir()
        self.path.write_text('{"status": "kept", "preserved": "exact bytes"}\n')
        (self.path.parent/'applications.json').write_text('{"status":"applied"}')
        (self.path.parent/'menu.json').write_text('{"status":"applied"}')
        self.original=self.path.read_bytes()
        self.manifest={'version':1,'required':['applications.json','menu.json'],'started':['applications.json','menu.json']}
        (self.path.parent/'stages.json').write_text(json.dumps(self.manifest))
        self.scripts={n:self.root/(n+'.py') for n in e.STAGES}
        for p in self.scripts.values(): p.write_text('#fixture')
        self.calls=[]; self.checks=[]
        self.addCleanup(patch.stopall)
        patch.object(e,'MARKER',self.root/'marker.json').start()
        patch.object(e,'scripts',return_value=self.scripts).start()
        patch.object(e.theme,'check',self.check).start()
        patch.object(e.control,'validate_source',lambda:self.calls.append('source')).start()
        patch.object(e,'arm',lambda p,s:self.calls.append('arm')).start()
        patch.object(e,'stop',lambda s:self.calls.append('stop')).start()
        patch.object(e.theme,'call',self.call).start()

    def check(self,path):
        self.assertEqual(path.read_bytes(),self.original); self.checks.append(True)

    def call(self,script,command,*args,**kwargs):
        receipt=Path(args[args.index('--state')+1]); name=receipt.name
        self.calls.append((command,name))
        if command=='apply':
            self.assertIn(name,e.load(e.journal(self.path))['started'])
            self.assertIn(name,e.load(self.path.parent/'stages.json')['started'])
            receipt.write_text(json.dumps({'status':'applied','actions':[]}))
        elif command=='restore': receipt.write_text(json.dumps({'status':'restored','actions':[]}))
        return '{}'

    def test_apply_and_keep_only_new_stages_core_byteexact(self):
        result=e.apply(self.path); self.assertEqual(result['status'],'pending')
        self.assertEqual(self.calls[:2],['source','arm'])
        self.assertEqual([x for x in self.calls if isinstance(x,tuple) and x[0]=='apply'],
                         [('apply','workspace.json'),('apply','logo.json')])
        self.assertEqual(e.load(e.MARKER)['status'],'pending')
        self.assertEqual(e.keep(self.path)['status'],'kept'); self.assertEqual(self.calls[-1],'stop')
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_restore_reverse_only_new_stages_and_original_manifest(self):
        e.apply(self.path); self.calls=[]
        self.assertEqual(e.restore(self.path)['status'],'restored')
        self.assertEqual([x for x in self.calls if isinstance(x,tuple) and x[0]=='restore'],
                         [('restore','logo.json'),('restore','workspace.json')])
        self.assertEqual(e.load(self.path.parent/'stages.json'),self.manifest)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual((self.path.parent/'applications.json').read_text(),'{"status":"applied"}')

    def test_missing_attempted_receipt_retains_recovery_registration(self):
        def failing(script,command,*args,**kwargs):
            if command=='apply': raise RuntimeError('before receipt crash')
            return self.call(script,command,*args,**kwargs)
        with patch.object(e.theme,'call',failing):
            with self.assertRaisesRegex(RuntimeError,'before receipt'): e.apply(self.path)
        state=e.load(e.journal(self.path)); self.assertEqual(state['status'],'recovery-required')
        self.assertIn('workspace.json',e.load(self.path.parent/'stages.json')['started'])
        self.assertNotIn('stop',self.calls)

    def test_restore_conflict_retains_timer_and_manifest(self):
        e.apply(self.path)
        def failing(script,command,*args,**kwargs):
            if command=='restore': raise RuntimeError('conflict')
            return self.call(script,command,*args,**kwargs)
        with patch.object(e.theme,'call',failing):
            result=e.restore(self.path)
        self.assertEqual(result['status'],'recovery-required'); self.assertNotIn('stop',self.calls)
        self.assertIn('logo.json',e.load(self.path.parent/'stages.json')['required'])

    def test_expired_keep_restores_instead(self):
        e.apply(self.path); state=e.load(e.journal(self.path)); state['deadline']=0; e.write(self.path,state)
        with self.assertRaisesRegex(RuntimeError,'expired'): e.keep(self.path)
        self.assertEqual(e.load(e.journal(self.path))['status'],'restored')

    def test_automatic_callback_after_keep_ignores(self):
        e.apply(self.path); e.keep(self.path); count=len(self.calls)
        self.assertEqual(e.restore(self.path,automatic=True)['timer'],'ignored')
        self.assertEqual(self.calls[count:],['stop'])

    def test_existing_receipt_change_fails_closed(self):
        e.apply(self.path); self.path.write_text('{"status":"kept","external":true}')
        result=e.restore(self.path)
        self.assertEqual(result['status'],'recovery-required')
        self.assertNotIn('stop',self.calls)

    def test_existing_enhancement_rejected(self):
        e.apply(self.path)
        with self.assertRaisesRegex(RuntimeError,'journal already exists'): e.apply(self.path)

    def test_keep_reconciles_terminal_journal_marker_gap(self):
        e.apply(self.path); original_save=e.theme.save_json
        def fail_marker(path,state):
            if path==e.MARKER and state['status']=='kept': raise RuntimeError('marker interruption')
            return original_save(path,state)
        with patch.object(e.theme,'save_json',fail_marker):
            with self.assertRaisesRegex(RuntimeError,'marker interruption'): e.keep(self.path)
        self.assertEqual(e.load(e.journal(self.path))['status'],'kept')
        self.assertEqual(e.load(e.MARKER)['status'],'pending')
        self.assertNotIn('stop',self.calls)
        self.assertEqual(e.keep(self.path)['status'],'kept')
        self.assertEqual(e.load(e.MARKER)['status'],'kept')
        self.assertEqual(self.calls[-1],'stop')

    def test_timer_reconciles_restored_journal_marker_gap(self):
        e.apply(self.path); state=e.load(e.journal(self.path)); state['status']='restored'
        e.theme.save_json(e.journal(self.path),state)
        self.assertEqual(e.restore(self.path,automatic=True)['status'],'restored')
        self.assertEqual(e.load(e.MARKER)['status'],'restored')

    def test_timer_command_scopes_exact_receipt_and_retry(self):
        patch.stopall()
        with patch.object(e,'command') as command:
            e.arm(self.path,{'unit':'fixture-enhancement'})
        args=command.call_args.args[0]
        self.assertIn('--on-active=180s',args)
        self.assertIn('--property=Restart=on-failure',args)
        self.assertEqual(args[-4:],['restore','--receipt',str(self.path.resolve()),'--automatic'])

if __name__=='__main__': unittest.main()
