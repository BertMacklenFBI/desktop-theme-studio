#!/usr/bin/python3
"""Only temporary filesystem copies; never starts Eww/media or a bus."""
from pathlib import Path
from unittest.mock import patch
import importlib.util,json,sys,tempfile

def refuses(call):
    try:call();raise AssertionError('Unknown private config accepted')
    except RuntimeError:return

def main():
    source=Path(__file__).resolve().parent.parent/'bundle-version2/private/launch.py';sys.path.insert(0,str(source.parent))
    spec=importlib.util.spec_from_file_location('receiver_reuse_fixture',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix='dts-receiver-reuse-') as temporary:
        root=Path(temporary)
        with patch.object(module,'attest_private_session'),patch.object(module,'root',return_value=root):
            config=module.prepare();assert module.prepare()==config
            script=config/'scripts/backend.py';original=script.read_bytes();script.write_bytes(original+b'\n# unknown edit\n');refuses(module.prepare);script.write_bytes(original)
            saved=root/'saved-config';config.rename(saved);foreign=root/'foreign';foreign.mkdir();config.symlink_to(foreign,target_is_directory=True);refuses(module.prepare);config.unlink();saved.rename(config)
            state=root/'state.json';state.unlink();outside=root/'unrelated.txt';outside.write_text('retained');state.symlink_to(outside);refuses(module.prepare);assert outside.read_text()=='retained'
    print(json.dumps({'schema':1,'scope':'Owned temp filesystem copies with environment guard mocked valid; no native acceptance','status':'passed','checks':['exact config inventory reuse passes','modified backend inventory refused','symlinked config refused','symlinked state refused without target write']},indent=2))
if __name__=='__main__':main()
