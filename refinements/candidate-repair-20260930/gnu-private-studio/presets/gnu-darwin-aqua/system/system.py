#!/usr/bin/env python3
"""GNU-Darwin Aqua staged system appearance transaction.

No arguments previews; apply needs --commit and root. Ported from the Macintosh Soft
installer; the transaction mechanics (receipts, shared lock, exact alternatives
restore, GRUB drop-in, greeter key patch) are unchanged. What differs is the asset
contract: the GRUB and Plymouth pixmaps are static files produced by the asset
agents under system/grub/ and system/plymouth/ and are installed verbatim, the
`.in` templates are rendered from design.json, and /etc/plymouth/plymouthd.conf is
a new root-owned target.
"""
import argparse, base64, contextlib, datetime, fcntl, hashlib, json, os
from pathlib import Path
import re, shutil, subprocess, sys, tempfile, uuid
HERE = Path(__file__).resolve().parent
PRESET = HERE.parent
IDENTITY = json.loads((PRESET/'design.json').read_text())
NAME = IDENTITY['name']
SLUG = IDENTITY['id']
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 -]{0,63}', NAME): raise ValueError('Unsafe visible theme name')
if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', SLUG): raise ValueError('Unsafe theme slug')
PLY = f'/usr/share/plymouth/themes/{SLUG}/{SLUG}.plymouth'
GREETER = Path('/etc/lightdm/slick-greeter.conf')
OVERRIDE = Path('/etc/default/grub.d/zz-desktop-theme-studio.cfg')
PLYMOUTHD = Path('/etc/plymouth/plymouthd.conf')
STATE = Path('/var/lib/desktop-theme-studio') / SLUG
TREES = (f'/usr/share/themes/{NAME}', f'/usr/share/icons/{NAME} icons',
         f'/usr/share/icons/{NAME} cursors', f'/usr/share/backgrounds/{SLUG}',
         f'/usr/share/grub/themes/{SLUG}', f'/usr/share/plymouth/themes/{SLUG}')
GRUB_FONT_FALLBACKS = (Path('/usr/share/grub/unicode.pf2'), Path('/boot/grub/fonts/unicode.pf2'))
# Static asset contract with the GRUB and Plymouth agents: everything else in
# system/grub/ and system/plymouth/ is installed verbatim.
STATIC_SKIP_NAMES = {'README.md', 'mockup.png'}  # plus every *.py (generate.py, check.py) and *.in
STATIC_SKIP_DIRS = {'__pycache__', 'mockups'}
PLYMOUTH_SKIP_NAMES = {'plymouthd.conf'}
TEMPLATES = ('grub/theme.txt.in', f'plymouth/{SLUG}.script.in', 'plymouth/theme.plymouth.in')
LOGO_BLOCK = '''logo.source = Image("logo.png");
logo.h = screen_h * 0.22;
logo.w = logo.h * logo.source.GetWidth() / logo.source.GetHeight();
logo.sprite = Sprite(logo.source.Scale(logo.w, logo.h));
logo.sprite.SetPosition((screen_w-logo.w)/2, screen_h*0.40-logo.h/2, 1);'''
LOGO_MARKER = '// @OPTIONAL_LOGO@'
PNG = b'\x89PNG\r\n\x1a\n'

def sha(data): return hashlib.sha256(data).hexdigest()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()

def design_spec():
    """Normalize the canonical design.json schema in memory; never edit it."""
    design=json.loads((PRESET/'design.json').read_text())
    if design.get('name') != NAME or design.get('id') != SLUG:
        raise ValueError('Design must retain exact GNU-Darwin Aqua identity')
    tokens=dict(design['palette'])
    for key in ('desktop','background','foreground','surface','border','control_border',
                'selection','selection_foreground','accent','elevated','muted','error'):
        if key not in tokens: raise ValueError('Missing palette token: '+key)
    for key,value in tokens.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]*',key): raise ValueError('Unsafe palette key: '+key)
        if not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value): raise ValueError('Invalid palette token: '+key)
    family=design['typography']['ui_family'];size=design['typography']['ui_size_pt']
    if not isinstance(family,str) or not family.strip() or any(c in family for c in '\r\n'):
        raise ValueError('Invalid interface font')
    if not isinstance(size,(int,float)) or isinstance(size,bool) or not 8 <= size <= 24:
        raise ValueError('Invalid interface font size')
    cursor=design.get('geometry',{}).get('cursor_size',28)
    if not isinstance(cursor,int) or isinstance(cursor,bool) or not 16 <= cursor <= 96:
        raise ValueError('Invalid cursor size')
    design['tokens']=tokens
    design['typography']=dict(design['typography'],interface=f'{family} {size:g}')
    design['cursor_size']=cursor
    design['wallpaper']={'path':'artwork/wallpaper.png'}
    design['version']=1
    return design

def rgb(color): return tuple(int(color[n:n+2],16) for n in (1,3,5))
def rgb_floats(color): return ', '.join(f'{channel/255:.4f}' for channel in rgb(color))

def template_values(design):
    tokens=design['tokens']; values=dict(tokens,theme_name=NAME,slug=SLUG)
    for key,value in tokens.items(): values[key+'_rgb']=rgb_floats(value)
    return values

def render_template(text,values,relative='template'):
    for key,value in values.items(): text=text.replace('@'+key+'@',value)
    if '@' in text:
        leftover=sorted(set(re.findall(r'@[A-Za-z0-9_]*@?',text)))[:8]
        raise ValueError(f'Unresolved template token in {relative}: '+', '.join(leftover))
    return text

def missing_templates():
    return [str(HERE/relative) for relative in TEMPLATES if not (HERE/relative).is_file()]

def render_assets(design,logo=False):
    """Render the three `.in` templates. `logo` inserts the optional Plymouth overlay block."""
    absent=missing_templates()
    if absent: raise FileNotFoundError('Missing template inputs: '+', '.join(absent))
    values=template_values(design); assets={}
    assets['grub/theme.txt']=render_template((HERE/'grub/theme.txt.in').read_text(),values,'grub/theme.txt.in').encode()
    script=(HERE/f'plymouth/{SLUG}.script.in').read_text()
    script=script.replace(LOGO_MARKER,LOGO_BLOCK if logo else '// optional logo overlay not requested')
    assets[f'plymouth/{SLUG}.script']=render_template(script,values,f'plymouth/{SLUG}.script.in').encode()
    assets[f'plymouth/{SLUG}.plymouth']=render_template((HERE/'plymouth/theme.plymouth.in').read_text(),values,'plymouth/theme.plymouth.in').encode()
    return assets

def static_assets():
    """Inventory of verbatim files: {'grub/icons/ubuntu.png': Path, ...}. Templates, generators,
    mockups, READMEs, plymouthd.conf, __pycache__ and dotfiles are excluded."""
    found={}
    for group in ('grub','plymouth'):
        base=HERE/group
        if base.is_symlink(): raise ValueError('Asset directory must be a regular directory: '+str(base))
        if not base.is_dir(): continue
        for item in sorted(base.rglob('*')):
            rel=item.relative_to(base)
            if any(part in STATIC_SKIP_DIRS or part.startswith('.') for part in rel.parts): continue
            if item.is_dir(): continue
            if item.name.endswith(('.in','.py')) or item.name in STATIC_SKIP_NAMES: continue
            if group=='plymouth' and item.name in PLYMOUTH_SKIP_NAMES: continue
            if item.is_symlink():
                if not item.resolve().is_relative_to(PRESET.resolve()) or not item.resolve().is_file():
                    raise ValueError(f'Asset symlink escapes the preset: {item}')
            elif not item.is_file(): continue
            found[group+'/'+rel.as_posix()]=item
    return found
def run(*argv):
    p = subprocess.run(argv, text=True, capture_output=True)
    if p.returncode: raise RuntimeError(f'{argv[0]} failed: {p.stderr.strip() or p.stdout.strip()}')
    return p.stdout

def syncdir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def atomic(path, data, mode=0o644, uid=0, gid=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.'+path.name+'.studio-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fchmod(f.fileno(), mode)
            if os.geteuid() == 0: os.fchown(f.fileno(), uid, gid)
            os.fsync(f.fileno())
        os.replace(temp, path); syncdir(path.parent)
    finally:
        if os.path.lexists(temp): os.unlink(temp)

def save(folder, journal):
    atomic(folder/'transaction.json', (json.dumps(journal, indent=2)+'\n').encode(), 0o600)

def node(path):
    if path.is_symlink():
        st=path.lstat(); return dict(kind='symlink', target=os.readlink(path), uid=st.st_uid, gid=st.st_gid)
    if not path.exists(): return {'kind':'missing'}
    if not path.is_file(): raise ValueError(f'Expected file, found directory or device: {path}')
    st=path.stat()
    return dict(kind='file', sha256=sha(path.read_bytes()), mode=st.st_mode&0o7777, uid=st.st_uid, gid=st.st_gid)

def target(raw):
    p=Path(raw)
    if '..' in p.parts or not p.is_absolute(): raise ValueError(f'Invalid destination {raw}')
    if p not in (GREETER,OVERRIDE,PLYMOUTHD) and not any(p.is_relative_to(t) and p != Path(t) for t in TREES):
        raise ValueError(f'Destination outside this preset: {raw}')
    for parent in p.parents:
        if parent.is_symlink(): raise ValueError(f'Symlink destination ancestor: {parent}')
    return p

def alternatives():
    text=run('update-alternatives','--query','default.plymouth')
    result={'entries':{}}
    current=None
    for line in text.splitlines():
        key,sep,value=line.partition(': ')
        if key=='Status': result['status']=value
        elif key=='Value': result['value']=value
        elif key=='Alternative': current=value
        elif key=='Priority' and current: result['entries'][current]=int(value)
    if result.get('status') not in ('auto','manual') or 'value' not in result: raise ValueError('Unexpected alternatives query')
    return result

def greeter_values(design):
    return {'theme-name':NAME, 'icon-theme-name':NAME+' icons', 'cursor-theme-name':NAME+' cursors',
            'cursor-theme-size':str(design['cursor_size']),
            'background':TREES[3]+'/wallpaper.png', 'background-color':design['tokens']['desktop'],
            'draw-user-backgrounds':'false', 'font-name':design['typography']['interface']}

def greeter_patch(text,design=None):
    design=design or design_spec()
    values=greeter_values(design)
    lines=text.splitlines(keepends=True); out=[]; section=''; seen=set(); found=False
    def missing(): return [k+'='+v+'\n' for k,v in values.items() if k not in seen]
    for line in lines:
        match=re.match(r'\s*\[([^]]+)\]',line)
        if match:
            if section=='Greeter': out += missing()
            section=match.group(1); found |= section=='Greeter'
        keymatch=re.match(r'\s*([^#;=\s]+)\s*=',line)
        if section=='Greeter' and keymatch and keymatch.group(1) in values:
            k=keymatch.group(1)
            if k not in seen: out.append(k+'='+values[k]+'\n'); seen.add(k)
        else: out.append(line if line.endswith('\n') else line+'\n')
    if section=='Greeter': out += missing()
    if not found: out += ['\n[Greeter]\n']+missing()
    return ''.join(out).encode()

def logo_source(logo):
    """--logo (or --logo default) selects artwork/plymouth-logo.png; any other value is a supplied PNG path."""
    if not logo: return None
    return PRESET/'artwork/plymouth-logo.png' if logo=='default' else Path(logo).resolve()

def build(logo=None):
    ops=[]; missing=[]; info={}; design=design_spec()
    def add(dest,data=None,link=None):
        p=target(dest)
        after=dict(kind='symlink',target=link,uid=0,gid=0) if link is not None else dict(kind='file',sha256=sha(data),mode=0o644,uid=0,gid=0)
        for existing in ops:
            if existing['path']==str(p): ops.remove(existing); break
        ops.append(dict(path=str(p),planned_before=node(p),after=after,content=base64.b64encode(data).decode() if data is not None else None))
    def addfile(dest,source):
        if not source.is_file(): missing.append(str(source)); return False
        add(dest,source.read_bytes()); return True
    for part,dest in [(NAME,TREES[0]),(NAME+' icons',TREES[1]),(NAME+' cursors',TREES[2])]:
        source=PRESET/'desktop'/part
        if source.is_symlink(): raise ValueError('Preset source tree must be a regular directory: '+str(source))
        if not source.is_dir(): missing.append(str(source)); continue
        for item in sorted(source.rglob('*')):
            rel=item.relative_to(source)
            if item.is_symlink():
                link=os.readlink(item)
                if Path(link).is_absolute() or not item.resolve().is_relative_to(source.resolve()):
                    raise ValueError(f'Preset symlink escapes its tree: {item}')
                add(str(Path(dest)/rel),link=link)
            elif item.is_file(): addfile(str(Path(dest)/rel),item)
    # Static GRUB/Plymouth assets from the asset agents, verbatim.
    statics=static_assets(); info['static_assets']={'grub':0,'plymouth':0}
    for relative,source in statics.items():
        group,filename=relative.split('/',1)
        add((TREES[4] if group=='grub' else TREES[5])+'/'+filename,source.read_bytes()); info['static_assets'][group]+=1
    # Wallpaper: greeter background, Plymouth backdrop, and GRUB only when no bespoke scene exists.
    info['grub_background']='bespoke grub/background.png' if 'grub/background.png' in statics else 'wallpaper.png fallback (grub/background.png absent)'
    wallpaper=(PRESET/design['wallpaper']['path']).resolve()
    if not wallpaper.is_relative_to((PRESET/'artwork').resolve()): raise ValueError('Wallpaper must be inside preset artwork/')
    if not wallpaper.is_file(): missing.append(str(wallpaper))
    else:
        artwork=wallpaper.read_bytes()
        if not artwork.startswith(PNG): raise ValueError('Wallpaper must be a PNG')
        for dest in (TREES[3],TREES[5]): add(dest+'/wallpaper.png',artwork)
        if 'grub/background.png' not in statics: add(TREES[4]+'/wallpaper.png',artwork)
    # GRUB font: bundled font.pf2 wins; otherwise the installed generic Unicode font.
    if 'grub/font.pf2' in statics: info['grub_font']='bundled grub/font.pf2'
    else:
        font=next((f for f in GRUB_FONT_FALLBACKS if f.is_file()),GRUB_FONT_FALLBACKS[0])
        info['grub_font']='fallback '+str(font)
        addfile(TREES[4]+'/font.pf2',font)
    # Optional Plymouth logo overlay: opt-in only.
    logo_path=logo_source(logo); info['logo']='none'
    if logo_path is not None:
        payload=logo_path.read_bytes() if logo_path.is_file() else b''
        if not logo_path.is_file(): missing.append(str(logo_path))
        elif not payload.startswith(PNG): raise ValueError('Optional logo must be PNG; original bytes are preserved')
        else: add(TREES[5]+'/logo.png',payload); info['logo']=str(logo_path)
    # Rendered templates (override any pre-rendered static copy of the same name).
    absent=missing_templates(); info['templates_missing']=absent; info['logo_marker_present']=None
    if absent: missing.extend(absent)
    else:
        info['logo_marker_present']=LOGO_MARKER in (HERE/f'plymouth/{SLUG}.script.in').read_text()
        for relative,payload in render_assets(design,logo=logo_path is not None).items():
            group,filename=relative.split('/',1)
            add((TREES[4] if group=='grub' else TREES[5])+'/'+filename,payload)
    # /etc/plymouth/plymouthd.conf (DeviceScale and theme name; copied into the initrd by the hook).
    info['plymouthd_conf']={'source':str(HERE/'plymouth/plymouthd.conf'),'destination':str(PLYMOUTHD),
                            'exists_now':PLYMOUTHD.is_file(),'restore':'restore exact backup' if PLYMOUTHD.is_file() else 'delete (did not exist)'}
    addfile(str(PLYMOUTHD),HERE/'plymouth/plymouthd.conf')
    old=GREETER.read_text() if GREETER.exists() else ''
    add(str(GREETER),greeter_patch(old,design))
    add(str(OVERRIDE),('# Desktop Theme Studio: appearance only; preserve all existing boot behavior.\nGRUB_THEME="'+TREES[4]+'/theme.txt"\n').encode())
    build.info=info
    return ops,missing
build.info={}

def prerequisites(missing):
    for command in ('update-alternatives','update-grub','update-initramfs','grub-script-check'):
        if not shutil.which(command): missing.append('command: '+command)
    if not list(Path('/usr/lib').glob('*/plymouth/script.so')): missing.append('Plymouth script plugin')
    fallbacks=set()
    for source in (PRESET/'desktop'/(NAME+' icons')/'index.theme',PRESET/'desktop'/(NAME+' cursors')/'index.theme'):
        if source.is_file():
            for line in source.read_text().splitlines():
                if line.startswith('Inherits='): fallbacks.update(x.strip() for x in line.split('=',1)[1].split(',') if x.strip())
    for icon in sorted(fallbacks):
        if not (Path('/usr/share/icons')/icon).is_dir() and not (PRESET/'desktop'/icon).is_dir():
            missing.append('system icon fallback: '+icon)
    return missing

@contextlib.contextmanager
def lock():
    if os.geteuid()!=0: raise PermissionError('Explicit apply/restore requires root; preview/check do not')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Shared entry point for participating system installers; legacy tools may bypass it.
    with (STATE.parent/'system.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB); yield

def writeop(op,folder=None,before=False):
    p=target(op['path']); expected=op['before'] if before else op['after']
    if expected['kind']=='missing':
        if os.path.lexists(p): p.unlink(); syncdir(p.parent)
    elif expected['kind']=='symlink':
        p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_name('.'+p.name+'.studio-'+uuid.uuid4().hex)
        try:
            os.symlink(expected['target'],tmp); os.lchown(tmp,expected['uid'],expected['gid']); os.replace(tmp,p); syncdir(p.parent)
        finally:
            if os.path.lexists(tmp): tmp.unlink()
    else:
        payload=(folder/op['backup']).read_bytes() if before else base64.b64decode(op['content'])
        if sha(payload)!=expected['sha256']: raise ValueError('Payload checksum mismatch: '+str(p))
        atomic(p,payload,expected['mode'],expected['uid'],expected['gid'])
    if node(p)!=expected: raise RuntimeError('Verification failed: '+str(p))

def rebuild(expected_theme=None):
    run('update-grub')
    run('grub-script-check','/boot/grub/grub.cfg')
    if expected_theme is not None and expected_theme not in Path('/boot/grub/grub.cfg').read_text():
        raise RuntimeError('Generated GRUB configuration did not select the requested theme')
    # Update the current/default initramfs only; no parallel jobs or all-kernel rebuild.
    run('update-initramfs','-u')

def restore(folder,rollback=False):
    journal=json.loads((folder/'transaction.json').read_text())
    if journal.get('name') != NAME or journal.get('preset_id') != SLUG:
        raise ValueError('Receipt belongs to another preset')
    allowed=[journal['alternative_before']]
    registered=dict(journal['alternative_before'],entries=dict(journal['alternative_before']['entries'],**{PLY:50}))
    allowed += [registered,dict(registered,status='manual',value=PLY),
                dict(registered,status='manual',value=journal['alternative_before']['value']),
                dict(journal['alternative_before'],status='manual')]
    if alternatives() not in allowed: raise RuntimeError('Plymouth alternatives changed externally; inspect receipt before restore')
    for op in journal['operations']:
        p=target(op['path'])
        if node(p) not in (op['before'],op['after']): raise RuntimeError('Restore conflict: '+str(p))
        if op['before']['kind']=='file':
            if sha((folder/op['backup']).read_bytes())!=op['before']['sha256']: raise RuntimeError('Missing/corrupt backup: '+str(p))
    journal['status']='restoring'; save(folder,journal)
    try:
        for op in reversed(journal['operations']): writeop(op,folder,True)
        prior=journal['alternative_before']
        # Select the previous target before removing our registration, so removal cannot choose a different default.
        run('update-alternatives','--set','default.plymouth',prior['value'])
        if PLY not in prior['entries']:
            run('update-alternatives','--remove','default.plymouth',PLY)
        else:
            run('update-alternatives','--install','/usr/share/plymouth/themes/default.plymouth','default.plymouth',PLY,str(prior['entries'][PLY]))
        if prior['status']=='auto': run('update-alternatives','--auto','default.plymouth')
        else: run('update-alternatives','--set','default.plymouth',prior['value'])
        if alternatives()!=prior: raise RuntimeError('Previous Plymouth alternatives state was not restored exactly')
        rebuild()
        for directory in reversed(journal['created_directories']):
            try: Path(directory).rmdir()
            except OSError: pass
        journal['status']='rolled-back' if rollback else 'restored'; journal['restored_at']=now(); save(folder,journal)
    except Exception as exc:
        journal['status']='recovery-required'; journal['restore_error']=str(exc); save(folder,journal); raise
    return {'status':journal['status'],'receipt':str(folder/'transaction.json'),'visual_verification':'pending reboot/login'}

def apply(ops):
    for previous in STATE.glob('*/transaction.json'):
        if json.loads(previous.read_text()).get('status') in ('applying','restoring','recovery-required'):
            raise RuntimeError('Resolve incomplete system recovery first: '+str(previous))
    folder=STATE/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')+uuid.uuid4().hex[:8])
    folder.mkdir(mode=0o700)
    journal={'version':1,'name':NAME,'preset_id':SLUG,'created_at':now(),'status':'preparing','alternative_before':alternatives(),'operations':[],'created_directories':[]}
    # Capture and sync every original before installing a single file.
    for n,op in enumerate(ops):
        p=target(op['path']); op['before']=node(p)
        if op['before'] != op['planned_before']: raise RuntimeError('Target changed during planning: '+str(p))
        if op['before']['kind']=='file':
            op['backup']=f'{n}.backup'; payload=p.read_bytes()
            if sha(payload)!=op['before']['sha256']: raise RuntimeError('Original changed during backup: '+str(p))
            atomic(folder/op['backup'],payload,0o600)
        journal['operations'].append(op)
        parents=[]; parent=p.parent
        while not parent.exists(): parents.append(str(parent)); parent=parent.parent
        for parent in reversed(parents):
            if parent not in journal['created_directories']: journal['created_directories'].append(parent)
    journal['status']='applying'; save(folder,journal)
    try:
        for op in ops:
            if node(target(op['path']))!=op['before']: raise RuntimeError('Target changed during planning: '+op['path'])
            writeop(op)
        run('update-alternatives','--install','/usr/share/plymouth/themes/default.plymouth','default.plymouth',PLY,'50')
        run('update-alternatives','--set','default.plymouth',PLY)
        rebuild(TREES[4]+'/theme.txt')
        journal['status']='applied'; journal['applied_at']=now(); save(folder,journal)
    except Exception as exc:
        journal['error']=str(exc); save(folder,journal)
        try: restore(folder,rollback=True)
        except Exception as recovery:
            journal=json.loads((folder/'transaction.json').read_text()); journal['status']='recovery-required'; journal['restore_error']=str(recovery); save(folder,journal)
            raise RuntimeError(f'Apply failed; recovery needs attention: {recovery}. Receipt: {folder}') from exc
        raise RuntimeError(f'Apply failed and configuration restored. Receipt: {folder}') from exc
    return {'status':'applied','receipt':str(folder/'transaction.json'),'visual_verification':'pending reboot/login'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preview','apply','check','restore'),nargs='?',default='preview')
    parser.add_argument('--commit',action='store_true',help='Required for apply/restore writes')
    parser.add_argument('--transaction',help='Receipt directory name shown by apply')
    parser.add_argument('--logo',nargs='?',const='default',
                        help='Opt-in Plymouth overlay: bare --logo or --logo default copies artwork/plymouth-logo.png; a path copies that PNG byte-for-byte')
    args=parser.parse_args()
    try:
        if args.action=='restore':
            if not args.transaction or Path(args.transaction).name!=args.transaction or args.transaction in ('.','..'): raise ValueError('Restore requires --transaction RECEIPT_ID')
            if not args.commit: result={'mode':'preview','restore_receipt':str(STATE/args.transaction/'transaction.json'),'instruction':'Add --commit under root to restore; all conflicts will be checked first'}
            else:
                with lock(): result=restore(STATE/args.transaction)
        else:
            ops,missing=build(args.logo); missing=prerequisites(missing)
            if args.action=='apply' and args.commit:
                if missing: raise ValueError('Missing dependencies: '+', '.join(missing))
                with lock(): result=apply(ops)
            elif args.action=='check':
                bad=[op['path'] for op in ops if node(target(op['path']))!=op['after']]
                selected=alternatives()
                result={'ok':not (bad or missing) and selected['value']==PLY,'mismatches':bad,'missing':missing,'plymouth':selected,'visual_verification':'not performed'}
                try:
                    grub=Path('/boot/grub/grub.cfg').read_text(); result['generated_grub_contains_theme']=(SLUG+'/theme.txt') in grub
                    if not result['generated_grub_contains_theme']: result['ok']=False
                except PermissionError: result['generated_grub_contains_theme']='unverified: root read needed'; result['ok']=False
            else:
                design=design_spec()
                result={'mode':'preview','name':NAME,'design_version':design['version'],
                        'design_sha256':sha((PRESET/'design.json').read_bytes()),
                        'wallpaper_source':design['wallpaper']['path'],
                        'wallpaper_sha256':next((op['after']['sha256'] for op in ops if op['path']==TREES[3]+'/wallpaper.png'),None),
                        'files':len(ops),'missing':missing,'inputs':build.info,'destinations':[op['path'] for op in ops],
                        'greeter_keys':greeter_values(design),
                        'commands_on_explicit_apply':['update-alternatives install/set default.plymouth','update-grub','grub-script-check /boot/grub/grub.cfg','update-initramfs -u'],
                        'unchanged':'Boot timeout (98_mintsysadm.cfg), /etc/default/grub, boot flags, EFI loaders, autologin, lock behavior, PAM and encryption configuration'}
        print(json.dumps(result,indent=2)); return 1 if result.get('ok') is False or result.get('missing') else 0
    except Exception as exc: print(json.dumps({'error':str(exc)}),file=sys.stderr); return 2
if __name__=='__main__': sys.exit(main())
