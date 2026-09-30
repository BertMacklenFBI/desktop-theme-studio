"""Registered enhanced plan: read-only projection, no duplicate allocation or subprocess."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import theme
import desktop_control as control


class EnhancedPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.directory=self.root/'state/kept'; self.directory.mkdir(parents=True)
        self.path=self.directory/'desktop.json'
        self.base="['panel2:left:0:menu@cinnamon.org:17']"
        self.added="['panel2:left:0:menu@cinnamon.org:17', 'panel2:center:0:workspace-switcher@cinnamon.org:50']"
        (self.root/'state/latest.json').write_text(json.dumps({'id':'kept'}))
        self.path.write_text(json.dumps({'status':'kept','settings':[],
            'panel':{'settings':[{'schema':'org.cinnamon','key':'enabled-applets','before':'original',
                                 'user':'original','after':self.base}]}}))
        self.workspace={'status':'applied','actions':[{'kind':'gsetting','schema':'org.cinnamon',
            'key':'enabled-applets','before':self.base,'after':self.added,'applied':True}]}
        (self.directory/'workspace.json').write_text(json.dumps(self.workspace))
        (self.directory/'stages.json').write_text(json.dumps({'required':['workspace.json'],'started':['workspace.json']}))
        self.addCleanup(patch.stopall)
        patch.object(theme,'ROOT',self.root).start()
        patch.object(control,'live_value',return_value=self.added).start()
        patch.object(control,'validate_source',return_value=None).start()
        settings=Mock(); settings.get_int.return_value=51
        patch.object(control.Gio.Settings,'new',return_value=settings).start()

    def hashes(self): return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}

    def test_current_layout_readonly_no_planner_or_instance_allocation(self):
        before=self.hashes()
        with patch.object(theme.subprocess,'run',side_effect=AssertionError('no subprocess plan expected')):
            result=theme.plan()
        self.assertEqual(result['writes'],0); self.assertEqual(result['prospective_action'],'none')
        self.assertEqual(result['core']['source_validation'],'ready')
        self.assertEqual(result['core']['settings_drift'],[])
        self.assertEqual(result['core']['panel_layout']['taskbar'][-1],'panel2:center:0:workspace-switcher@cinnamon.org:50')
        self.assertTrue(result['full_reapply_requires_restore'])
        self.assertEqual(before,self.hashes())

    def test_reports_live_drift_without_claiming_already_applied(self):
        with patch.object(control,'live_value',return_value=self.base): result=theme.plan()
        self.assertEqual(result['core']['settings_drift'],['org.cinnamon/enabled-applets'])
        self.assertFalse(result['core']['panel_layout']['already_applied'])

    def test_discontinuous_receipt_refuses_projection(self):
        self.workspace['actions'][0]['before']="['drifted']"
        (self.directory/'workspace.json').write_text(json.dumps(self.workspace))
        with self.assertRaisesRegex(RuntimeError,'discontinuous'): theme.plan()

    def test_without_applied_workspace_uses_existing_fresh_plan_path(self):
        (self.directory/'workspace.json').unlink()
        with patch.object(theme.subprocess,'run',side_effect=RuntimeError('original core plan path')):
            with self.assertRaisesRegex(RuntimeError,'original core plan path'): theme.plan()

if __name__=='__main__': unittest.main()
