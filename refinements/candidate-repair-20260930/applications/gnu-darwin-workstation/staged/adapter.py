#!/usr/bin/env python3
"""GNU-Darwin Workstation scoped application appearance transaction.

Import, `plan` and `generate` are read-only against the live home. `apply` and
`restore` need --state with a fresh private receipt and --commit to write; the
coordinator runs them, never this worker. Colors come only from ../design.json
(`palette` + `terminal_ansi`).
"""
import argparse, base64, hashlib, json, os, re, subprocess, sys, tempfile, uuid, stat
from gi.repository import Gio, GLib
from pathlib import Path
from datetime import datetime, timezone
import appearance_compat as compat
HERE=Path(__file__).resolve().parent
ROOT=Path('/home/bertmacklen/Documents/desktop-theme-studio/presets/gnu-darwin-workstation')
HOME=Path.home()
SLUG='gnu-darwin-workstation'
MISSING={'__gnu_darwin_workstation_missing__':True}
GENERATED=HERE/'generated'
# Deterministic so a re-plan never invents a second profile; not an existing UUID.
TERMINAL_UUID=str(uuid.uuid5(uuid.NAMESPACE_URL,'desktop-theme-studio/'+SLUG+'/gnome-terminal-profile'))
PROTECTED_MIXED='skipped: protected/mixed file'

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
def preserved_text(path):
    # Path.read_text() applies universal newline translation. GIMP mixed files
    # must preserve every unrelated byte, including CRLF and standalone CR.
    with Path(path).open('r',encoding='utf-8',newline='') as stream:return stream.read()

def guard_gimp_path(path, allow_missing_ancestors=False):
    """Fail closed on redirected/mixed profile paths, before reads and every write.

    Do not resolve(): that would discard evidence of a symlink ancestor.
    Missing leaf is allowed for creating a new appearance file.
    """
    p=Path(path)
    if not p.is_absolute() or '..' in p.parts:raise RuntimeError('Unsafe GIMP preference path')
    for entry in list(reversed(p.parents))+[p]:
        try:mode=entry.lstat().st_mode
        except FileNotFoundError:
            if entry==p or allow_missing_ancestors:return
            raise RuntimeError('Missing GIMP preference ancestor: '+str(entry))
        if stat.S_ISLNK(mode):raise RuntimeError('GIMP preference symlink requires review: '+str(entry))
        if entry==p:
            if not stat.S_ISREG(mode):raise RuntimeError('GIMP preference is not a regular file: '+str(entry))
        elif not stat.S_ISDIR(mode):raise RuntimeError('GIMP preference ancestor is not a directory: '+str(entry))

def atomic(path,data,private=False):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    mode=0o600 if private else (p.stat().st_mode & 0o777 if p.exists() else 0o644)
    fd,tmpname=tempfile.mkstemp(prefix='.'+p.name+'.'+SLUG+'-',dir=p.parent)
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
    if action.get('gimp_path_guard'):guard_gimp_path(p)
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
        text=preserved_text(p) if action.get('gimp_path_guard') else p.read_text(); before=action['before']; after=action['after']
        if before and text.count(before)==action['count']:return before
        if after and text.count(after)==action['count']:return after
        if not before and after not in text:return ''
        raise RuntimeError('Text patch conflict: '+str(p))
    raise ValueError(kind)

def write(action,value):
    k=action['kind']; p=Path(action['path']) if 'path' in action else None
    if action.get('gimp_path_guard'):guard_gimp_path(p)
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
        text=preserved_text(p) if action.get('gimp_path_guard') else p.read_text();old=action['after'] if value==action['before'] else action['before']
        if old:text=text.replace(old,value)
        else:text+=value
        atomic(p,text.encode())

def design():
    d=load(ROOT/'design.json')
    if d['id']!=SLUG or d['name']!='GNU-Darwin Workstation':raise RuntimeError('design.json identity does not match this adapter')
    if len(d['terminal_ansi'])!=16:raise RuntimeError('terminal_ansi must have sixteen colors')
    return d

def monospace(d):
    family=d['typography']['monospace_family']; size=str(d['typography']['monospace_size_pt'])
    note=None
    try:
        if family.casefold() not in run(['fc-match','-f','%{family}',family]).casefold():
            note='Requested monospace '+family+' unavailable; using installed DejaVu Sans Mono '+size+'.'; family='DejaVu Sans Mono'
    except Exception:
        note='fc-match unavailable; assuming '+family+' is installed.'
    return family,size,note

def fastfetch_bands(d):
    """Six [r,g,b] bands, light to dark: artwork's registry entry when present, else derived from the palette."""
    t=d['palette']
    return [rgb(c) for c in [t['terminal_foreground'],d['terminal_ansi'][7],d['terminal_ansi'][8],d['rainbow'][0],d['rainbow'][1],d['rainbow'][2]]],'design.json neutral terminal-safe palette roles'

def render(d=None):
    """Every generated fragment as {name: text}; identical bytes feed plan() and generated/."""
    d=d or design(); t=d['palette']; colors=d['terminal_ansi']; name=d['name']
    font,size,_=monospace(d)
    bg,fg=t['terminal'],t['terminal_foreground']
    out={}
    out['kitty/'+SLUG+'.conf']='\n'.join([f'# {name} — generated from design.json; selected by an include line in kitty.conf',f'font_family {font}',f'font_size {float(size):.1f}','window_padding_width 12','background_opacity 1.0',f'background {bg}',f'foreground {fg}',f'cursor {fg}',f'cursor_text_color {bg}',f'selection_background {t["selection"]}',f'selection_foreground {t["selection_foreground"]}',f'active_tab_background {t["selection"]}',f'active_tab_foreground {t["selection_foreground"]}',f'inactive_tab_background {t["surface"]}',f'inactive_tab_foreground {t["muted"]}',f'active_border_color {t["focus"]}',f'inactive_border_color {t["border"]}',f'url_color {colors[4]}']+[f'color{i} {c}' for i,c in enumerate(colors)])+'\n'
    out['kitty/include-line.conf']='\n# '+name+' appearance override\ninclude '+SLUG+'.conf\n'
    scheme='[General]\nDescription='+name+'\nOpacity=1\n'
    for section,col in [('Background',bg),('Foreground',fg),('BackgroundIntense',bg),('ForegroundIntense',fg)]+[(f'Color{i%8}'+('Intense' if i>=8 else ''),c) for i,c in enumerate(colors)]:scheme+=f'\n[{section}]\nColor='+','.join(map(str,rgb(col)))+'\n'
    out['konsole/'+SLUG+'.colorscheme']=scheme
    out['konsole/'+SLUG+'.profile']='[Appearance]\nColorScheme='+SLUG+'\nFont='+font+','+size+',-1,5,50,0,0,0,0,0\n\n[General]\nName='+name+'\nParent=FALLBACK/\n'
    palette='[Palette]\nName='+name+'\n'
    for section in ['Light','Dark']:palette+='\n['+section+']\nBackground='+bg+'\nForeground='+fg+'\nCursor='+fg+'\n'+'\n'.join(f'Color{i}={c}' for i,c in enumerate(colors))+'\n'
    out['ptyxis/'+SLUG+'.palette']=palette
    out['gtksourceview/'+SLUG+'.xml']=f'''<?xml version="1.0" encoding="UTF-8"?>
<style-scheme id="{SLUG}" name="{name}" version="1.0"><author>Desktop Theme Studio</author><description>Compact gray workstation editor with slate selection.</description>
<style name="text" foreground="{t['foreground']}" background="{t['selection_foreground']}"/>
<style name="selection" foreground="{t['selection_foreground']}" background="{t['selection']}"/>
<style name="cursor" foreground="{t['focus']}"/>
<style name="current-line" background="{t['surface']}"/><style name="line-numbers" foreground="{t['muted']}" background="{t['surface']}"/>
<style name="bracket-match" foreground="{t['selection_foreground']}" background="{t['secondary']}" bold="true"/>
<style name="search-match" foreground="{t['selection_foreground']}" background="{t['warning']}"/>
<style name="def:comment" foreground="{t['muted']}" italic="true"/><style name="def:keyword" foreground="{t['focus']}" bold="true"/>
<style name="def:string" foreground="{t['success']}"/><style name="def:number" foreground="{t['secondary']}"/><style name="def:type" foreground="{t['secondary']}"/>
<style name="def:function" foreground="{t['focus']}"/><style name="def:preprocessor" foreground="{t['warning']}"/><style name="def:error" foreground="{t['error']}"/>
</style-scheme>
'''
    out['nano/'+SLUG+'.nanorc']='## '+name+' interface colors for nano 7.x (merged key by key into ~/.nanorc)\n'+''.join(f'set {k} {v}\n' for k,v in nano_keys(d).items())
    out['gnome-terminal/profile.json']=json.dumps({'uuid':TERMINAL_UUID,'schema':'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:'+TERMINAL_UUID+'/','keys':terminal_keys(d,font,size)},indent=2)+'\n'
    out['kde/kdeglobals-colors.ini']='\n'.join('['+section+']\n'+'\n'.join(k+'='+v for k,v in rows.items()) for section,rows in kde_sections(d).items())+'\n'
    out['flatpak/overrides-global.ini']='[Environment]\nGTK_THEME='+name+'\n'
    bands,source=fastfetch_bands(d)
    out['fastfetch/palettes-entry.json']=json.dumps({SLUG:bands,'_source':source},indent=2)+'\n'
    out['gimp/gimprc-appearance.conf']='(theme "System")\n(icon-theme "Legacy")\n'
    out['gimp/gtkrc-include.conf']='\n# '+name+' appearance, loaded after GIMP System defaults\ninclude '+json.dumps(str(HOME/'.themes'/name/'gtk-2.0/gtkrc'))+'\n'
    return out

def terminal_keys(d,font,size):
    t=d['palette']; colors=d['terminal_ansi']
    return {'visible-name':repr(d['name']),'background-color':repr(t['terminal']),'foreground-color':repr(t['terminal_foreground']),'palette':repr(colors),'use-theme-colors':'false','use-theme-transparency':'false','use-transparent-background':'false','background-transparency-percent':'0','use-system-font':'false','font':repr(font+' '+size),'cursor-colors-set':'true','cursor-background-color':repr(t['terminal_foreground']),'cursor-foreground-color':repr(t['terminal']),'bold-color-same-as-fg':'true','bold-is-bright':'false','highlight-colors-set':'true','highlight-background-color':repr(t['selection']),'highlight-foreground-color':repr(t['selection_foreground'])}

def nano_keys(d):
    t=d['palette']
    return {k:v for k,v in [('titlecolor',f"bold,{t['selection_foreground']},{t['selection']}"),('statuscolor',f"{t['foreground']},{t['surface']}"),('errorcolor',f"{t['selection_foreground']},{t['error']}"),('promptcolor',f"{t['foreground']},{t['surface']}"),('selectedcolor',f"{t['selection_foreground']},{t['selection']}"),('stripecolor',','+t['surface']),('numbercolor',d['terminal_ansi'][8]),('keycolor',t['terminal_foreground']),('functioncolor',d['terminal_ansi'][7]),('scrollercolor',d['terminal_ansi'][8]),('spotlightcolor',f"{t['selection_foreground']},{t['secondary']}"),('minicolor',d['terminal_ansi'][7])]}

def kde_sections(d):
    t=d['palette']; out={}
    for section,bg in [('View',t['elevated']),('Window',t['background']),('Button',t['surface']),('Tooltip',t['elevated']),('Selection',t['selection'])]:
        fgn=t['selection_foreground'] if section=='Selection' else t['foreground']
        out['Colors:'+section]={k:','.join(map(str,rgb(c))) for k,c in [('BackgroundNormal',bg),('BackgroundAlternate',t['surface'] if section!='Selection' else t['focus']),('ForegroundNormal',fgn),('ForegroundInactive',fgn if section=='Selection' else t['muted']),('ForegroundLink',fgn if section=='Selection' else t['focus']),('ForegroundNegative',fgn if section=='Selection' else t['error']),('ForegroundPositive',fgn if section=='Selection' else t['success']),('ForegroundNeutral',fgn if section=='Selection' else t['warning']),('DecorationFocus',t['focus']),('DecorationHover',t['focus'])]}
    return out

def gimp_appearance_patches(text):
    """Select only top-level theme/icon-theme expressions; retain comments and nested prefs.

    Ambiguous or malformed forms fail closed. Returned literals feed the existing
    conflict-checked text action, so receipt recovery needs no new action kind.
    """
    forms=[]; depth=0; start=None; quoted=False; escaped=False; comment=False
    for i,c in enumerate(text):
        if comment:
            if c=='\n':comment=False
            continue
        if quoted:
            if escaped:escaped=False
            elif c=='\\':escaped=True
            elif c=='"':quoted=False
            continue
        if c=='#':comment=True;continue
        if c=='"':quoted=True;continue
        if c=='(':
            if depth==0:start=i
            depth+=1
        elif c==')':
            depth-=1
            if depth<0:raise RuntimeError('Malformed GIMP preferences')
            if depth==0:forms.append(text[start:i+1])
    if depth or quoted:raise RuntimeError('Malformed GIMP preferences')
    result=[]
    for key,value in [('theme','System'),('icon-theme','Legacy')]:
        matches=[f for f in forms if re.match(r'\(\s*'+key+r'(?:\s|\))',f)]
        if len(matches)>1:raise RuntimeError('Duplicate GIMP appearance key: '+key)
        after='('+key+' '+json.dumps(value)+')'
        if matches:
            before=matches[0]
            if not re.fullmatch(r'\(\s*'+key+r'\s+"(?:[^"\\]|\\.)*"\s*\)',before):raise RuntimeError('Unsupported GIMP appearance value: '+key)
            if text.count(before)!=1:raise RuntimeError('Ambiguous GIMP literal: '+key)
            if before!=after:result.append((before,after))
        else:result.append(('', '\n'+after+'\n'))
    return result

def plan(home=HOME,settings=True):
    HOME=Path(home)
    d=design(); t=d['palette']; colors=d['terminal_ansi']; name=d['name']; actions=[]; skipped=[]
    fragments=render(d)
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
    def patch(path,before,after,preserve_newlines=False):
        p=Path(path)
        if not p.exists():skipped.append(str(p)+' absent');return
        raw=preserved_text(p) if preserve_newlines else p.read_text()
        n=raw.count(before) if before else 1
        if before and not n:raise RuntimeError('Expected patch not found '+str(p))
        if not before and after in raw:return
        action=dict(kind='text',path=str(p),before=before,count=n)
        if preserve_newlines:action['gimp_path_guard']=True
        add(action,after)
    font,size,note=monospace(d)
    if note:skipped.append(note)
    # Kitty: generated palette file, selected by one appended include; existing includes stay.
    file(HOME/'.config/kitty'/(SLUG+'.conf'),fragments['kitty/'+SLUG+'.conf'])
    kp=HOME/'.config/kitty/kitty.conf'
    if kp.exists():
        before=kp.read_text();patch(kp,before,compat.kitty_include(before,SLUG+'.conf',name))
    else:file(kp,'include '+SLUG+'.conf\n')
    # Konsole: new color scheme and a NEW profile; existing profiles are never edited.
    file(HOME/'.local/share/konsole'/(SLUG+'.colorscheme'),fragments['konsole/'+SLUG+'.colorscheme'])
    file(HOME/'.local/share/konsole'/(SLUG+'.profile'),fragments['konsole/'+SLUG+'.profile'])
    konsolerc=HOME/'.config/konsolerc'
    if konsolerc.exists():ini(konsolerc,'Desktop Entry','DefaultProfile',SLUG+'.profile')
    else:skipped.append('konsolerc absent; Konsole profile built but not selected.')
    # GNOME Terminal: a NEW profile UUID with its own keys, appended to the list and made default.
    # The existing profiles (including dd8eea03… Nocturne) keep every key and their visible names.
    schema='org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:'+TERMINAL_UUID+'/'
    for key,value in terminal_keys(d,font,size).items():add(dict(kind='gsetting',schema=schema,key=key),value)
    listing=GLib.Variant.parse(None,(run(['gsettings','get','org.gnome.Terminal.ProfilesList','list']) if settings else '@as []'),None,None).unpack()
    if TERMINAL_UUID in listing[:-1] or listing.count(TERMINAL_UUID)>1:raise RuntimeError('Unexpected duplicate GNU-Darwin Workstation profile in GNOME Terminal list')
    add(dict(kind='gsetting',schema='org.gnome.Terminal.ProfilesList',key='list'),repr(listing if TERMINAL_UUID in listing else listing+[TERMINAL_UUID]))
    add(dict(kind='gsetting',schema='org.gnome.Terminal.ProfilesList',key='default'),repr(TERMINAL_UUID))
    # Ptyxis palettes for host and installed Flatpak; select in whichever profile store exists.
    palette=fragments['ptyxis/'+SLUG+'.palette']
    for dr in [HOME/'.local/share/org.gnome.Ptyxis/palettes',HOME/'.var/app/app.devsuite.Ptyxis/data/org.gnome.Ptyxis/palettes']:file(dr/(SLUG+'.palette'),palette)
    flatpak=HOME/'.var/app/app.devsuite.Ptyxis'
    keyfile=flatpak/'config/glib-2.0/settings/keyfile'
    if keyfile.is_file():
        if keyfile.is_symlink():raise RuntimeError('Ptyxis settings keyfile symlink requires review')
        active=ini_get(keyfile.read_text(),'org/gnome/Ptyxis','default-profile-uuid')
        if not isinstance(active,str) or not re.fullmatch(r"'[a-f0-9]{32}'",active):raise RuntimeError('Unexpected installed Ptyxis profile UUID')
        file(flatpak/'data/app.devsuite.Ptyxis/palettes'/(SLUG+'.palette'),palette)
        ini(keyfile,'org/gnome/Ptyxis/Profiles/'+active.strip("'"),'palette',repr(SLUG))
    else:skipped.append('Installed Flatpak Ptyxis keyfile absent; app-ID selection skipped.')
    pu=run(['dconf','read','/org/gnome/Ptyxis/default-profile-uuid']).strip("'") if settings else ''
    if pu:add(dict(kind='dconf',key=f'/org/gnome/Ptyxis/Profiles/{pu}/palette'),repr(SLUG))
    else:skipped.append('Ptyxis host default profile not accessible.')
    # GtkSourceView 3/4/5 scheme used by Xed, Builder and other source views; select it in Xed.
    xml=fragments['gtksourceview/'+SLUG+'.xml']
    for version in ['3.0','4','5']:file(HOME/f'.local/share/gtksourceview-{version}/styles'/(SLUG+'.xml'),xml)
    add(dict(kind='gsetting',schema='org.x.editor.preferences.editor',key='scheme'),repr(SLUG))
    # Nano: replace only existing `set <color>` lines and recognised syntax color literals.
    nanorc=HOME/'.nanorc'
    if nanorc.exists():
        for key,value in nano_keys(d).items():
            match=re.search(r'^set\s+'+key+r'\s+.*$',nanorc.read_text(),re.M)
            if match:patch(nanorc,match.group(),f'set {key} {value}')
        for line in nanorc.read_text().splitlines():
            match=re.match(r'^(extendsyntax\s+(\S+)\s+color\s+)#[0-9a-fA-F]{6}',line)
            if match and match.group(2) in ['c','python','sh','json','nanorc']:
                col=colors[4] if match.group(2)=='json' else colors[6] if match.group(2)=='nanorc' else colors[7]
                new=match.group(1)+col+line[match.end():]
                if new!=line:patch(nanorc,line,new)
    else:skipped.append('.nanorc absent; nano fragment generated only.')
    # Fastfetch: pin the palette and register text-safe bands; preserve original mark; the
    # user's short/long shell function, long.jsonc and the custom Local IP line are untouched.
    ff=HOME/'.config/fastfetch/config.jsonc'; registry=HOME/'.config/fastfetch/logos/hues/palettes.json'
    bands,bands_source=fastfetch_bands(d)
    jp(registry,[SLUG],bands)
    file(HOME/'.config/fastfetch/.theme',SLUG+'\n')
    if ff.exists():
        cfg=load(ff)
        for key,band in [('title',bands[0]),('keys',bands[2])]:jp(ff,['display','color',key],'38;2;'+';'.join(map(str,band)))
        groups={'os':1,'host':1,'kernel':1,'uptime':1,'packages':1,'shell':1,'de':2,'wm':2,'theme':2,'icons':2,'terminal':2,'terminalfont':2,'cpu':3,'gpu':3,'display':2,'memory':3,'swap':3,'disk':3,'localip':1}
        for i,item in enumerate(cfg['modules']):
            if not isinstance(item,dict):continue
            if item.get('type') in groups:jp(ff,['modules',i,'keyColor'],'38;2;'+';'.join(map(str,bands[groups[item['type']]])))
            elif item.get('type')=='custom' and item.get('key')=='Theme':
                jp(ff,['modules',i,'format'],name);jp(ff,['modules',i,'keyColor'],'38;2;'+';'.join(map(str,bands[2])))
    else:skipped.append(str(ff)+' absent; existing logo and wrappers remain untouched.')
    # Flatpak: only the GTK_THEME environment key; filesystem grants are preserved.
    ini(HOME/'.local/share/flatpak/overrides/global','Environment','GTK_THEME',name)
    # KDE colors affect KDE-aware applications; unrelated kdeglobals keys survive.
    for section,rows in kde_sections(d).items():
        for key,value in rows.items():ini(HOME/'.config/kdeglobals',section,key,value)
    # GIMP 2.10 only: own application preferences override desktop toolkit choice.
    # Use GIMP's personal gtkrc load path, not a process-wide GTK2_RC_FILES override.
    gd=HOME/'.config/GIMP/2.10'; gr=gd/'gimprc'; rc=gd/'gtkrc'
    guard_gimp_path(gr,allow_missing_ancestors=True);guard_gimp_path(rc,allow_missing_ancestors=True)
    if gd.exists() or gd.is_symlink():
        guard_gimp_path(gr);guard_gimp_path(rc)
        if gr.exists():
            for before,after in gimp_appearance_patches(preserved_text(gr)):patch(gr,before,after,preserve_newlines=True)
        else:file(gr,fragments['gimp/gimprc-appearance.conf'])
        if rc.exists():patch(rc,'',fragments['gimp/gtkrc-include.conf'],preserve_newlines=True)
        else:file(rc,fragments['gimp/gtkrc-include.conf'])
    else:skipped.append('GIMP2.10 profile absent; appearance fragments generated, no profile created.')
    for action in actions:
        if action.get('path') in {str(gr),str(rc)}:action['gimp_path_guard']=True
    # Deliberately untouched surfaces.
    mixed=[HOME/'Documents/eww-graphite-brass/config/themes/palettes.json',HOME/'Documents/eww-graphite-brass/config/eww.scss',
           HOME/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget/appearance.json',HOME/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget/ui/graphite-brass.css',
           HOME/'Projects/mint-dashboard/config.json',HOME/'.config/mpv/mpv.conf',HOME/'.config/cava/config',HOME/'.config/cava/panel.conf',HOME/'.local/bin/cava-panel.sh']
    skipped+=[str(p)+' — '+PROTECTED_MIXED for p in mixed]
    skipped+=['Eww widgets: not themed; the existing theme watcher controls their palette.',
              'Browser/Electron content themes, Tilda, Krita, Blender UI and LibreOffice canvas colors are not covered by this adapter.',
              'Qt palette honors KDE-aware applications only; general Qt platform/plugin selection remains unchanged.']
    protected=load(ROOT.parents[1]/'audits/applications.json')['protected_files']
    protected=[str(HOME/Path(p).relative_to(Path.home())) if Path(p).is_relative_to(Path.home()) else p for p in protected]
    return {'theme':name,'id':SLUG,'created_at':datetime.now(timezone.utc).isoformat(),
            'touched_paths':sorted({a['path'] for a in actions if 'path' in a}),
            'gsettings':[{'schema':a['schema'],'key':a['key'],'before':a['before'],'after':a['after']} for a in actions if a['kind']=='gsetting'],
            'dconf':[{'key':a['key'],'before':a['before'],'after':a['after']} for a in actions if a['kind']=='dconf'],
            'gnome_terminal_profile':TERMINAL_UUID,'fastfetch_bands_source':bands_source,
            'actions':actions,'skipped':skipped,
            'protected_hashes':{p:digest(Path(p).read_bytes()) for p in protected if Path(p).is_file()},
            'shell_startup_files_untouched':True,'fastfetch_logo_source_untouched':True}

def validate_protected(state):
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

def generate():
    written=[]
    for name,text in render().items():
        p=GENERATED/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);written.append(str(p))
    return written

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['plan','generate','apply','restore','check']);parser.add_argument('--state',type=Path);parser.add_argument('--commit',action='store_true');args=parser.parse_args()
    if args.command=='plan':
        p=plan();print(json.dumps(p,indent=2));return
    if args.command=='generate':
        print(json.dumps({'generated':generate()},indent=2));return
    if not args.state:parser.error('--state is required')
    if args.command=='apply':
        if args.state.exists():raise RuntimeError('State already exists; choose a fresh path')
        state=plan()
        if not args.commit:print(json.dumps({'preview':True,'actions':len(state['actions']),'skipped':state['skipped']},indent=2));return
        validate_protected(state)
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
