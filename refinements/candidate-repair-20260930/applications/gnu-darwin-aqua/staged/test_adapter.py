#!/usr/bin/env python3
"""Fixture checks: no writes to live application paths or desktop settings.

The real plan is computed read-only against this home, then every action is
replayed inside a temporary copy with gsettings/dconf replaced by a dictionary.
"""
import importlib.util,json,tempfile,shutil,copy,os,subprocess,sys,xml.dom.minidom
from pathlib import Path
spec=importlib.util.spec_from_file_location('adapter',Path(__file__).with_name('adapter.py')); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
HOME=str(Path.home())
plan=m.plan()
results=[]
protected_before={p:m.digest(Path(p).read_bytes()) for p in plan['protected_hashes']}

# Scope invariants: controller-owned GTK files and protected music/visualizer files are never touched.
assert all(not ('gtk-3.0' in p or 'gtk-4.0' in p or p.endswith('.gtkrc-2.0')) for p in plan['touched_paths'])
assert all('bin/cava-panel.sh' not in p and '/mpv/' not in p and '/cava/' not in p and 'eww-graphite-brass' not in p and 'music-widget' not in p for p in plan['touched_paths'])
assert not any(p.endswith('long.jsonc') or p.endswith('rotate-logo.sh') or p.endswith('apply-hue-colors.py') or p.endswith('modes.py') for p in plan['touched_paths'])
assert str(Path(HOME)/'.config/mpv/mpv.conf') in plan['protected_hashes'] or not (Path(HOME)/'.config/mpv/mpv.conf').is_file()
assert any(s.endswith(m.PROTECTED_MIXED) for s in plan['skipped'])
# GNOME Terminal: only the new UUID gains keys; every existing profile keeps its keys and visible name.
new=plan['gnome_terminal_profile']
profile_keys=[a for a in plan['actions'] if a['kind']=='gsetting' and a['schema'].startswith('org.gnome.Terminal.Legacy.Profile:')]
assert profile_keys and all(new in a['schema'] for a in profile_keys),'profile keys must target only the new UUID'
assert all(new in a['schema'] for a in plan['actions'] if a.get('key')=='visible-name')
listing=next(a for a in plan['actions'] if a['kind']=='gsetting' and a['schema']=='org.gnome.Terminal.ProfilesList' and a['key']=='list')
before_list=m.GLib.Variant.parse(None,listing['before'],None,None).unpack() if listing['before']!=m.MISSING else []
after_list=m.GLib.Variant.parse(None,listing['after'],None,None).unpack()
assert after_list[:-1]==before_list and after_list[-1]==new,'new profile must be appended, existing order intact'
assert 'dd8eea03-4e10-4a4a-aed4-cd641c0e1cc5' in after_list or 'dd8eea03-4e10-4a4a-aed4-cd641c0e1cc5' not in before_list
assert all(a['before']==m.MISSING for a in profile_keys),'new UUID must not already carry user values'
# Kitty include appended after existing includes; Konsole gets a new profile rather than an edited one.
kitty=[a for a in plan['actions'] if a.get('path')==HOME+'/.config/kitty/kitty.conf']
assert all(a['kind']=='text' and a['before']=='' for a in kitty)
assert not any(a.get('path','').endswith('Nocturne.profile') for a in plan['actions'])
# Bash and all startup files are untouched.
assert not any(a.get('path','').endswith('/.bashrc') for a in plan['actions'])
assert plan['shell_startup_files_untouched'] and plan['fastfetch_logo_source_untouched']
# Generated fragments must be byte-identical to what the plan writes.
fragments=m.render()
for name,rel in [('kitty/gnu-darwin-aqua.conf','.config/kitty/gnu-darwin-aqua.conf'),('gtksourceview/gnu-darwin-aqua.xml','.local/share/gtksourceview-5/styles/gnu-darwin-aqua.xml'),('konsole/gnu-darwin-aqua.colorscheme','.local/share/konsole/gnu-darwin-aqua.colorscheme')]:
    action=next(a for a in plan['actions'] if a.get('path')==str(Path(HOME)/rel))
    assert m.base64.b64decode(action['after']).decode()==fragments[name],name
    generated=m.GENERATED/name
    assert generated.is_file() and generated.read_text()==fragments[name],'run adapter.py generate first: '+name
xml.dom.minidom.parseString(fragments['gtksourceview/gnu-darwin-aqua.xml'])
assert 'font_family Liberation Mono' in fragments['kitty/gnu-darwin-aqua.conf'] and 'font_size 11.0' in fragments['kitty/gnu-darwin-aqua.conf'] and 'window_padding_width 12' in fragments['kitty/gnu-darwin-aqua.conf']
results.append({'actions':len(plan['actions']),'touched_paths':len(plan['touched_paths']),'gsettings':len(plan['gsettings']),'dconf':len(plan['dconf']),'skipped':len(plan['skipped']),'gnome_terminal_profile':new,'fastfetch_bands_source':plan['fastfetch_bands_source'],'result':'scope, new-profile, kitty include, shell preservation and fragment identity invariants pass'})

with tempfile.TemporaryDirectory(prefix='gnu-darwin-aqua-app-fixture-') as temp:
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
    # Kitty keeps every pre-existing include line and gains ours at the end.
    fixture_kitty=root/(HOME.lstrip('/'))/'.config/kitty/kitty.conf'
    if HOME+'/.config/kitty/kitty.conf' in original:
        old_includes=[l for l in original[HOME+'/.config/kitty/kitty.conf'].decode().splitlines() if l.startswith('include ')]
        new_includes=[l for l in fixture_kitty.read_text().splitlines() if l.startswith('include ')]
        assert new_includes[:len(old_includes)]==old_includes and new_includes[-1]=='include gnu-darwin-aqua.conf',new_includes
    # Fastfetch: the custom Local IP line lives in long.jsonc, which is untouched; config.jsonc keeps its structure.
    ff=root/(HOME.lstrip('/'))/'.config/fastfetch/config.jsonc';cfg=m.load(ff)
    assert cfg['logo']['source']==m.load(Path(HOME)/'.config/fastfetch/config.jsonc')['logo']['source'] and cfg['logo']['type']=='chafa'
    assert (root/(HOME.lstrip('/'))/'.config/fastfetch/.theme').read_text()=='gnu-darwin-aqua\n'
    assert m.load(root/(HOME.lstrip('/'))/'.config/fastfetch/logos/hues/palettes.json')['gnu-darwin-aqua']==m.fastfetch_bands(m.design())[0]
    # GNOME Terminal fixture list holds every original UUID plus the new one.
    assert m.GLib.Variant.parse(None,settings[('org.gnome.Terminal.ProfilesList','list')],None,None).unpack()==after_list
    # JSON key-level restore preserves an unrelated preference added after apply.
    obj=m.load(ff);obj['fixture-unrelated']='keep';ff.write_text(json.dumps(obj))
    # INI rollback also leaves unrelated values alone.
    kde=root/(HOME.lstrip('/'))/'.config/kdeglobals';kde.write_text(kde.read_text()+'\n[Fixture]\nUnrelated=keep\n')
    m.restore(state,journal)
    assert m.load(ff)['fixture-unrelated']=='keep'
    assert m.ini_get(kde.read_text(),'Fixture','Unrelated')=='keep'
    for a in state['actions']:assert m.current(a)==a['before'],a
    # Restore refuses an explicit edited appearance value before any mutation.
    a=next(x for x in state['actions'] if x['kind']=='json');m.write(a,a['after']);a['applied']=True;m.write(a,'fixture-conflict')
    try:m.restore(state,journal)
    except RuntimeError as exc:assert 'Restore conflict' in str(exc)
    else:raise AssertionError('Expected conflict')
results.append({'result':'fixture apply/restore, shell startup untouched, kitty include order, Fastfetch original logo preserved, unrelated preference preservation and conflict refusal pass'})

# Inherited value restoration, attempted-write recovery, and automatic rollback.
with tempfile.TemporaryDirectory(prefix='gnu-darwin-aqua-recovery-fixture-') as temp:
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
results.append({'result':'inherited reset, attempted-write recovery, automatic rollback and private journal checks pass'})

# The fake settings dictionary never reached the real bus: prove the live default profile is unchanged.
live_default=subprocess.run(['gsettings','get','org.gnome.Terminal.ProfilesList','default'],capture_output=True,text=True,check=True).stdout.strip()
default_action=next(a for a in plan['actions'] if a['kind']=='gsetting' and a['schema']=='org.gnome.Terminal.ProfilesList' and a['key']=='default')
assert live_default==default_action['before'],'live GNOME Terminal default changed during fixtures'
results.append({'result':'live GNOME Terminal default unchanged: '+live_default})
m.validate_protected(plan)
assert protected_before=={p:m.digest(Path(p).read_bytes()) for p in protected_before}
d=m.design()
kitty=fragments['kitty/'+m.SLUG+'.conf']
for i,col in enumerate(d['terminal_ansi']): assert f'color{i} {col}\n' in kitty
assert 'audible-bell' not in m.terminal_keys(d,'Liberation Mono','11')
assert 'background '+d['palette']['terminal'] in kitty
selection=m.kde_sections(d)['Colors:Selection']
for key,value in selection.items():
    if key.startswith('Foreground'): assert value==','.join(map(str,m.rgb(d['palette']['selection_foreground'])))
results.append({'result':'protected hashes unchanged; 16 exact ANSI slots, light terminal, KDE selected text and bell preference preservation pass','protected_files':len(protected_before)})
for row in results:print(json.dumps(row))
