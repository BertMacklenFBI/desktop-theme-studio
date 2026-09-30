#!/usr/bin/python3
"""Install approved Apple art and numbered-pager styling through scoped transactions."""
import base64
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gnu_darwin_logo_transaction', ROOT/'applications/adapter.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
_guard = engine.guard_gimp_path
engine.guard_gimp_path = lambda path, **kw: _guard(path, allow_missing_ancestors=True)
SOURCE = ROOT/'artwork/fastfetch-lm-apple.png'


def plan():
    home = engine.HOME
    target = home/'.config/fastfetch/logos/hues/macklenmobile-logo-gnu-darwin-workstation.png'
    config = home/'.config/fastfetch/config.jsonc'
    actions = []
    def add(action, after):
        # Reuse the engine's lexical ancestor guard at plan/current/write/restore.
        action['gimp_path_guard'] = True
        action['after'] = after
        before = engine.current(action)
        if before != after:
            actions.append({**action, 'before':before, 'after':after, 'applied':False})
    raw = SOURCE.read_bytes()
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise RuntimeError('Logo source is not PNG')
    add({'kind':'file','path':str(target)}, base64.b64encode(raw).decode())
    css=home/'.themes/GNU-Darwin Workstation/cinnamon/cinnamon.css'
    if css.is_file():
        suffix=(ROOT/'desktop/workspace-picker.css').read_text()
        engine.guard_gimp_path(css)
        # Fresh installs already carry these rules; older kept releases receive a
        # journaled suffix whose removal recovers their exact original asset hash.
        if not css.read_text().endswith(suffix):
            add({'kind':'suffix','path':str(css)},suffix)
    skipped=[]
    if config.is_file():
        add({'kind':'json','path':str(config),'keys':['logo','source']}, str(target))
        # The user's existing hue helper selects these bands on every normal
        # fastfetch invocation. Match it in this final layer so it cannot undo
        # the earlier application stage's receipt expectations.
        registry=home/'.config/fastfetch/logos/hues/palettes.json'
        if registry.is_file():
            bands=engine.load(registry).get(engine.SLUG)
            if not (isinstance(bands,list) and len(bands)==6 and all(isinstance(c,list) and len(c)==3 and all(type(v)==int and 0<=v<=255 for v in c) for c in bands)):
                raise RuntimeError('Missing or invalid registered Fastfetch palette')
            cfg=engine.load(config)
            for index,item in enumerate(cfg.get('modules',[])):
                band={'display':4,'memory':5,'swap':5,'disk':5}.get(item.get('type')) if isinstance(item,dict) else None
                if band is not None:
                    add({'kind':'json','path':str(config),'keys':['modules',index,'keyColor']},'38;2;'+';'.join(map(str,bands[band])))
    else: skipped.append('Fastfetch configuration absent; logo installed without creating preferences.')
    protected = engine.load(ROOT.parents[1]/'audits/applications.json')['protected_files']
    return {'theme':'GNU-Darwin Workstation', 'stage':'approved-apple-logo',
            'source':str(SOURCE), 'source_sha256':engine.digest(raw), 'actions':actions,
            'touched_paths':sorted({a['path'] for a in actions}), 'gsettings':[], 'dconf':[], 'skipped':skipped,
            'protected_hashes':{p:engine.digest(Path(p).read_bytes()) for p in protected if Path(p).is_file()},
            'original_artwork_preserved':True,'fastfetch_modes_and_modules_preserved':True}


engine.plan = plan
if __name__ == '__main__': engine.main()
