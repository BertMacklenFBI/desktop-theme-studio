#!/usr/bin/python3
"""Quiet Sage scoped appearance transaction; plan is read-only, fresh receipts only."""
import base64, copy, json, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from gi.repository import GLib
import engine, office, render
HERE=Path(__file__).resolve().parent;PRESET=HERE.parent;HOME=Path.home()
DASH_REL='Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget'
old_current,old_write,old_validate=engine.current,engine.write,engine.validate_protected

def current(a):
    return office.xml_get(Path(a['path']).read_text()) if a['kind']=='xmlprop' else old_current(a)
def ensure_closed(name):
    result=subprocess.run(['pgrep','-u',str(__import__('os').getuid()),'-x',name],capture_output=True,timeout=5)
    if result.returncode==0:raise RuntimeError(name+' is running; coordinator must close it normally before preference changes')
    if result.returncode!=1:raise RuntimeError('Unable to verify '+name+' process state')
def write(a,value):
    if a.get('requires_closed'):ensure_closed(a['requires_closed'])
    if a['kind']=='xmlprop':
        path=Path(a['path']);engine.atomic(path,office.xml_set(path.read_text(),value).encode())
    else:old_write(a,value)
def noncolor(text):
    for key in ('background','foreground','gradient'):text=engine.ini_set(text,'color',key,engine.MISSING)
    return text.rstrip()+'\n'
def validate(state):
    old_validate(state)
    for p,h in state.get('required_assets',{}).items():
        if not Path(p).is_file() or engine.digest(Path(p).read_bytes())!=h:raise RuntimeError('Identity asset changed: '+p)
    for item in state.get('appearance_invariants',[]):
        text=Path(item['path']).read_text()
        if item['kind']=='cava':text=noncolor(text)
        else:
            for patch in item['patches']:
                matches=[value for value in (patch['before'],patch['after']) if value and value in text]
                if not matches:raise RuntimeError('Protected appearance anchor changed: '+item['path'])
                text=text.replace(max(matches,key=len),'__APPEARANCE__')
        if engine.digest(text.encode())!=item['sha256']:raise RuntimeError('Non-appearance content changed: '+item['path'])
def preflight(state):
    for name in state.get('requires_closed',[]):ensure_closed(name)
engine.current=current;engine.write=write;engine.validate_protected=validate;engine.preflight=preflight

def plan(home=HOME,settings=True,logo_path=None):
    d=engine.load(PRESET/'design.json');t=d['palette'];colors=d['terminal_ansi'];slug=d['id'];name=d['name'];actions=[];skipped=[];invariants=[]
    logo=Path(logo_path) if logo_path is not None else PRESET/'artwork/menu-logo.png'
    if not logo.is_file():raise RuntimeError('Canonical Quiet Sage menu-logo.png is required before planning')
    def add(a,after):
        if 'path' in a and Path(a['path']).is_symlink():raise RuntimeError('Expected regular appearance file: '+a['path'])
        if a['kind']=='gsetting':after=GLib.Variant.parse(None,after,None,None).print_(True)
        a['after']=after
        if 'before' not in a:a['before']=current(a)
        if a['before']!=after:actions.append(a)
    def file(path,value):add({'kind':'file','path':str(path)},base64.b64encode(value.encode() if isinstance(value,str) else value).decode())
    def jp(path,keys,value):
        if path.exists():add({'kind':'json','path':str(path),'keys':keys},value)
        else:skipped.append(str(path)+' absent')
    def ini(path,section,key,value,closed=None):
        a={'kind':'ini','path':str(path),'section':section,'key':key}
        if closed:a['requires_closed']=closed
        add(a,value)
    def patch(path,before,after):
        if before==after:return
        if not path.exists():skipped.append(str(path)+' absent');return
        text=path.read_text();count=text.count(before) if before else 1
        if before and count==0:raise RuntimeError('Appearance patch anchor absent: '+str(path))
        if not before and after in text:return
        add({'kind':'text','path':str(path),'before':before,'count':count},after)
    def gs(schema,key,value):
        if settings:add({'kind':'gsetting','schema':schema,'key':key},value)
    # Current-state charcoal terminal and application appearance; preserve behavior.
    file(home/'.config/kitty/quiet-sage-current.conf',render.kitty(t,colors))
    kitty=home/'.config/kitty/kitty.conf'
    if kitty.exists():
        patch(kitty,'include macintosh-soft-evergreen.conf','include quiet-sage-current.conf')
    else:file(kitty,'include quiet-sage-current.conf\n')
    file(home/'.local/share/konsole/quiet-sage-current.colorscheme',render.konsole(t,colors))
    konsole=home/'.config/konsolerc'
    if konsole.exists():
        profile=engine.ini_get(konsole.read_text(),'Desktop Entry','DefaultProfile')
        if isinstance(profile,str) and (home/'.local/share/konsole'/profile).exists():ini(home/'.local/share/konsole'/profile,'Appearance','ColorScheme',slug)
        else:skipped.append('Konsole active profile unavailable; palette built only')
    if settings:
        profile=engine.run(['gsettings','get','org.gnome.Terminal.ProfilesList','default']).strip("'")
        if not re.fullmatch(r'[a-f0-9-]{36}',profile):raise RuntimeError('Unexpected GNOME Terminal active UUID')
        schema=f'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{profile}/'
        values={'background-color':repr(t['terminal']),'foreground-color':repr(t['terminal_foreground']),'palette':repr(colors),'use-theme-colors':'false','use-theme-transparency':'false','use-transparent-background':'false','background-transparency-percent':'0','use-system-font':'false','font':repr('Hack 11'),'cursor-colors-set':'true','cursor-background-color':repr(colors[3]),'cursor-foreground-color':repr(t['terminal']),'bold-color-same-as-fg':'true','highlight-colors-set':'true','highlight-background-color':repr(t['selection']),'highlight-foreground-color':repr(t['selection_foreground'])}
        for key,value in values.items():gs(schema,key,value)
    palette=render.ptyxis(t,colors)
    for folder in [home/'.local/share/org.gnome.Ptyxis/palettes',home/'.var/app/app.devsuite.Ptyxis/data/org.gnome.Ptyxis/palettes',home/'.var/app/app.devsuite.Ptyxis/data/app.devsuite.Ptyxis/palettes']:file(folder/'quiet-sage-current.palette',palette)
    keyfile=home/'.var/app/app.devsuite.Ptyxis/config/glib-2.0/settings/keyfile'
    if keyfile.exists():
        profile=engine.ini_get(keyfile.read_text(),'org/gnome/Ptyxis','default-profile-uuid')
        if not isinstance(profile,str) or not re.fullmatch(r"'[a-f0-9]{32}'",profile):raise RuntimeError('Unexpected Ptyxis profile UUID')
        ini(keyfile,'org/gnome/Ptyxis/Profiles/'+profile.strip("'"),'palette',repr(slug),'ptyxis')
        for key,value in [('use-system-font','false'),('font-name',repr('Hack 11'))]:ini(keyfile,'org/gnome/Ptyxis',key,value,'ptyxis')
        css=home/'.var/app/app.devsuite.Ptyxis/config/gtk-4.0/gtk.css';suffix='\n'+render.ptyxis_css(t)
        if css.exists():
            matches=list(re.finditer(r'/\* Macintosh Soft Evergreen app-local chrome\. \*/\n:root \{[^}]+\}',css.read_text()))
            if len(matches)!=1:raise RuntimeError('Expected exactly one current Evergreen Ptyxis chrome block')
            patch(css,matches[0].group(),render.ptyxis_css(t))
        else:file(css,suffix)
    if settings:
        profile=engine.run(['dconf','read','/org/gnome/Ptyxis/default-profile-uuid']).strip("'")
        if profile:add({'kind':'dconf','key':f'/org/gnome/Ptyxis/Profiles/{profile}/palette'},repr(slug))
        else:skipped.append('Host Ptyxis profile absent; Flatpak profile handled separately')
    tilda=home/'.config/tilda/config_0'
    if tilda.exists():
        desired={'scheme':'0','palette_scheme':'0','enable_transparency':'false','back_alpha':'65535'}
        for prefix,col in [('back',t['terminal']),('text',t['terminal_foreground']),('cursor',colors[3])]:
            desired.update({prefix+'_'+key:str(v*257) for key,v in zip(['red','green','blue'],engine.rgb(col))})
        desired['palette']='{'+', '.join(str(v*257) for col in colors for v in engine.rgb(col))+'}'
        for key,value in desired.items():
            match=list(re.finditer(r'^'+key+r'\s*=.*$',tilda.read_text(),re.M))
            if len(match)!=1:raise RuntimeError('Unexpected Tilda key '+key)
            before=match[0].group();after=key+'='+value
            if before!=after:add({'kind':'text','path':str(tilda),'before':before,'count':1,'requires_closed':'tilda'},after)
    for relative in ['stylesheet.css','5.4/stylesheet.css','3.8/stylesheet.css']:
        gtile=home/'.local/share/cinnamon/extensions/gTile@shuairan'/relative
        if gtile.is_file():
            marker='/* Macintosh Soft Evergreen gTile appearance; existing tiling behavior and geometry retained. */'
            raw=gtile.read_text()
            if raw.count(marker)!=1:raise RuntimeError('Expected exactly one current gTile appearance block')
            patch(gtile,raw[raw.index(marker):],render.gtile(t))
    # Current Fastfetch modes and wrappers stay untouched; project hue updater's exact colors now.
    ffroot=home/'.config/fastfetch';ff=ffroot/'config.jsonc';registry=ffroot/'logos/hues/palettes.json';bands=[engine.rgb(c) for c in d.get('fastfetch_palette',d['rainbow'])]
    jp(registry,[slug],bands);dest=registry.parent/f'macklenmobile-logo-{slug}.png';file(dest,logo.read_bytes());file(ffroot/'.theme',slug+'\n')
    if ff.exists():
        data=engine.load(ff);jp(ff,['logo','source'],str(dest))
        for key,index in [('title',0),('keys',2)]:jp(ff,['display','color',key],'38;2;'+';'.join(map(str,bands[index])))
        groups={'os':1,'host':1,'kernel':1,'uptime':1,'packages':1,'shell':1,'de':2,'wm':2,'theme':2,'icons':2,'terminal':2,'terminalfont':2,'cpu':3,'gpu':3,'display':4,'memory':5,'swap':5,'disk':5,'localip':1}
        for i,item in enumerate(data['modules']):
            if not isinstance(item,dict):continue
            if item.get('type') in groups:jp(ff,['modules',i,'keyColor'],'38;2;'+';'.join(map(str,bands[groups[item['type']]])))
            elif item.get('type')=='custom' and item.get('key')=='Theme':
                jp(ff,['modules',i,'format'],name);jp(ff,['modules',i,'keyColor'],'38;2;'+';'.join(map(str,bands[1])))
    # Existing prompt module appearance only; .bashrc and functions remain byte-identical.
    prompt=home/'.config/plum-afterglow/prompt.bash'
    if prompt.exists():
        patch(prompt,'38;2;154;174;164',engine.ansi(colors[8]));patch(prompt,'38;2;131;179;154',engine.ansi(colors[2]))
    for version in ['3.0','4','5']:file(home/f'.local/share/gtksourceview-{version}/styles/quiet-sage-current.xml',render.sourceview(t))
    gs('org.x.editor.preferences.editor','scheme',repr(slug))
    nano=home/'.nanorc'
    if nano.exists():
        values={'titlecolor':f"bold,{t['terminal']},{colors[2]}",'statuscolor':f"{t['terminal_foreground']},{t['terminal']}",'errorcolor':f"{t['terminal']},{colors[1]}",'promptcolor':f"{t['terminal_foreground']},{t['terminal']}",'selectedcolor':f"{t['selection_foreground']},{t['selection']}",'stripecolor':','+t['terminal'],'numbercolor':colors[8],'keycolor':colors[2],'functioncolor':t['terminal_foreground'],'scrollercolor':colors[8],'spotlightcolor':f"{t['terminal']},{colors[3]}",'minicolor':colors[8]}
        for key,value in values.items():
            match=re.search(r'^set\s+'+key+r'\s+.*$',nano.read_text(),re.M)
            if match:patch(nano,match.group(),f'set {key} {value}')
    ini(home/'.local/share/flatpak/overrides/global','Environment','GTK_THEME',name)
    # Supported Qt/KDE palette roles and Krita's interface-only scheme.
    for section,values in render.kde(t).items():
        for key,col in values.items():ini(home/'.config/kdeglobals',section,key,','.join(map(str,engine.rgb(col))))
    file(home/'.local/share/color-schemes/Quiet Sage.colors',render.kde_scheme(t))
    krita=home/'.config/kritarc'
    if krita.exists():ini(krita,'theme','Theme',name,'krita')
    else:skipped.append('Krita preferences absent; scheme installed only')
    gimp=home/'.config/GIMP/2.10/gimprc'
    if gimp.exists():
        matches=list(re.finditer(r'^\(theme\s+"[^"\n]*"\)\s*$',gimp.read_text(),re.M))
        if len(matches)!=1:raise RuntimeError('Expected one GIMP theme preference')
        if matches[0].group().strip()!='(theme "System")':add({'kind':'text','path':str(gimp),'before':matches[0].group(),'count':1,'requires_closed':'gimp-2.10'},'(theme "System")')
    # Match dark chrome toolbar icons; retain document/canvas and other XML entries.
    office_path=home/'.config/libreoffice/4/user/registrymodifications.xcu'
    if office_path.exists():
        if not Path('/usr/share/libreoffice/share/config/images_colibre_dark_svg.zip').is_file():raise RuntimeError('Installed dark Colibre SVG icons missing')
        add({'kind':'xmlprop','path':str(office_path),'section':office.XML_PATH,'key':office.XML_KEY,'requires_closed':'soffice.bin'},'colibre_dark_svg')
    # Native library: final scoped style sheet, source-faithful logo; no media/lifecycle edits.
    dash=home/DASH_REL
    theme={'accent':t['focus'],'warning':t['warning'],'critical':t['error'],'card_background':t['surface'],'border':t['border'],'surface':t['elevated'],'surface_soft':t['surface'],'hover':t['elevated'],'text':t['foreground'],'muted':t['muted'],'palette':[t['focus'],t['success'],t['warning'],t['error']],'font':'Ubuntu, sans-serif','logo':str(logo)}
    for key,value in theme.items():jp(dash/'appearance.json',['theme',key],value)
    file(dash/'ui/quiet-sage-current.css',render.library(t))
    html=dash/'ui/index.html'
    if html.exists():
        patch(html,'<link rel="stylesheet" href="macintosh-soft-evergreen.css">','<link rel="stylesheet" href="quiet-sage-current.css">')
        oldlogo=(PRESET.parents[1]/'refinements/macintosh-soft-evergreen/artwork/menu-logo.png').as_uri()
        if oldlogo in html.read_text():patch(html,oldlogo,logo.as_uri())
    hook=dash/'native_music_flow.py'
    if hook.exists():patch(hook,'Gdk.RGBA(40/255,50/255,54/255,1)','Gdk.RGBA('+','.join(str(v)+'/255' for v in engine.rgb(t['surface']))+',1)')
    cava=home/'.config/cava/config'
    if cava.exists():
        for key,value in [('background',repr(t['terminal'])),('foreground',repr(colors[2])),('gradient','0')]:ini(cava,'color',key,value)
        invariants.append({'path':str(cava),'kind':'cava','sha256':engine.digest(noncolor(cava.read_text()).encode())})
    for path in [hook,prompt,html]:
        edits=[a for a in actions if a.get('path')==str(path) and a['kind']=='text']
        if edits:
            normalized=path.read_text()
            for a in edits:normalized=normalized.replace(a['before'],'__APPEARANCE__')
            invariants.append({'path':str(path),'kind':'text','patches':[{'before':a['before'],'after':a['after']} for a in edits],'sha256':engine.digest(normalized.encode())})
    protected=engine.load(PRESET.parents[1]/'audits/applications.json')['protected_files']
    protected=[Path(p) if home==HOME else home/Path(p).relative_to(HOME) for p in protected if Path(p).is_relative_to(HOME)]
    protected += [home/'.bashrc',ffroot/'modes.py',ffroot/'long.jsonc',ffroot/'apply-hue-colors.py',dash/'dashboard.py',dash/'ui/app.js',dash/'backend/media.py',dash/'backend/music.py',home/'Documents/eww-graphite-brass/config/scripts/backend.py']
    touched={a['path'] for a in actions if 'path' in a}
    skipped += ['GTK2/3/4 theme selection, Cinnamon panel/menu, cursor and interface font are coordinator/desktop-owned.', 'Browser/Electron internal content and Blender private UI are not covered by toolkit palette selection.', 'Qt palette covers KDE-aware applications; no unverified Qt platform/plugin override is introduced.', 'Music Library and already-running terminals need coordinator-controlled reload or fresh windows for visual validation.']
    return {'theme':name,'created_at':datetime.now(timezone.utc).isoformat(),'actions':actions,'touched_paths':sorted(touched),'gsettings':[a for a in actions if a['kind']=='gsetting'],'skipped':skipped,'required_assets':{str(logo):engine.digest(logo.read_bytes())},'protected_hashes':{str(p):engine.digest(p.read_bytes()) for p in dict.fromkeys(protected) if str(p) not in touched and p.is_file()},'appearance_invariants':invariants,'requires_closed':sorted({a['requires_closed'] for a in actions if a.get('requires_closed')}),'coverage':['GNOME Terminal','Kitty','Konsole','Ptyxis host and Flatpak','Tilda','Fastfetch short/long hue identity','Xed/GtkSourceView','Nano','KDE roles','Krita scheme','GIMP System theme','LibreOffice dark toolbar icons','Native Music Library','Standalone Cava colors','gTile neutral/teal CSS'],'protected_edits':projected_edits(actions,{str(cava),str(hook),str(html),str(prompt)})}

def project_bytes(raw,actions):
    for a in actions:
        k=a['kind'];value=a['after']
        if k=='file':raw=base64.b64decode(value)
        elif k=='json':
            data=json.loads(raw);engine.put(data,a['keys'],value);raw=(json.dumps(data,indent=2)+'\n').encode()
        elif k=='ini':raw=engine.ini_set(raw.decode(),a['section'],a['key'],value).encode()
        elif k=='xmlprop':raw=office.xml_set(raw.decode(),value).encode()
        elif k=='text':raw=(raw.decode().replace(a['before'],value) if a['before'] else raw.decode()+value).encode()
        elif k=='suffix':raw+=value.encode()
        else:raise RuntimeError('Unknown file projection '+k)
    return raw

def projected_edits(actions,paths):
    result=[]
    for path in sorted(paths):
        selected=[a for a in actions if a.get('path')==path]
        if selected:
            before=Path(path).read_bytes();after=project_bytes(before,selected)
            result.append({'path':path,'before_sha256':engine.digest(before),'after_sha256':engine.digest(after),'before_base64':base64.b64encode(before).decode(),'scope':'Exact scoped appearance fields; nonappearance music/shell behavior unchanged'})
    return result

def stage():
    state=plan();out=HERE/'staged';out.mkdir(exist_ok=True)
    for prior in out.iterdir():
        if prior.is_file() and re.match(r'^\d+-',prior.name):prior.unlink()
    for index,a in enumerate(a for a in state['actions'] if a['kind']=='file'):
        (out/(f'{index:02d}-'+Path(a['path']).name)).write_bytes(base64.b64decode(a['after']))
    (HERE/'plan.json').write_text(json.dumps(state,indent=2)+'\n')
    (HERE/'inventory.json').write_text(json.dumps({'observed_at':state['created_at'],'paths':{p:{'exists':Path(p).exists(),'before_sha256':engine.digest(Path(p).read_bytes()) if Path(p).is_file() else None} for p in state['touched_paths']},'protected_hashes':state['protected_hashes'],'requires_closed':state['requires_closed'],'coverage':state['coverage'],'skipped':state['skipped']},indent=2)+'\n')
    print(json.dumps({'actions':len(state['actions']),'paths':len(state['touched_paths']),'requires_closed':state['requires_closed'],'protected':len(state['protected_hashes'])}))
if __name__=='__main__':
    engine.plan=plan
    if sys.argv[1:]==['stage']:stage()
    elif sys.argv[1:]==['preflight']:
        state=plan();preflight(state);print(json.dumps({'ok':True,'requires_closed':state['requires_closed']}))
    else:engine.main()
