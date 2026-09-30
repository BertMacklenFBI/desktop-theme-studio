#!/usr/bin/env python3
"""Staged system appearance transaction. No arguments previews; apply needs --commit and root."""
import argparse, base64, contextlib, datetime, fcntl, hashlib, json, os
from pathlib import Path
import re, shutil, struct, subprocess, sys, tempfile, uuid, zlib
HERE = LINEAGE_PRESET / 'system'
PRESET = HERE.parent
OPTIONS = {'required_tokens': ('background','foreground','surface','border','selection','accent','accent_dark','elevated','muted'),
           'rgb_tokens': ('background','foreground','muted'), 'progress_track': 'border', 'receiver_panel': None,
           'menu_fill': 'surface', 'menu_edge': 'border', 'icon_fallbacks': ('Papirus-Dark','Papirus','hicolor','Bibata-Modern-Classic')}
if set(globals().get('LINEAGE_OPTIONS', {})) - set(OPTIONS): raise ValueError('Unknown lineage option: '+', '.join(sorted(set(LINEAGE_OPTIONS)-set(OPTIONS))))
OPTIONS.update(globals().get('LINEAGE_OPTIONS', {}))
IDENTITY = json.loads((PRESET/'design.json').read_text())
NAME = IDENTITY['name']
SLUG = IDENTITY['slug']
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 -]{0,63}', NAME): raise ValueError('Unsafe visible theme name')
if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', SLUG): raise ValueError('Unsafe theme slug')
PLY = f'/usr/share/plymouth/themes/{SLUG}/{SLUG}.plymouth'
GREETER = Path('/etc/lightdm/slick-greeter.conf')
OVERRIDE = Path('/etc/default/grub.d/zz-desktop-theme-studio.cfg')
STATE = Path('/var/lib/desktop-theme-studio') / SLUG
TREES = (f'/usr/share/themes/{NAME}', f'/usr/share/icons/{NAME} icons',
         f'/usr/share/icons/{NAME} cursors', f'/usr/share/backgrounds/{SLUG}',
         f'/usr/share/grub/themes/{SLUG}', f'/usr/share/plymouth/themes/{SLUG}')

def sha(data): return hashlib.sha256(data).hexdigest()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()

def design_spec():
    design=json.loads((PRESET/'design.json').read_text())
    if design.get('name') != NAME or design.get('slug') != SLUG: raise ValueError('Design must retain exact visible name '+NAME)
    for key in OPTIONS['required_tokens']:
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',design['tokens'][key]): raise ValueError('Invalid palette token: '+key)
    if any(c in design['typography']['interface'] for c in '\r\n'): raise ValueError('Invalid interface font')
    return design

def rgb(color): return tuple(int(color[n:n+2],16) for n in (1,3,5))

def ui_png(width,height,pixel):
    """Generate simple palette-only UI swatches; never edit supplied artwork."""
    def chunk(kind,data): return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    data=b''.join(b'\0'+b''.join(bytes(pixel(x,y)) for x in range(width)) for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(data))+chunk(b'IEND',b'')

def render_assets(design):
    tokens=design['tokens']; values=dict(tokens,theme_name=NAME,slug=SLUG)
    for key in OPTIONS['rgb_tokens']:
        values[key+'_rgb']=', '.join(f'{channel/255:.6f}' for channel in rgb(tokens[key]))
    assets={}
    for relative in ('grub/theme.txt',f'plymouth/{SLUG}.script'):
        text=(HERE/(relative+'.in')).read_text()
        for key,value in values.items(): text=text.replace('@'+key+'@',value)
        if re.search(r'@[a-z][a-z_]*@',text): raise ValueError('Unresolved system template token: '+relative)
        assets[relative]=text.encode()
    assets['plymouth/progress-track.png']=ui_png(1,1,lambda x,y:rgb(tokens[OPTIONS['progress_track']]))
    assets['plymouth/progress-fill.png']=ui_png(1,1,lambda x,y:rgb(tokens['accent']))
    if OPTIONS['receiver_panel']:
        def receiver_pixel(x,y,fill=OPTIONS['receiver_panel'][0],frame=OPTIONS['receiver_panel'][1]):
            edge=min(x,y,479-x,119-y)
            if edge>=5: return rgb(tokens[fill])
            shade=(-6 if y%2 else 3) if edge>0 else -24
            return tuple(max(0,min(255,c+shade)) for c in rgb(tokens[frame]))
        assets['plymouth/receiver-panel.png']=ui_png(480,120,receiver_pixel)
    # Flat palette-only menu backgrounds, without material textures.
    for prefix,fill in (('menu',OPTIONS['menu_fill']),('selected','selection')):
        for position in ('c','n','s','e','w','ne','nw','se','sw'):
            width=8 if ('e' in position or 'w' in position) else 1
            height=8 if ('n' in position or 's' in position) else 1
            def pixel(x,y,position=position,width=width,height=height,fill=fill):
                edge=('n' in position and y<2) or ('s' in position and y>=height-2) or ('w' in position and x<2) or ('e' in position and x>=width-2)
                return rgb(tokens[OPTIONS['menu_edge']] if edge else tokens[fill])
            assets['grub/'+prefix+'_'+position+'.png']=ui_png(width,height,pixel)
    return assets
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
    if p not in (GREETER,OVERRIDE) and not any(p.is_relative_to(t) and p != Path(t) for t in TREES):
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

def greeter_patch(text,design=None):
    design=design or design_spec()
    values={'theme-name':NAME, 'icon-theme-name':NAME+' icons', 'cursor-theme-name':NAME+' cursors',
            'background':TREES[3]+'/wallpaper.png', 'background-color':design['tokens']['background'],
            'draw-user-backgrounds':'false', 'font-name':design['typography']['interface']}
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

def build(logo=None):
    ops=[]; missing=[]; design=design_spec(); rendered=render_assets(design)
    def add(dest,data=None,link=None):
        p=target(dest)
        after=dict(kind='symlink',target=link,uid=0,gid=0) if link is not None else dict(kind='file',sha256=sha(data),mode=0o644,uid=0,gid=0)
        ops.append(dict(path=str(p),planned_before=node(p),after=after,content=base64.b64encode(data).decode() if data is not None else None))
    def addfile(dest,source):
        if not source.is_file(): missing.append(str(source)); return
        add(dest,source.read_bytes())
    for part,dest in [(NAME,TREES[0]),(NAME+' icons',TREES[1]),(NAME+' cursors',TREES[2])]:
        source=PRESET/'desktop'/part
        if not source.is_dir(): missing.append(str(source)); continue
        for item in sorted(source.rglob('*')):
            rel=item.relative_to(source)
            if item.is_symlink():
                link=os.readlink(item)
                if Path(link).is_absolute() or not item.resolve().is_relative_to(source.resolve()):
                    raise ValueError(f'Preset symlink escapes its tree: {item}')
                add(str(Path(dest)/rel),link=link)
            elif item.is_file(): addfile(str(Path(dest)/rel),item)
    wallpaper=(PRESET/design['wallpaper']['path']).resolve()
    if not wallpaper.is_relative_to((PRESET/'artwork').resolve()): raise ValueError('Wallpaper must be inside preset artwork/')
    if not wallpaper.is_file(): missing.append(str(wallpaper))
    else:
        artwork=wallpaper.read_bytes()
        if not artwork.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('Wallpaper must be a PNG')
        for dest in (TREES[3],TREES[4],TREES[5]): add(dest+'/wallpaper.png',artwork)
    for relative,payload in rendered.items():
        group,filename=relative.split('/',1)
        if filename != SLUG+'.script': add((TREES[4] if group=='grub' else TREES[5])+'/'+filename,payload)
    font=Path('/usr/share/grub/themes/nocturne/font.pf2')
    if not font.is_file(): font=Path('/boot/grub/fonts/unicode.pf2')
    addfile(TREES[4]+'/font.pf2',font)
    descriptor=(HERE/'plymouth/theme.plymouth.in').read_text().replace('@theme_name@',NAME).replace('@slug@',SLUG)
    add(TREES[5]+'/'+SLUG+'.plymouth',descriptor.encode())
    script=rendered[f'plymouth/{SLUG}.script'].decode()
    if logo:
        supplied=Path(logo).resolve(); payload=supplied.read_bytes()
        if not payload.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('Optional logo must be PNG; original bytes are preserved')
        add(TREES[5]+'/logo.png',payload)
        block='''logo.source = Image("logo.png");
logo.h = screen_h * 0.18;
logo.w = logo.h * logo.source.GetWidth() / logo.source.GetHeight();
logo.sprite = Sprite(logo.source.Scale(logo.w, logo.h));
logo.sprite.SetPosition((screen_w-logo.w)/2, screen_h*0.45-logo.h/2, 1);'''
        script=script.replace('// @OPTIONAL_LOGO@',block)
    add(TREES[5]+'/'+SLUG+'.script',script.encode())
    old=GREETER.read_text() if GREETER.exists() else ''
    add(str(GREETER),greeter_patch(old,design))
    add(str(OVERRIDE),('# Desktop Theme Studio: appearance only; preserve all existing boot behavior.\nGRUB_THEME="'+TREES[4]+'/theme.txt"\n').encode())
    return ops,missing

def prerequisites(missing):
    for command in ('update-alternatives','update-grub','update-initramfs','grub-script-check'):
        if not shutil.which(command): missing.append('command: '+command)
    if not list(Path('/usr/lib').glob('*/plymouth/script.so')): missing.append('Plymouth script plugin')
    for icon in OPTIONS['icon_fallbacks']:
        if not (Path('/usr/share/icons')/icon).is_dir(): missing.append('system icon fallback: '+icon)
    return missing

@contextlib.contextmanager
def lock():
    if os.geteuid()!=0: raise PermissionError('Explicit apply/restore requires root; preview/check do not')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE/'lock').open('a') as handle:
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
    folder=STATE/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')+uuid.uuid4().hex[:8])
    folder.mkdir(mode=0o700)
    journal={'version':1,'name':NAME,'created_at':now(),'status':'preparing','alternative_before':alternatives(),'operations':[],'created_directories':[]}
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
    parser.add_argument('--logo',help='Optional supplied PNG copied byte-for-byte; default uses wallpaper only')
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
                result={'mode':'preview','name':NAME,'design_version':design_spec()['version'],
                        'design_sha256':sha((PRESET/'design.json').read_bytes()),
                        'wallpaper_source':design_spec()['wallpaper']['path'],
                        'wallpaper_sha256':next((op['after']['sha256'] for op in ops if op['path']==TREES[3]+'/wallpaper.png'),None),
                        'files':len(ops),'missing':missing,'destinations':[op['path'] for op in ops],
                        'commands_on_explicit_apply':['update-alternatives install/set default.plymouth','update-grub','grub-script-check /boot/grub/grub.cfg','update-initramfs -u'],
                        'unchanged':'Boot timeout, boot flags, EFI loaders, autologin, lock behavior, PAM and encryption configuration'}
        print(json.dumps(result,indent=2)); return 1 if result.get('ok') is False or result.get('missing') else 0
    except Exception as exc: print(json.dumps({'error':str(exc)}),file=sys.stderr); return 2
if __name__=='__main__': sys.exit(main())
