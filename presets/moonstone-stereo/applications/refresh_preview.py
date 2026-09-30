#!/usr/bin/env python3
"""Refresh staged appearance assets/metadata after the canonical logo changes; no live writes."""
import base64,importlib.util,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('moonstone_adapter',HERE/'adapter.py');adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
def main():
    plan=adapter.plan();(HERE/'generated').mkdir(exist_ok=True)
    for action in plan['actions']:
        if action['kind']=='file' and action['path'].endswith(('.conf','.colorscheme','.palette','.xml')):
            (HERE/'generated'/Path(action['path']).name).write_bytes(base64.b64decode(action['after']))
    preview={k:v for k,v in plan.items() if k not in ['actions','protected_hashes']}
    preview['actions']=[{key:(f'<{len(base64.b64decode(value))} bytes>' if key in ('before','after') and action['kind']=='file' and value!=adapter.MISSING else value) for key,value in action.items()} for action in plan['actions']]
    preview['protected_file_count']=len(plan['protected_hashes'])
    (HERE/'plan-preview.json').write_text(json.dumps(preview,indent=2)+'\n')
    print(json.dumps({'logo_source':plan['logo_source'],'logo_sha256':plan['logo_sha256'],'actions':len(plan['actions']),'live_changes':False},indent=2))
if __name__=='__main__':main()
