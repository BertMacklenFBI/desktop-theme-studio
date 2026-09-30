#!/usr/bin/env python3
"""Fixture checks: no writes to live application paths or desktop settings."""
import importlib.util,json,tempfile,shutil,copy
from pathlib import Path
spec=importlib.util.spec_from_file_location('adapter',Path(__file__).with_name('adapter.py')); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def planning_run(args):
    if args[0]=='fc-match':return args[-1]
    if args[:2]==['gsettings','get']:return "'00000000-0000-0000-0000-000000000000'"
    if args[:2]==['gsettings','user-value'] or args[:2]==['dconf','read']:return ''
    raise AssertionError('Unexpected planning command '+str(args))
m.run=planning_run
plan=m.plan()
assert all(not ('gtk-3.0' in p or 'gtk-4.0' in p or p.endswith('.gtkrc-2.0')) for p in plan['touched_paths'])
assert not any(a.get('key')=='visible-name' for a in plan['actions'])
assert all('bin/cava-panel.sh' not in p for p in plan['touched_paths'])
with tempfile.TemporaryDirectory(prefix='moonstone-app-fixture-') as temp:
    root=Path(temp);state=copy.deepcopy(plan);original={};settings={};fakehome=root/'home';fakehome.mkdir()
    for a in state['actions']:
        if 'path' in a:
            source=Path(a['path']);target=root/str(source).lstrip('/');target.parent.mkdir(parents=True,exist_ok=True)
            if source.exists() and str(source) not in original:
                shutil.copyfile(source,target);original[str(source)]=source.read_bytes()
            a['path']=str(target)
        elif a['kind']=='gsetting':settings[(a['schema'],a['key'])]=a['before']
        else:settings[('dconf',a['key'])]=a['before']
    def fake_run(args):
        if args[0]=='gsettings':
            key=(args[2],args[3])
            if args[1] in ('get','user-value'):return '' if settings[key]==m.MISSING else settings[key]
            settings[key]=m.MISSING if args[1]=='reset' else args[4];return ''
        if args[0]=='dconf':
            key=('dconf',args[2])
            if args[1]=='read':return '' if settings[key]==m.MISSING else settings[key]
            settings[key]=m.MISSING if args[1]=='reset' else args[3];return ''
        raise AssertionError(args)
    m.run=fake_run;state['protected_hashes']={};journal=root/'journal.json'
    for a in state['actions']:
        assert m.current(a)==a['before'],a
        m.write(a,a['after']);a['applied']=True
        assert m.current(a)==a['after'],a
    # JSON key-level restore preserves an unrelated preference added after apply.
    ff=root/'home/bertmacklen/.config/fastfetch/config.jsonc';obj=m.load(ff);obj['fixture-unrelated']='keep';ff.write_text(json.dumps(obj))
    # INI rollback also leaves unrelated values alone.
    kde=root/'home/bertmacklen/.config/kdeglobals';kde.write_text(kde.read_text()+'\n[Fixture]\nUnrelated=keep\n')
    m.restore(state,journal)
    assert m.load(ff)['fixture-unrelated']=='keep'
    assert m.ini_get(kde.read_text(),'Fixture','Unrelated')=='keep'
    for a in state['actions']:assert m.current(a)==a['before'],a
    # Restore refuses an explicit edited appearance value before any mutation.
    a=next(x for x in state['actions'] if x['kind']=='json');m.write(a,a['after']);a['applied']=True;m.write(a,'fixture-conflict')
    try:m.restore(state,journal)
    except RuntimeError as exc:assert 'Restore conflict' in str(exc)
    else:raise AssertionError('Expected conflict')
print(json.dumps({'actions':len(plan['actions']),'touched_paths':len(plan['touched_paths']),'gsettings':len(plan['gsettings']),'dconf':len(plan['dconf']),'result':'fixture apply/restore and unrelated preference preservation pass; conflict refusal pass'}))
# Inherited value restoration, attempted-write recovery, and automatic rollback.
with tempfile.TemporaryDirectory(prefix='moonstone-recovery-fixture-') as temp:
    root=Path(temp);target=root/'appearance.json';target.write_text('{"color":"old","unrelated":42}')
    action={'kind':'json','path':str(target),'keys':['color'],'before':'old','after':'new'}
    state={'actions':[action],'protected_hashes':{},'skipped':[]}
    real_plan=m.plan;real_write=m.write;real_argv=m.sys.argv
    m.plan=lambda:copy.deepcopy(state)
    calls=[0]
    def fail_after_write(a,value):
        real_write(a,value);calls[0]+=1
        if calls[0]==1:raise OSError('simulated failure immediately after atomic write')
    m.write=fail_after_write;journal=root/'state.json';m.sys.argv=['adapter.py','apply','--state',str(journal),'--commit']
    try:m.main()
    except OSError:pass
    else:raise AssertionError('Expected simulated failure')
    recovered=m.load(journal)
    assert recovered['status']=='rolled-back',recovered
    assert m.load(target)=={'color':'old','unrelated':42}
    assert journal.stat().st_mode & 0o777 == 0o600
    m.write=real_write;m.plan=real_plan;m.sys.argv=real_argv
    inherited={'kind':'gsetting','schema':'fixture','key':'inherited','before':m.MISSING,'after':"'new'"}
    settings[('fixture','inherited')]=m.MISSING
    assert m.current(inherited)==m.MISSING
    m.write(inherited,inherited['after']);assert m.current(inherited)==inherited['after']
    m.write(inherited,inherited['before']);assert m.current(inherited)==m.MISSING
print('Inherited reset, attempted-write recovery, automatic rollback and private journal checks pass.')
