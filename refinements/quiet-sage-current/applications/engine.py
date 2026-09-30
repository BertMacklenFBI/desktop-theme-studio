#!/usr/bin/env python3
"""Scoped application appearance transaction. Import and plan are read-only."""
import argparse, base64, hashlib, json, os, re, subprocess, sys, tempfile
from gi.repository import Gio, GLib
from pathlib import Path
from datetime import datetime, timezone
HERE=Path(__file__).resolve().parent
HOME=Path.home()
MISSING={'__macintosh_soft_missing__':True}

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
    fd,tmpname=tempfile.mkstemp(prefix='.'+p.name+'.macintosh-soft-',dir=p.parent)
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
        # Insertions may contain their original anchor. Prefer the longer exact state.
        for candidate in sorted((before,after),key=len,reverse=True):
            if candidate and text.count(candidate)==action['count']:return candidate
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

def validate_protected(state):
    if state.get('protected_prompt'):
        r=state['protected_prompt'];body=Path(r['path']).read_bytes().removesuffix(r['suffix'].encode())
        if digest(body)!=r['sha256']:raise RuntimeError('Non-appearance shell configuration changed')
    bad=[p for p,h in state['protected_hashes'].items() if not Path(p).is_file() or digest(Path(p).read_bytes())!=h]
    if bad:raise RuntimeError('Protected file changed: '+', '.join(bad))

def restore(state,state_path):
    validate_protected(state)
    preflight(state)
    pending=[a for a in state['actions'] if a.get('applied') or a.get('attempted')]
    # A write may have completed before its journal success flag: compare both states.
    for a in reversed(pending):
        if current(a) not in (a['before'],a['after']):raise RuntimeError('Restore conflict: '+str(a.get('path',a.get('key'))))
    for a in reversed(pending):
        if current(a)==a['after']:write(a,a['before'])
        a['applied']=False;a['attempted']=False;journal(state_path,state)
    state['status']='restored';journal(state_path,state);validate_protected(state)

def preflight(state):
    pass  # Adapter supplies scoped external-application checks.

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
        preflight(state)
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
