#!/usr/bin/python3
"""Disposable fixture tests; never execute live widget/backend commands."""
from pathlib import Path
import importlib.util,json,tempfile,base64,re,sys
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('wa',HERE/'widget_adapter.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
# Real command handlers retain exact audio/music actions.
for name in ['saimoom.yuck','carbon.yuck']:
    before=(m.LIVE/name).read_text();after=(HERE/'widgets'/name).read_text()
    get=lambda x: sorted(re.findall(r'"(scripts/backend\.py action [^"]+)"',x))
    assert get(before)==get(after),(name,'changed backend actions')
assert (m.LIVE/'scripts/backend.py').is_file()
yuck=(HERE/'widgets/eww.yuck').read_text()
assert yuck.count('(deflisten')==1 and '(defpoll' not in yuck
assert yuck.count(':anchor "bottom right"')==6 and yuck.count(':y "-70px"')==6
assert yuck.count(':anchor "top right"')==6 and yuck.count(':y "56px"')==6
assert m.E.MISSING=={'__macintosh_soft_missing__':True}
assert ':image-width 184 :image-height 184' in (HERE/'widgets/saimoom.yuck').read_text()
# Watcher output exactly equals the staged variables: repeated palette writes are idempotent.
s=importlib.util.spec_from_file_location('watcher',m.LIVE/'scripts/theme-sync.py');watcher=importlib.util.module_from_spec(s);s.loader.exec_module(watcher)
palette=json.loads((HERE/'widgets/palette.json').read_text());scss=(HERE/'widgets/eww.scss').read_text()
assert watcher.render_variables(palette) in scss
assert watcher.resolve('Macintosh Soft',{'Macintosh Soft':palette})=='Macintosh Soft'
with tempfile.TemporaryDirectory(prefix='macintosh-widget-test-') as temp:
    live=Path(temp)/'config';live.mkdir();(live/'themes').mkdir();(live.parent/'bin').mkdir()
    (live/'scripts').mkdir()
    for name in ['backend.py','ui','theme-sync.py','theme-watch.py']:(live/'scripts'/name).write_bytes((m.LIVE/'scripts'/name).read_bytes())
    original={}
    for name in ['eww.yuck','saimoom.yuck','carbon.yuck','eww.scss']:
        data=(m.LIVE/name).read_bytes();(live/name).write_bytes(data);original[str(live/name)]=data
    launcher=live.parent/'bin/eww-widgets';launcher.write_bytes((m.LIVE.parent/'bin/eww-widgets').read_bytes());original[str(launcher)]=launcher.read_bytes()
    registry=live/'themes/palettes.json';registry.write_text('{"Other theme":{"preserve":true}}\n')
    state=m.plan(live);assert len(state['actions'])==5
    assert not any(a['path']==str(launcher) for a in state['actions']),'Current launcher/help and handler bytes must be retained'
    receipt=Path(temp)/'receipt.json'
    for a in state['actions']:
        assert m.E.current(a)==a['before'];a['attempted']=True;m.E.journal(receipt,state);m.E.write(a,a['after']);a['applied']=True;m.E.journal(receipt,state)
    assert not m.plan(live)['actions']
    assert json.loads(registry.read_text())['Other theme']=={'preserve':True}
    target=live/'eww.yuck';after=target.read_bytes();target.write_text('unexpected user edit')
    try:m.E.restore(state,receipt)
    except RuntimeError as e:assert 'Restore conflict' in str(e)
    else:raise AssertionError('Restore accepted unknown edit')
    target.write_bytes(after);m.E.restore(state,receipt)
    assert all(Path(p).read_bytes()==data for p,data in original.items())
    assert json.loads(registry.read_text())=={'Other theme':{'preserve':True}}
    target.write_text('new source after staging')
    try:m.plan(live)
    except RuntimeError as e:assert 'baseline drift' in str(e)
    else:raise AssertionError('Plan accepted drift')
    target.write_bytes(original[str(target)])
    # A normal watcher palette update can change only its variable block.
    scss=live/'eww.scss';raw=scss.read_text()
    scss.write_text(re.sub(r'// THEME_VARIABLES_START.*?// THEME_VARIABLES_END',watcher.render_variables({**palette,'accent':'#356262'}),raw,flags=re.S))
    assert m.plan(live)['actions'],'Recognized current watcher variables must remain selectable'
    # Unrelated style body changes remain an unknown source revision.
    scss.write_text(scss.read_text()+'\n.unreviewed { padding:99px; }\n')
    try:m.plan(live)
    except RuntimeError as e:assert 'baseline drift' in str(e)
    else:raise AssertionError('Plan accepted unrelated stylesheet changes')
    dependency=live/'scripts/theme-sync.py';dependency.write_text(dependency.read_text()+'\n# unknown renderer revision\n')
    try:m.plan(live)
    except RuntimeError as e:assert 'dependency revision drift' in str(e)
    else:raise AssertionError('Plan accepted unknown renderer revision')
print(json.dumps({'fixture_roundtrip':'passed','unknown_edit_restore':'refused','stale_baseline':'refused','backend_action_handlers':'unchanged','watcher_palette':'idempotent exact variables','new_runtime_commands_executed':False},indent=2))
