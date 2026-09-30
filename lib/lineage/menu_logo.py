#!/usr/bin/env python3
"""Change only the existing Cinnamon menu icon; preview unless --commit."""
import argparse,ast,hashlib,json,os,struct,subprocess,sys,tempfile
from pathlib import Path
HERE=LINEAGE_PRESET/'desktop'
UUID='menu@cinnamon.org'
INSTANCE='17'
SETTINGS=Path.home()/'.config/cinnamon/spices'/UUID/(INSTANCE+'.json')
LOGO=HERE.parent/'artwork/menu-logo.png'
KEYS=('menu-icon','menu-custom')
def sha(b):return hashlib.sha256(b).hexdigest()
def load(p):return json.loads(Path(p).read_text())
def active():
    r=subprocess.run(['gsettings','get','org.cinnamon','enabled-applets'],capture_output=True,text=True,check=True,timeout=8)
    values=ast.literal_eval(r.stdout)
    if not any(x.split(':')[-2:]==[UUID,INSTANCE] for x in values):raise RuntimeError('Expected existing menu instance 17 is no longer enabled; refusing to select another instance.')
def label_hash(data):return sha(json.dumps(data['menu-label']['value'],ensure_ascii=False).encode())
def atomic(path,data,private=False,expected=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if expected is not None and sha(path.read_bytes())!=expected:raise RuntimeError('Concurrent file update: '+str(path))
    mode=0o600 if private else path.stat().st_mode & 0o777
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,mode)
        if expected is not None and sha(path.read_bytes())!=expected:raise RuntimeError('Concurrent file update: '+str(path))
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
def save(path,state):atomic(path,(json.dumps(state,indent=2)+'\n').encode(),private=True)
def png_check():
    raw=LOGO.read_bytes()
    if raw[:8]!=b'\x89PNG\r\n\x1a\n' or raw[12:16]!=b'IHDR':raise RuntimeError('Menu artwork is not a PNG')
    width,height=struct.unpack('>II',raw[16:24])
    if not (0<width<=8192 and 0<height<=8192):raise RuntimeError('Invalid menu PNG dimensions')
    return {'path':str(LOGO),'sha256':sha(raw),'dimensions':[width,height]}
def refresh(value):
    # Cinnamon's implementation calls _checkSettings() once, reading both keys.
    r=subprocess.run(['gdbus','call','--session','--dest','org.Cinnamon','--object-path','/org/Cinnamon','--method','org.Cinnamon.updateSetting',UUID,INSTANCE,'menu-icon',json.dumps(value)],capture_output=True,text=True,check=True,timeout=8)
    return {'method':'org.Cinnamon.updateSetting','uuid':UUID,'instance':INSTANCE,'result':r.stdout.strip(),'visual_verified':False}
def plan():
    active();data=load(SETTINGS)
    if any(k not in data or 'value' not in data[k] for k in KEYS):raise RuntimeError('Menu schema changed')
    if data['menu-label']['value']!='cinabon':raise RuntimeError('Menu label changed from cinabon; inspect current settings before continuing.')
    return {'version':1,'uuid':UUID,'instance':INSTANCE,'path':str(SETTINGS),'status':'planned','keys':{k:{'before':data[k]['value'],'after':str(LOGO) if k=='menu-icon' else True} for k in KEYS},'label_sha256':label_hash(data),'logo_ready':LOGO.is_file(),'touched_paths':[str(SETTINGS)]}
def set_values(state,target):
    active();raw=SETTINGS.read_bytes();data=json.loads(raw)
    if label_hash(data)!=state['label_sha256']:raise RuntimeError('Menu label changed; refusing to overwrite it.')
    for key,values in state['keys'].items():
        if key not in KEYS:raise RuntimeError('Unexpected journal key')
        if data[key]['value'] not in (values['before'],values['after']):raise RuntimeError('Menu icon restore/apply conflict: '+key)
        data[key]['value']=values[target]
    atomic(SETTINGS,(json.dumps(data,indent=4,ensure_ascii=False)+'\n').encode(),expected=sha(raw))
    if label_hash(load(SETTINGS))!=state['label_sha256']:raise RuntimeError('Menu label invariant failed')
def restore(state,path):
    state['status']='restoring';save(path,state)
    if state.get('attempted'):set_values(state,'before')
    state['restore_refresh']=refresh(state['keys']['menu-icon']['before']);state['status']='restored';save(path,state)
def check(state):
    active();data=load(SETTINGS);target='before' if state.get('status') in ('restored','rolled-back') else 'after'
    problems=[k for k,v in state['keys'].items() if data[k]['value']!=v[target]]
    if label_hash(data)!=state['label_sha256']:problems.append('menu-label changed')
    if target=='after':
        if not LOGO.is_file():problems.append('logo missing')
        elif state.get('artwork',{}).get('sha256')!=sha(LOGO.read_bytes()):problems.append('logo content changed')
    return {'ok':not problems,'target':target,'problems':problems,'menu_label':data['menu-label']['value'],'uuid':UUID,'instance':INSTANCE,'visual_verified':False}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['plan','apply','check','restore']);p.add_argument('--state',type=Path);p.add_argument('--commit',action='store_true');a=p.parse_args()
    if a.command=='plan':print(json.dumps(plan(),indent=2));return
    if not a.state:p.error('--state required')
    if a.command=='check':
        result=check(load(a.state));print(json.dumps(result,indent=2));return 0 if result['ok'] else 1
    if a.command=='restore':
        state=load(a.state)
        if not a.commit:print(json.dumps({'preview':True,'keys':list(state['keys']),'target':'before'}));return
        restore(state,a.state);print(json.dumps(check(state),indent=2));return
    state=plan()
    if not a.commit:print(json.dumps({'preview':True,**state},indent=2));return
    if a.state.exists():raise RuntimeError('Use a fresh state path')
    state['artwork']=png_check();state['status']='applying';state['attempted']=True;save(a.state,state)
    try:
        set_values(state,'after');state['refresh']=refresh(str(LOGO));state['status']='applied';save(a.state,state)
    except Exception as exc:
        state['error']=str(exc)
        try:restore(state,a.state);state['status']='rolled-back'
        except Exception as err:state['status']='recovery-required';state['recovery_error']=str(err)
        save(a.state,state);raise
    print(json.dumps(check(state),indent=2))
if __name__=='__main__':
    try:sys.exit(main() or 0)
    except Exception as exc:print(json.dumps({'ok':False,'error':str(exc)},indent=2));sys.exit(2)
