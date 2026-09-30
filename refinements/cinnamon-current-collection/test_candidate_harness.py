"""Pure authored-bound checks and owned-process cleanup failure boundaries."""
import copy
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parent
def load(name):
    spec=importlib.util.spec_from_file_location('candidate_test_'+name,HERE/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
layout=load('candidate_layout')
harness=load('candidate_isolate')


class NativeBounds(unittest.TestCase):
    def setUp(self):
        self.profile={'candidate_layout':{'kind':'two-panel','top_height':78,'bottom_height':76}}
        self.measured={'screen':[2880,1800],'panels':[
            {'id':1,'height':78,'mapped':True,'position':[12,0],'size':[2856,78]},
            {'id':2,'height':76,'mapped':True,'position':[0,1724],'size':[2880,76]}]}
    def test_authored_inset_is_preserved(self):
        layout.assert_native(self.measured,self.profile)
    def test_wrong_edge_and_uncontained_asymmetric_bounds_refused(self):
        for field,value in [('position',[12,100]),('position',[-12,0]),('size',[2880,78])]:
            with self.subTest(field=field,value=value):
                changed=copy.deepcopy(self.measured);changed['panels'][0][field]=value
                with self.assertRaises(RuntimeError):layout.assert_native(changed,self.profile)
    def test_nonfinite_and_fake_mapped_receipts_refused(self):
        for field,value in [('height',math.nan),('height',math.inf),('mapped','true'),('position',[math.nan,0])]:
            with self.subTest(field=field,value=value):
                changed=copy.deepcopy(self.measured);changed['panels'][0][field]=value
                with self.assertRaises(RuntimeError):layout.assert_native(changed,self.profile)
    def test_single_bottom_has_one_panel_and_declared_location(self):
        p={'candidate_layout':{'kind':'single-bottom','height':42}}
        native={'screen':[2880,1800],'panels':[{'id':1,'height':42,'mapped':True,'position':[0,1758],'size':[2880,42]}]}
        settings=layout.assert_native(native,p)
        self.assertEqual(settings['panels-enabled'],['1:0:bottom'])
        self.assertTrue(all(row.startswith('panel1:') for row in settings['enabled-applets']))


class Cleanup(unittest.TestCase):
    def test_failed_display_startup_never_reaps_host_and_terminates_server(self):
        with tempfile.TemporaryDirectory() as folder:
            server=Mock()
            with patch.object(harness,'reap_private_children') as reap,patch.object(harness.os,'close') as close:
                harness.cleanup_private_server(server,12,{'DISPLAY':':0','CURRENT_COLLECTION_HOST_DISPLAY':':0'},Path(folder))
            reap.assert_not_called();close.assert_called_once_with(12);server.terminate.assert_called_once();server.wait.assert_called_once()
    def test_reaper_failure_still_closes_and_terminates_owned_server(self):
        with tempfile.TemporaryDirectory() as folder:
            server=Mock()
            with (patch.object(harness,'reap_private_children',side_effect=OSError('injected reaper')),
                  patch.object(harness.os,'close') as close):
                with self.assertRaisesRegex(OSError,'injected reaper'):
                    harness.cleanup_private_server(server,12,{'DISPLAY':':99','CURRENT_COLLECTION_HOST_DISPLAY':':0'},Path(folder))
            close.assert_called_once_with(12);server.terminate.assert_called_once();server.wait.assert_called_once()
    def test_evidence_failure_still_terminates_owned_server(self):
        with tempfile.TemporaryDirectory() as folder:
            server=Mock();root=Path(folder)/'absent'
            with patch.object(harness,'reap_private_children',return_value={}),patch.object(harness.os,'close'):
                with self.assertRaises(FileNotFoundError):
                    harness.cleanup_private_server(server,12,{'DISPLAY':':99','CURRENT_COLLECTION_HOST_DISPLAY':':0'},root)
            server.terminate.assert_called_once();server.wait.assert_called_once()


if __name__=='__main__':unittest.main()
