#!/usr/bin/env python3
"""Scoped application appearance transaction. Import and plan are read-only."""
import argparse, base64, hashlib, json, os, re, subprocess, sys, tempfile
from gi.repository import Gio, GLib
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
_compat_spec=importlib.util.spec_from_file_location('candidate_appearance_compat',Path(__file__).with_name('appearance_compat.py'))
compat=importlib.util.module_from_spec(_compat_spec);_compat_spec.loader.exec_module(compat)
HERE=Path(__file__).resolve().parent
HOME=Path.home()
MISSING={'__plum_afterglow_missing__':True}

def run(args):
    if args[:2]==['gsettings','user-value']:
        schema=args[2]
        settings=Gio.Settings.new_with_path(*schema.split(':',1)) if ':' in schema else Gio.Settings.new(schema)
        value=settings.get_user_value(args[3])
        return value.print_(True) if value is not None else ''
    return subprocess.run(args,capture_output=True,text=True,check=True,timeout=10).stdout.strip()
def digest(data): return hashlib.sha256(data).hexdigest()
def rgb(c): return [int(c[i:i+2],16) for i in (1,3,5)]
def ansi(c): return '38;2;'+';'.join(map(str,rgb(c)))
def load(p): return json.loads(Path(p).read_text())
def atomic(path,data,private=False):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    mode=0o600 if private else (p.stat().st_mode & 0o777 if p.exists() else 0o644)
    fd,tmpname=tempfile.mkstemp(prefix='.'+p.name+'.plum-afterglow-',dir=p.parent)
    tmp=Path(tmpname)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        tmp.chmod(mode);tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)
def journal(path,state):atomic(path,(json.dumps(state,indent=2)+'\n').encode(),private=True)
def lookup(obj,keys):
    try:
        for k in keys: obj=obj[k]
        return obj
    except (KeyError,IndexError): return MISSING

def put(obj,keys,value):
    for k in keys[:-1]:
        if isinstance(obj,dict): obj=obj.setdefault(k,{})
        else: obj=obj[k]
    if value==MISSING:
        if isinstance(obj,dict): obj.pop(keys[-1],None)
        else: raise RuntimeError('Cannot remove an array element')
    else: obj[keys[-1]]=value

def ini_get(text,section,key):
    head=re.search(r'^\['+re.escape(section)+r'\]\s*$',text,re.M)
    if not head:return MISSING
    tail=re.search(r'^\[',text[head.end():],re.M); end=head.end()+tail.start() if tail else len(text)
    match=re.search(r'^'+re.escape(key)+r'\s*=(.*)$',text[head.end():end],re.M)
    return match.group(1).strip() if match else MISSING

def ini_set(text,section,key,value):
    head=re.search(r'^\['+re.escape(section)+r'\]\s*$',text,re.M)
    if not head:
        if value==MISSING:return text
        return text.rstrip()+'\n\n['+section+']\n'+key+'='+value+'\n'
    tail=re.search(r'^\[',text[head.end():],re.M); end=head.end()+tail.start() if tail else len(text)
    middle=text[head.end():end]; pattern=r'^'+re.escape(key)+r'\s*=.*(?:\n|$)'
    if re.search(pattern,middle,re.M):middle=re.sub(pattern,'' if value==MISSING else key+'='+value+'\n',middle,count=1,flags=re.M)
    elif value!=MISSING:middle=middle.rstrip()+'\n'+key+'='+value+'\n\n'
    return text[:head.end()]+middle+text[end:]

def current(action):
    kind=action['kind']; p=Path(action['path']) if 'path' in action else None
    if kind=='json':return lookup(load(p),action['keys'])
    if kind=='ini':return ini_get(p.read_text() if p.exists() else '',action['section'],action['key'])
    if kind=='file':return base64.b64encode(p.read_bytes()).decode() if p.exists() else MISSING
    if kind=='gsetting':return run(['gsettings','user-value',action['schema'],action['key']]) or MISSING
    if kind=='dconf':return run(['dconf','read',action['key']]) or MISSING
    if kind=='suffix':
        raw=p.read_bytes();suffix=action['after'].encode()
        if raw.endswith(suffix):return action['after']
        if suffix not in raw:return ''
        raise RuntimeError('Prompt suffix was moved; refusing to rewrite shell configuration')
    if kind=='text':
        text=p.read_text(); before=action['before']; after=action['after']
        if before and text.count(before)==action['count']:return before
        if after and text.count(after)==action['count']:return after
        if not before and after not in text:return ''
        raise RuntimeError('Text patch conflict: '+str(p))
    raise ValueError(kind)

def write(action,value):
    k=action['kind']; p=Path(action['path']) if 'path' in action else None
    if k=='json':
        obj=load(p);put(obj,action['keys'],value);atomic(p,(json.dumps(obj,indent=2)+'\n').encode())
    elif k=='ini':atomic(p,ini_set(p.read_text() if p.exists() else '',action['section'],action['key'],value).encode())
    elif k=='file':
        if value==MISSING:p.unlink(missing_ok=True)
        else:atomic(p,base64.b64decode(value))
    elif k=='gsetting':
        run(['gsettings','reset',action['schema'],action['key']]) if value==MISSING else run(['gsettings','set',action['schema'],action['key'],value])
    elif k=='dconf':run(['dconf','reset',action['key']]) if value==MISSING else run(['dconf','write',action['key'],value])
    elif k=='suffix':
        raw=p.read_bytes();suffix=action['after'].encode()
        if value=='':
            if not raw.endswith(suffix):raise RuntimeError('Prompt suffix restore conflict')
            raw=raw[:-len(suffix)]
        else:
            if suffix in raw:raise RuntimeError('Prompt suffix already present')
            raw+=suffix
        atomic(p,raw)
    elif k=='text':
        text=p.read_text();old=action['after'] if value==action['before'] else action['before']
        if old:text=text.replace(old,value)
        else:text+=value
        atomic(p,text.encode())

def plan(home=HOME,settings=True):
    HOME=Path(home)
    d=load(HERE.parent/'design.json'); t=d['tokens']; colors=d['terminal_ansi']; name=d['name']; actions=[]; skipped=[]
    def add(action,after):
        if not settings and action['kind'] in ('gsetting','dconf'):return
        if action['kind']=='gsetting':after=GLib.Variant.parse(None,after,None,None).print_(True)
        action['after']=after
        if action['kind']!='text':action['before']=current(action)
        if action['before']!=after:actions.append(action)
    def file(path,data):add(dict(kind='file',path=str(path)),base64.b64encode(data.encode() if isinstance(data,str) else data).decode())
    def jp(path,keys,value):
        if Path(path).exists():add(dict(kind='json',path=str(path),keys=keys),value)
        else:skipped.append(str(path)+' absent')
    def ini(path,section,key,value):add(dict(kind='ini',path=str(path),section=section,key=key),value)
    def patch(path,before,after):
        p=Path(path)
        if not p.exists():skipped.append(str(p)+' absent');return
        n=p.read_text().count(before) if before else 1
        if before and not n:raise RuntimeError('Expected patch not found '+str(p))
        if not before and after in p.read_text():return
        add(dict(kind='text',path=str(p),before=before,count=n),after)
    font,font_size=d['typography']['monospace'].rsplit(' ',1)
    if font.casefold() not in run(['fc-match','-f','%{family}',font]).casefold():font='DejaVu Sans Mono';skipped.append('Requested monospace unavailable; using installed DejaVu Sans Mono '+font_size+'.')
    kitty='\n'.join([f'# {name}',f'font_family {font}',f'font_size {float(font_size):.1f}','window_padding_width 12','background_opacity 1.0',f'background {t["background"]}',f'foreground {t["foreground"]}',f'cursor {t["accent"]}',f'cursor_text_color {t["background"]}',f'selection_background {t["selection"]}',f'selection_foreground {t["foreground"]}',f'active_tab_background {t["accent"]}',f'active_tab_foreground {t["background"]}',f'inactive_tab_background {t["surface"]}',f'inactive_tab_foreground {t["muted"]}',f'url_color {colors[4]}']+[f'color{i} {c}' for i,c in enumerate(colors)])+'\n'
    file(HOME/'.config/kitty/plum-afterglow.conf',kitty)
    kp=HOME/'.config/kitty/kitty.conf'
    if kp.exists():
        before=kp.read_text();after=compat.kitty_include(before,'plum-afterglow.conf',name)
        if before!=after:patch(kp,before,after)
    else:file(kp,'include plum-afterglow.conf\n')
    scheme='[General]\nDescription=Plum Afterglow\nOpacity=1\n'
    for section,col in [('Background',t['background']),('Foreground',t['foreground'])]+[(f'Color{i%8}'+('Intense' if i>=8 else ''),c) for i,c in enumerate(colors)]:scheme+=f'\n[{section}]\nColor='+','.join(map(str,rgb(col)))+'\n'
    file(HOME/'.local/share/konsole/plum-afterglow.colorscheme',scheme)
    profile=run(['gsettings','get','org.gnome.Terminal.ProfilesList','default']).strip("'") if settings else '00000000-0000-0000-0000-000000000000'
    schema=f'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{profile}/'
    for key,value in {'background-color':repr(t['background']),'foreground-color':repr(t['foreground']),'palette':repr(colors),'use-theme-colors':'false','use-theme-transparency':'false','use-transparent-background':'false','background-transparency-percent':'0','use-system-font':'false','font':repr(font+' '+font_size),'cursor-colors-set':'true','cursor-background-color':repr(t['accent']),'cursor-foreground-color':repr(t['background']),'bold-color-same-as-fg':'true','highlight-colors-set':'true','highlight-background-color':repr(t['selection']),'highlight-foreground-color':repr(t['foreground'])}.items():add(dict(kind='gsetting',schema=schema,key=key),value)
    # Retain GNOME profile UUID and visible-name; do not recolor historical profiles.
    konsole=HOME/'.config/konsolerc'; active=ini_get(konsole.read_text(),'Desktop Entry','DefaultProfile')
    if active!=MISSING and (HOME/'.local/share/konsole'/active).exists():ini(HOME/'.local/share/konsole'/active,'Appearance','ColorScheme','plum-afterglow')
    else:skipped.append('Konsole active profile absent; color scheme built only.')
    palette='[Palette]\nName=Plum Afterglow\n'
    for section in ['Light','Dark']:palette+='\n['+section+']\nBackground='+t['background']+'\nForeground='+t['foreground']+'\nCursor='+t['accent']+'\n'+'\n'.join(f'Color{i}={c}' for i,c in enumerate(colors))+'\n'
    for dr in [HOME/'.local/share/org.gnome.Ptyxis/palettes',HOME/'.var/app/app.devsuite.Ptyxis/data/org.gnome.Ptyxis/palettes']:file(dr/'plum-afterglow.palette',palette)
    flatpak=HOME/'.var/app/app.devsuite.Ptyxis'
    keyfile=flatpak/'config/glib-2.0/settings/keyfile'
    if keyfile.is_file():
        if keyfile.is_symlink():raise RuntimeError('Ptyxis settings keyfile symlink requires review')
        active=ini_get(keyfile.read_text(),'org/gnome/Ptyxis','default-profile-uuid')
        if not isinstance(active,str) or not re.fullmatch(r"'[a-f0-9]{32}'",active):raise RuntimeError('Unexpected installed Ptyxis profile UUID')
        file(flatpak/'data/app.devsuite.Ptyxis/palettes/plum-afterglow.palette',palette)
        ini(keyfile,'org/gnome/Ptyxis/Profiles/'+active.strip("'"),'palette',repr('plum-afterglow'))
    else:skipped.append('Installed Flatpak Ptyxis keyfile absent; app-ID selection skipped.')
    pu=run(['dconf','read','/org/gnome/Ptyxis/default-profile-uuid']).strip("'") if settings else ''
    if pu:add(dict(kind='dconf',key=f'/org/gnome/Ptyxis/Profiles/{pu}/palette'),repr('plum-afterglow'))
    else:skipped.append('Ptyxis default profile not accessible.')
    # Install independent source-view syntax theme used by Xed.
    xml=f'''<?xml version="1.0" encoding="UTF-8"?>
<style-scheme id="plum-afterglow" name="Plum Afterglow" version="1.0"><author>Desktop Theme Studio</author>
<style name="text" foreground="{t['foreground']}" background="{t['background']}"/>
<style name="selection" foreground="{t['foreground']}" background="{t['selection']}"/>
<style name="current-line" background="{t['surface']}"/><style name="line-numbers" foreground="{t['muted']}" background="{t['surface']}"/>
<style name="def:comment" foreground="{t['muted']}" italic="true"/><style name="def:keyword" foreground="{t['accent']}" bold="true"/>
<style name="def:string" foreground="{t['sage']}"/><style name="def:number" foreground="{colors[4]}"/><style name="def:type" foreground="{colors[5]}"/><style name="def:error" foreground="{t['error']}"/>
</style-scheme>\n'''
    for version in ['3.0','4','5']:file(HOME/f'.local/share/gtksourceview-{version}/styles/plum-afterglow.xml',xml)
    add(dict(kind='gsetting',schema='org.x.editor.preferences.editor',key='scheme'),repr('plum-afterglow'))
    nanorc=HOME/'.nanorc'
    nano={k:v for k,v in [('titlecolor',f"bold,{t['background']},{t['accent']}"),('statuscolor',f"{t['foreground']},{t['surface']}"),('errorcolor',f"{t['background']},{t['error']}"),('promptcolor',f"{t['foreground']},{t['surface']}"),('selectedcolor',f"{t['foreground']},{t['selection']}"),('stripecolor',','+t['surface']),('numbercolor',t['muted']),('keycolor',t['accent']),('functioncolor',t['foreground']),('scrollercolor',t['border']),('spotlightcolor',f"{t['background']},{t['sage']}"),('minicolor',t['muted'])]}
    if nanorc.exists():
        for key,value in nano.items():
            match=re.search(r'^set\s+'+key+r'\s+.*$',nanorc.read_text(),re.M)
            if match:patch(nanorc,match.group(),f'set {key} {value}')
        for line in nanorc.read_text().splitlines():
            match=re.match(r'^(extendsyntax\s+(\S+)\s+color\s+)#[0-9a-fA-F]{6}',line)
            if match and match.group(2) in ['c','python','sh','json','nanorc']:
                col=colors[4] if match.group(2)=='json' else colors[5] if match.group(2)=='nanorc' else t['accent']
                new=match.group(1)+col+line[match.end():]
                if new!=line:patch(nanorc,line,new)
    # Pin a registry entry, preserving every pixel and original logo dimensions.
    ff=HOME/'.config/fastfetch/config.jsonc'; cfg=load(ff); registry=HOME/'.config/fastfetch/logos/hues/palettes.json'
    bands=[rgb(t['foreground']),rgb(t['accent']),rgb(t['muted']),rgb(t['sage']),rgb(colors[4]),rgb(t['foreground'])]
    jp(registry,['plum-afterglow'],bands)
    logo=HERE.parent/'artwork/menu-logo.png'; dest=registry.parent/'macklenmobile-logo-plum-afterglow.png'
    file(dest,logo.read_bytes());file(HOME/'.config/fastfetch/.theme','plum-afterglow\n');jp(ff,['logo','source'],str(dest))
    for key,col in [('title',t['foreground']),('keys',t['muted'])]:jp(ff,['display','color',key],ansi(col))
    groups={'os':1,'host':1,'kernel':1,'uptime':1,'packages':1,'shell':1,'de':2,'wm':2,'theme':2,'icons':2,'terminal':2,'terminalfont':2,'cpu':3,'gpu':3,'display':4,'memory':5,'swap':5,'disk':5,'localip':1}
    for i,item in enumerate(cfg['modules']):
        if isinstance(item,dict) and item.get('type') in groups:jp(ff,['modules',i,'keyColor'],'38;2;'+';'.join(map(str,bands[groups[item['type']]])))
    mpv=HOME/'.config/mpv/mpv.conf'
    if mpv.exists():
        for line in dict.fromkeys(mpv.read_text().splitlines()):
            new=line
            if line.startswith('osd-color='):new='osd-color="'+t['foreground']+'"'
            elif line.startswith('osd-border-color='):new='osd-border-color="'+t['background']+'"'
            elif line.startswith('lavfi-complex=') and 'showfreqs=' in line:new=re.sub(r'colors=0x[0-9a-fA-F]{6}','colors=0x'+t['accent'][1:],line)
            if new!=line:patch(mpv,line,new)
    # Actual autostart dashboard merges this appearance overlay last.
    dash=HOME/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget'
    theme={'accent':t['accent'],'warning':t['warning'],'critical':t['error'],'card_background':t['surface'],'border':t['border'],'surface':t['elevated'],'surface_soft':t['background'],'hover':t['selection'],'text':t['foreground'],'muted':t['muted'],'palette':[t['accent'],t['sage'],colors[4],colors[5]],'font':'Ubuntu, sans-serif'}
    for key,value in theme.items():jp(dash/'appearance.json',['theme',key],value)
    file(dash/'ui/fastfetch-logo.png',logo.read_bytes())
    jp(dash/'appearance.json',['theme','logo'],str(dash/'ui/fastfetch-logo.png'))
    css='\n/* Plum Afterglow appearance override */\n'+(HERE/'library.css').read_text()
    patch(dash/'ui/graphite-brass.css','',css)
    eww=HOME/'Documents/eww-graphite-brass/config'
    ep={'label':'Plum Afterglow','short':'PA','base':t['background'],'surface0':t['surface'],'surface1':t['elevated'],'border':t['border'],'text':t['foreground'],'muted':t['muted'],'accent':t['accent'],'secondary':t['sage'],'battery':t['sage'],'tile':t['selection'],'accent-hover':t['focus'],'indeterminate':t['muted']}
    jp(eww/'themes/palettes.json',[name],ep)
    # The existing watcher writes the variables after Cinnamon is selected; no process restart here.
    old=re.search(r'// THEME_VARIABLES_START.*?// THEME_VARIABLES_END',(eww/'eww.scss').read_text(),re.S)
    if old:
        new=compat.variables(ep)
        if old.group()!=new:patch(eww/'eww.scss',old.group(),new)
    else:raise RuntimeError('Unknown Eww variable block; refusing stylesheet rewrite')
    ini(HOME/'.local/share/flatpak/overrides/global','Environment','GTK_THEME',name)
    # KDE colors affect supported KDE applications while preserving all existing unrelated keys.
    for section,bg in [('View',t['elevated']),('Window',t['background']),('Button',t['surface']),('Tooltip',t['elevated']),('Selection',t['selection'])]:
        for key,col in [('BackgroundNormal',bg),('ForegroundNormal',t['foreground']),('ForegroundInactive',t['muted']),('DecorationFocus',t['focus']),('DecorationHover',t['accent'])]:ini(HOME/'.config/kdeglobals','Colors:'+section,key,','.join(map(str,rgb(col))))
    skipped+=['Cava raw output has no color keys; dashboard bars are themed; protected cava-panel.sh remains byte-identical.','Browser/Electron content themes, Blender UI and LibreOffice canvas colors are not covered; scoped Tilda/Ptyxis and GIMP/KDE adapters are separate.','Qt palette honors KDE-aware applications only; general Qt platform/plugin selection remains unchanged.']
    file(HOME/'.local/share/applications/plum-afterglow-library.desktop', '[Desktop Entry]\nType=Application\nName=Music Library\nComment=Open your music library on demand\nExec=/usr/bin/python3 \"'+str(HERE.parent/'native_visibility.py')+'\" show\nIcon=multimedia-player\nTerminal=false\nCategories=AudioVideo;Audio;\n')
    prompt_path=HOME/'.config/plum-afterglow/prompt.bash'
    raw_prompt=prompt_path.read_text() if prompt_path.is_file() else (HERE/'prompt.bash').read_text()
    file(prompt_path,compat.prompt_colors(raw_prompt,ansi(t['muted']),ansi(t['accent'])))
    prompt_suffix='\n# Plum Afterglow prompt (appearance only)\n[ -r "$HOME/.config/plum-afterglow/prompt.bash" ] && . "$HOME/.config/plum-afterglow/prompt.bash"\n'
    bashrc=HOME/'.bashrc'
    original_bash=bashrc.read_bytes()
    if not compat.prompt_hook_presence(original_bash,prompt_suffix.encode()):
        add(dict(kind='suffix',path=str(bashrc)),prompt_suffix)
    original_bash=original_bash.removesuffix(prompt_suffix.encode())
    protected=[str(HOME/Path(p).relative_to(Path.home())) if Path(p).is_relative_to(Path.home()) else p for p in load(HERE.parents[2]/'audits/applications.json')['protected_files']]
    protected=[p for p in protected if p not in (str(mpv),str(bashrc))]
    return {'touched_paths':sorted({a['path'] for a in actions if 'path' in a}), 'gsettings':[{'schema':a['schema'],'key':a['key'],'before':a['before'],'after':a['after']} for a in actions if a['kind']=='gsetting'], 'dconf':[{'key':a['key'],'before':a['before'],'after':a['after']} for a in actions if a['kind']=='dconf'], 'indirect_watcher_paths':[str(eww/'assets/music.svg'),str(eww.parent/'state/theme.json')], 'theme':name,'created_at':datetime.now(timezone.utc).isoformat(),'actions':actions,'skipped':skipped,'protected_hashes':{p:digest(Path(p).read_bytes()) for p in protected if Path(p).is_file()},'protected_prompt':{'path':str(bashrc),'suffix':prompt_suffix,'sha256':digest(original_bash)},'intentional_protected_appearance_edits':[str(mpv),str(bashrc)],'logo_source':str(logo),'logo_sha256':digest(logo.read_bytes())}

def validate_protected(state):
    if state.get('protected_prompt'):
        r=state['protected_prompt'];body=Path(r['path']).read_bytes().removesuffix(r['suffix'].encode())
        if digest(body)!=r['sha256']:raise RuntimeError('Non-appearance shell configuration changed')
    bad=[p for p,h in state['protected_hashes'].items() if not Path(p).is_file() or digest(Path(p).read_bytes())!=h]
    if bad:raise RuntimeError('Protected file changed: '+', '.join(bad))

def restore(state,state_path):
    validate_protected(state)
    pending=[a for a in state['actions'] if a.get('applied') or a.get('attempted')]
    # A write may have completed before its journal success flag: compare both states.
    for a in reversed(pending):
        if current(a) not in (a['before'],a['after']):raise RuntimeError('Restore conflict: '+str(a.get('path',a.get('key'))))
    for a in reversed(pending):
        if current(a)==a['after']:write(a,a['before'])
        a['applied']=False;a['attempted']=False;journal(state_path,state)
    state['status']='restored';journal(state_path,state);validate_protected(state)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['plan','apply','restore','check']);parser.add_argument('--state',type=Path);parser.add_argument('--commit',action='store_true');args=parser.parse_args()
    if args.command=='plan':
        p=plan();print(json.dumps(p,indent=2));return
    if not args.state:parser.error('--state is required')
    if args.command=='apply':
        if args.state.exists():raise RuntimeError('State already exists; choose a fresh path')
        state=plan()
        if not args.commit:print(json.dumps({'preview':True,'actions':len(state['actions']),'skipped':state['skipped']},indent=2));return
        validate_protected(state)
        # Detect stale values before the first write.
        for a in state['actions']:
            if current(a)!=a['before']:raise RuntimeError('Apply conflict '+str(a))
        state['status']='applying';journal(args.state,state)
        try:
            for a in state['actions']:
                if current(a)!=a['before']:raise RuntimeError('Apply conflict '+str(a.get('path',a.get('key'))))
                a['attempted']=True;journal(args.state,state)
                write(a,a['after']);a['applied']=True;journal(args.state,state)
            validate_protected(state);state['status']='applied';journal(args.state,state)
        except Exception as exc:
            state['error']=str(exc);journal(args.state,state)
            try:
                restore(state,args.state);state['status']='rolled-back'
            except Exception as recovery:
                state['status']='recovery-required';state['recovery_error']=str(recovery)
            journal(args.state,state);raise
    elif args.command=='restore':
        state=load(args.state)
        if not args.commit:print('Preview: restore '+str(sum(bool(a.get('applied')) for a in state['actions']))+' appearance actions');return
        restore(state,args.state)
    else:
        state=load(args.state);validate_protected(state)
        conflicts=[str(a.get('path',a.get('key'))) for a in state['actions'] if a.get('applied') and current(a)!=a['after']]
        print(json.dumps({'status':state['status'],'conflicts':conflicts,'protected':'unchanged'},indent=2))
        if conflicts:sys.exit(1)
if __name__=='__main__':main()
