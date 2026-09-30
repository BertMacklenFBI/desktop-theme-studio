#!/usr/bin/python3
"""Pure version2 backend media-only route check; no actual process or bus."""
from pathlib import Path
import importlib.util,json,os,sys,tempfile
from unittest.mock import patch

def main():
    source=Path(__file__).resolve().parent.parent/'bundle-version2/private/backend.py'
    sys.path.insert(0,str(source.parent));spec=importlib.util.spec_from_file_location('version2_fixture_backend',source);backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
    with tempfile.TemporaryDirectory(prefix='dts-version2-backend-') as temporary:
        root=Path(temporary);state={'volume':25,'brightness':50,'mic':20,'muted':False,'media':{},'timer':{'minutes':25,'running':False,'text':'25:00'},'notes':'Keep this note','workspaces':[{'id':i,'active':i==0} for i in range(4)]};(root/'state.json').write_text(json.dumps(state));calls=[]
        with patch.dict(os.environ,{'DTS_PRIVATE_ROOT':str(root)}),patch.object(backend,'owned_context',return_value=(root,{})),patch.object(backend,'actual_media',return_value={'media_type':'actual','title':'Owned readback'}),patch.object(backend,'actual_media_action',side_effect=lambda *argv:calls.append(argv)):
            backend.action(['media','seek','35']);assert calls==[('seek','35')]
            backend.action(['volume','70']);backend.action(['timer','toggle']);backend.action(['workspace','2'])
            observed=json.loads((root/'state.json').read_text());assert observed['volume']==70 and observed['timer']['running'] and observed['workspaces'][2]['active'];assert observed['notes']=='Keep this note';assert calls==[('seek','35')]
    print(json.dumps({'schema':1,'scope':'Pure mocked actual-media function; no actual media acceptance','status':'passed','checks':['media action routes to exact adapter entrypoint','volume/timer/workspace retain mock state behavior','unrelated notes preserved']},indent=2))
if __name__=='__main__':main()
