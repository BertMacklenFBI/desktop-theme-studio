#!/usr/bin/python3
"""Direct backend/UI entrypoints refuse before writes or external actions."""
from pathlib import Path
import importlib.util,json,os,socket,subprocess,sys,tempfile
from unittest.mock import patch
sys.dont_write_bytecode=True

def imported(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def main():
    source=Path(__file__).resolve().parent.parent/'bundle-version2/private'
    sys.path.insert(0,str(source))
    backend=imported('direct_fixture_backend',source/'backend.py')
    with patch.dict(sys.modules,{'backend':backend}):ui=imported('direct_fixture_ui',source/'ui.py')
    checks=[]
    with tempfile.TemporaryDirectory(prefix='dts-direct-entry-test-') as temporary:
        root=Path(temporary);(root/'state.json').write_text('{"unrelated":"preserve"}')
        before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        def denied():raise RuntimeError('Private session attestation refused')
        def external(*args,**kwargs):raise AssertionError('External action attempted')
        with patch.dict(os.environ,{'DTS_PRIVATE_ROOT':str(root),'HOME':'/host','DISPLAY':':0.0'}),patch.object(backend,'owned_context',side_effect=denied),patch.object(socket,'socket',side_effect=external),patch.object(subprocess,'run',side_effect=external),patch.object(subprocess,'Popen',side_effect=external),patch.object(os,'kill',side_effect=external),patch.object(backend,'actual_media',side_effect=external),patch.object(backend,'actual_media_action',side_effect=external):
            for label,call in [('backend observation',backend.load),('backend action',lambda:backend.action(['media','pause']))]:
                try:call();raise AssertionError('Direct guard bypass accepted')
                except RuntimeError as error:assert str(error)=='Private session attestation refused'
                checks.append(label+' attests before reads or writes')
            for command in ['network','dashboard','edit-notes']:
                with patch.object(sys,'argv',['private/ui.py',command]):
                    try:ui.main();raise AssertionError('Direct UI guard bypass accepted')
                    except RuntimeError as error:assert str(error)=='Private session attestation refused'
                checks.append('direct UI '+command+' attests before receipts or external actions')
        after={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        assert after==before;checks.append('all unrelated scratch bytes preserved; no new receipts')
    print(json.dumps({'schema':1,'status':'passed','scope':'Imported direct-entry code with invalid-attestation and process/socket/signal tripwires; no real external actions','checks':checks},indent=2))

if __name__=='__main__':main()
