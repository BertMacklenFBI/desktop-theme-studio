#!/usr/bin/python3
"""Validate the built GNU-Darwin Workstation theme: GTK3/GTK4 CSS parse, asset hygiene, palette leakage. Read-only by default; --write saves validation.json, --output PATH saves an explicit report. No live settings."""
from pathlib import Path
import argparse,json,re,subprocess,sys,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;D=json.loads((ROOT/'design.json').read_text());OUT=HERE/D['name']
parser=argparse.ArgumentParser(description=__doc__)
group=parser.add_mutually_exclusive_group()
group.add_argument('--write',action='store_true',help='Save desktop/validation.json')
group.add_argument('--output',type=Path,help='Save report to this explicit path')
args=parser.parse_args()
def parse(version):
 code=f"""
import gi,sys,json
gi.require_version('Gtk','{version}.0')
from gi.repository import Gtk
p=Gtk.CssProvider();errs=[]
p.connect('parsing-error',lambda pr,sec,err:errs.append(str(err)+' @ '+(f'{{sec.get_start_line()+1}}:{{sec.get_start_position()}}' if hasattr(sec,'get_start_line') else str(sec.get_start_location().lines+1 if hasattr(sec,'get_start_location') else '?'))))
p.load_from_path({json.dumps(str(OUT/f'gtk-{version}.0/gtk.css'))})
print(json.dumps(errs))
"""
 r=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
 try:errs=json.loads(r.stdout.strip().splitlines()[-1])
 except Exception:errs=['no result: '+r.stderr[-800:]]
 return {'exit':r.returncode,'parse_errors':errs,'stderr':[l for l in r.stderr.splitlines() if 'libEGL' not in l][:20],'sha256':hashlib.sha256((OUT/f'gtk-{version}.0/gtk.css').read_bytes()).hexdigest()}
V={'gtk3':parse('3'),'gtk4':parse('4')}
# GTK2 rc parser can parse without opening a display. Report diagnostics separately.
code="import ctypes,ctypes.util;g=ctypes.CDLL(ctypes.util.find_library('gtk-x11-2.0'));g.gtk_rc_parse.argtypes=[ctypes.c_char_p];g.gtk_rc_parse("+repr(str(OUT/'gtk-2.0/gtkrc').encode())+")"
r=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
V['gtk2']={'exit':r.returncode,'diagnostics':r.stderr.strip(),'method':'gtk_rc_parse via installed libgtk-x11-2.0; no display or widgets'}

pngs={}
for kit in ['gtk-3.0','gtk-4.0']:
 refs=[]
 for f in (OUT/kit).glob('*.css'):refs+=[f.name+':'+m for m in re.findall(r'[^\s"\'()]+\.png',f.read_text())]
 pngs[kit]=refs
V['png_references_in_gtk_css']=pngs
V['define_color_lines']={kit:sum(1 for l in (OUT/kit/'gtk.css').read_text().splitlines() if l.startswith('@define-color')) for kit in ['gtk-3.0','gtk-4.0']}
MINT=['35a854','6db442','9ab87c','3bbb5e','298141','88c663','568f34','9ad4aa','a4d8b2','73d216','d7eedd','e1f2e5','30974c','2f954a','5294e2','102b68','4ac66b','71d28b','98deab','5acb78','3ab85c','1d5a2d','236e37','f04a50','fc4138','f27835']
leak={}
derived=json.loads((HERE/'build-report.json').read_text())['derived_tokens']
allowed={v.lower().lstrip('#') for v in list(D['palette'].values())+D['rainbow']+[x for x in derived.values() if x.startswith('#')]}|{'ffffff','000000'}
foreign={}
for f in OUT.rglob('*'):
 if f.is_file() and f.suffix in ['.css','.svg','.xml','.rc','.theme']:
  s=f.read_text().lower()
  for h in MINT:
   n=s.count('#'+h)
   if n:leak[str(f.relative_to(OUT))+':#'+h]=n
  for h in set(re.findall(r'#([0-9a-f]{6})\b',s)):
   if h not in allowed:foreign.setdefault('#'+h,0);foreign['#'+h]+=s.count('#'+h)
V['mint_y_hex_leaks']=leak
V['non_palette_hexes']=dict(sorted(foreign.items(),key=lambda kv:-kv[1]))
V['non_palette_hexes_note']='Leftover literals outside the role map (white/black excluded); located per file in build-report.json foreign_hex_files.'
V['index_theme']=(OUT/'index.theme').read_text()
h=hashlib.sha256()
for p in sorted(OUT.rglob('*')):
 if p.is_file():h.update(str(p.relative_to(OUT)).encode());h.update(p.read_bytes())
V['theme_tree_sha256']=h.hexdigest()
pv=HERE/'preview-gtk3.png'
V['preview_gtk3']={'path':str(pv),'sha256':hashlib.sha256(pv.read_bytes()).hexdigest() if pv.exists() else None,'harness':str(HERE/'preview_gtk3.py'),'method':'Gtk.OffscreenWindow render, process-local GTK_THEME, no live settings'}
V['worker_visual_review']='Pending coordinator serialized private GTK/Cinnamon render; this validator is structural only.'
V['ok']=V['gtk2']['exit']==0 and not V['gtk2']['diagnostics'] and not V['gtk3']['parse_errors'] and not V['gtk4']['parse_errors'] and not any(pngs.values()) and not leak
if args.write or args.output:
 (args.output or HERE/'validation.json').write_text(json.dumps(V,indent=2)+'\n')
print(json.dumps({k:v for k,v in V.items() if k!='index_theme'},indent=2))

if not V["ok"]: sys.exit(1)
