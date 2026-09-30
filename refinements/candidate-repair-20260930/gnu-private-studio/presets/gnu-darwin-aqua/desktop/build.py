#!/usr/bin/python3
"""Build staged GNU-Darwin Aqua from the packaged Mint-Y-Dark snapshot in base/. Never install.

Lineage: presets/quiet-sage/desktop/build.py and presets/plum-afterglow/desktop/build.py.
design.json 'palette' is the single source of truth; every Mint-Y hex is role-mapped, every referenced
GTK PNG becomes a flat state SVG, and the toolkit/shell override blocks are appended last.
Outputs only '<preset>/desktop/GNU-Darwin Aqua/' plus build-report.json. Icons and cursors belong to another worker.
"""
from pathlib import Path
import json,re,shutil,hashlib,xml.etree.ElementTree as ET
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
D=json.loads((ROOT/'design.json').read_text());T=dict(D['palette']);G=D['geometry'];TY=D['typography'];NAME=D['name'];OUT=HERE/NAME;BASE=HERE/'base'
def rgb(h):return tuple(int(h[i:i+2],16) for i in (1,3,5))
def mix(a,b,t):return '#%02x%02x%02x'%tuple(round(x+(y-x)*t) for x,y in zip(rgb(a),rgb(b)))
def shade(h,k):return mix(h,'#ffffff',k-1) if k>1 else mix(h,'#000000',1-k)
def alpha(h,a):return 'rgba(%d,%d,%d,%s)'%(*rgb(h),a)
# Derived tokens (documented in build-report.json): hover/active shades, silver material. 'disabled' comes from design.json; popups are opaque (materials.text_surfaces).
T.update({'selection_hover':T['secondary'],'selection_active':shade(T['selection'],0.85),'selection_pale':D['rainbow'][0],
'accent_hover':shade(T['accent'],1.12),'accent_active':shade(T['accent'],0.85),'secondary_hover':shade(T['secondary'],1.12),'secondary_active':shade(T['secondary'],0.85),
'muted_hover':shade(T['muted'],1.12),'muted_active':shade(T['muted'],0.85),'error_active':shade(T['error'],0.85),'warning_active':shade(T['warning'],0.85),'secondary_dark':shade(T['secondary'],0.6),
'highlight':mix(T['surface'],T['foreground'],0.08),'highlight_rgba':alpha(T['foreground'],0.08),'highlight_rgba_strong':alpha(T['foreground'],0.12),
'popup':T['elevated'],'popup_top':mix(T['surface'],T['foreground'],0.06),'shadow':'rgba(0,0,0,0.28)',
'app_radius':str(G['app_radius']),'widget_radius':str(G['widget_radius']),'ui_family':TY['ui_family'],'ui_size':str(TY['ui_size_pt'])})
for role in ('error','warning','success'):
 T[role+'_hover']=shade(T[role],0.9);T[role+'_active']=shade(T[role],0.8)
T['shadow']='rgba(0,0,0,0.20)'
if OUT.exists():shutil.rmtree(OUT)
shutil.copytree(BASE,OUT)
# Role map: every Mint-Y-Dark hex found in the snapshot's css/svg/xml/rc files, by role. Source snapshot remains untouched.
ROLES={
'desktop':['080808','050506','0f0f0f','0f0f10','111113','141416','161616','161619','171717','1a1a1a','1b1b1b','1b1b1e','1c1c1c','1d1d21','18181b','161a26','0f1116','1b1c21'],
'background':['202023','222226','212121','222222','242429','25252a','26262a','27272b','29292e','2a2a2f','2b2b2b','202020','252a35'],
'surface':['2e2e33','303036','2c2c31','2c2c30','333339','333338','343439','383838','353535','303030','373737','2f2f2f','383c4a','323644','353945','2b2f3b','3c4049','353537'],
'elevated':['3c3c44','44444c','38383e','494951','3f3f46','404040','3e3e3e','3a3a41','46464e','474747','4a4a4a','4c4c50','3b3c3e','434343'],
'border':['55555f','555559','5c5c5c','5c616c','5e5e69','4d4f52','525252','5b5b5b'],
'control_border':['616166','6a6a77','797987'],
'disabled':['666666','676767'],
'muted':['7f7f7f','808080','838383','909090','949498','9d9d9d','a0a0a0','a1a1a1','a8a8a8','a7a7ab','aeaeae','b6b6b6','bababa','bebebe','c3c3c3','c4c7cc','c7c7c7','c8c8c8','d4d4d4'],
'foreground':['ffffff','e1e1e1','dbdbdb','dadada','d3d3d3','d7d7d7','d9d9d9','e2e2e2','eeeeee','f4f4f4','f8f8f8','fdfdfd'],
'selection':['35a854','6db442','30974c','30984c'],
'selection_hover':['3bbb5e','88c663','4ac66b','71d28b','5acb78','3ab85c'],
'selection_active':['298141','2f954a','568f34','1d5a2d','236e37'],
'selection_pale':['9ad4aa','a4d8b2','98deab','86cb98','aedcbb','c2e5cc'],
'selection_foreground':['d7eedd','e1f2e5','ebf6ee'],
'error':['f04a50','fc4138','c01c28','d61f2d','ec1b22','e12e3b','f4797e','ff4d4d','ff6666','ee878f','f70505','f75a61','ff7a80','ef2929','ff0b00'],
'error_active':['aa3936','a53531','d8354a'],
'warning':['f27835','fbeaa0','f57900'],
'warning_active':['a45a34','9f562f'],
'secondary':['5294e2','7eafe9','55c1ec','001aff'],
'secondary_dark':['102b68'],
'success':['73d216']}
M={'#'+c:T[role] for role,colors in ROLES.items() for c in colors}
def recolor(s):
 s=re.sub(r'#[0-9a-fA-F]{6}\b',lambda m:M.get(m[0].lower(),m[0]),s)
 def rgba(m):
  c='#'+''.join(f'{int(m[i]):02x}' for i in (1,2,3));v=M.get(c)
  return 'rgba('+','.join(str(x) for x in rgb(v))+','+m[4]+')' if v else m[0]
 return re.sub(r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)',rgba,s)
def tok(s):
 for k,v in T.items():s=s.replace('@'+k+'@',v)
 assert not re.search(r'@[a-z_]+@',s),re.search(r'@[a-z_]+@',s)[0]
 return s
for f in OUT.rglob('*'):
 if f.is_file() and f.suffix in ['.css','.svg','.xml','.rc']:f.write_text(recolor(f.read_text()).replace('version="< ', 'version="&lt; '))
# All toolkit bitmap references become native SVG state assets; no copied green PNG controls.
def svg_asset(name):
 n=name.lower();disabled='insensitive' in n;active=('checked' in n and 'unchecked' not in n) or 'mixed' in n or 'active' in n
 fill=T['elevated'] if disabled or not active else T['selection'];ink=T['muted'] if disabled else T['selection_foreground'];edge=T['control_border']
 if 'switch' in n:
  w,h=52,24;body=f'<rect x="1" y="2" width="50" height="20" rx="10" fill="{fill}" stroke="{edge}"/><circle cx="{40 if active else 12}" cy="12" r="8" fill="{T["foreground"] if not active else ink}"/>'
 elif 'checkbox' in n or 'radio' in n:
  w=h=16
  body=f'<rect x="1" y="1" width="14" height="14" rx="{7 if "radio" in n else 3}" fill="{fill}" stroke="{edge}"/>'
  if active:
   if 'mixed' in n:body+=f'<path d="M4 8h8" stroke="{ink}" stroke-width="2"/>'
   elif 'radio' in n:body+=f'<circle cx="8" cy="8" r="3" fill="{ink}"/>'
   else:body+=f'<path d="M4 8l3 3 5-6" fill="none" stroke="{ink}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
 else:
  w=h=12;body=f'<rect x=".5" y=".5" width="11" height="11" rx="2" fill="none" stroke="{edge}"/>'
 return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w*(2 if "@2" in n else 1)}" height="{h*(2 if "@2" in n else 1)}" viewBox="0 0 {w} {h}">{body}</svg>'
replaced=set()
for kit in ['gtk-3.0','gtk-4.0']:
 for f in (OUT/kit).glob('*.css'):
  s=f.read_text()
  def asset(m):
   path=m[0];dest=path[:-4]+'.svg';n=Path(path).name
   (f.parent/dest).parent.mkdir(parents=True,exist_ok=True);(f.parent/dest).write_text(svg_asset(n));replaced.add(kit+'/'+dest)
   return dest
  s=re.sub(r'assets/[a-zA-Z0-9_@.-]+\.png',asset,s)
  f.write_text(s)
 for f in (OUT/kit).rglob('*.png'):f.unlink()
# Shared GTK3/GTK4 state contract. Selection = accessible blue with white ink; links = secondary blue.
shared=tok("""
/* GNU-Darwin Aqua state contract: light silver surfaces, dark text, blue selection with white ink, opaque popups. */
@define-color theme_bg_color @background@;
@define-color theme_fg_color @foreground@;
@define-color theme_base_color @surface@;
@define-color theme_text_color @foreground@;
@define-color theme_selected_bg_color @selection@;
@define-color theme_selected_fg_color @selection_foreground@;
@define-color insensitive_fg_color @disabled@;
@define-color borders @control_border@;
@define-color link_color @accent@;
@define-color visited_link_color @secondary@;
@define-color success_color @success@;
@define-color warning_color @warning@;
@define-color error_color @error@;
@define-color accent_color @accent@;
@define-color wm_bg @surface@;
@define-color wm_bg_unfocused @background@;
@define-color wm_border @border@;
@define-color wm_border_unfocused @border@;
@define-color wm_highlight @highlight@;
@define-color wm_title @foreground@;
@define-color wm_title_unfocused @muted@;
@define-color wm_icon_bg @selection_foreground@;
@define-color wm_icon_unfocused_bg @muted@;
@define-color wm_icon_hover_bg @selection_foreground@;
@define-color wm_icon_active_bg @selection_foreground@;
@define-color wm_icon_close_bg @selection_foreground@;
@define-color wm_button_hover_bg @elevated@;
@define-color wm_button_active_bg @control_border@;
VteTerminal, vte-terminal { padding:12px; }
label.link, link, button.link, *:link, *:link:hover, *:link:active { color:@accent@; }
*:visited { color:@secondary@; }
window, dialog, .background { background-color:@background@; color:@foreground@; }
.view, textview, textview text, treeview { background-color:@surface@; color:@foreground@; }
*:selected, *:selected label, *:selected image, .view:selected, textview text selection, selection { background-color:@selection@; color:@selection_foreground@; }
/* Selection ink: base Mint-Y tree/icon/list rules outrank *:selected and paint foreground on the selection fill; restate selection_foreground at matching specificity. */
treeview.view:selected, treeview.view:selected:focus, treeview.view:selected:hover, iconview:selected, iconview:selected:focus, .view:selected, .view:selected:focus, label:selected, flowbox flowboxchild:selected, list row:selected, list row:selected label, .nemo-window .sidebar .view.cell:selected, .nemo-window .sidebar row.cell:selected, .nemo-window .sidebar .view.cell:selected:focus { color:@selection_foreground@; }
headerbar, .titlebar, toolbar { background-color:@surface@; background-image:none; color:@foreground@; box-shadow:inset 0 1px @highlight_rgba@; border-color:@border@; }
headerbar { min-height:34px; padding:3px 8px; border-bottom:1px solid @border@; }
headerbar:backdrop, .titlebar:backdrop { background-color:@background@; color:@muted@; box-shadow:none; }
button { background-color:@elevated@; background-image:none; color:@foreground@; border:1px solid @control_border@; border-radius:@app_radius@px; box-shadow:inset 0 1px @highlight_rgba@; text-shadow:none; padding:5px 10px; }
button:hover { background-color:@border@; color:@foreground@; border-color:@control_border@; }
button:active, button:checked, button.suggested-action, button.default { background-color:@selection@; color:@selection_foreground@; border-color:@selection_active@; box-shadow:none; }
button:active label, button:checked label, button.suggested-action label { color:@selection_foreground@; }
button.suggested-action:hover { background-color:@selection_hover@; }
button.destructive-action { background-color:@error@; color:@selection_foreground@; border-color:@error_active@; }
button:disabled, button:disabled label { color:@disabled@; background-color:@surface@; border-color:@border@; box-shadow:none; }
button:focus, entry:focus, spinbutton:focus, searchentry:focus { border-color:@focus@; }
entry, searchentry, spinbutton { background-color:@surface@; background-image:none; color:@foreground@; border:1px solid @control_border@; border-radius:@app_radius@px; box-shadow:none; padding:6px 8px; }
entry selection, entry:focus selection { background-color:@selection@; color:@selection_foreground@; }
/* Warning/error entries: base paints WHITE on warning_active/error_active. Dark ink on warning_active is 6.1:1; error_active reaches no ink, so error entries lift to error (5.5:1). */
entry.warning, entry.warning:focus, entry.warning image, headerbar entry.warning, .primary-toolbar entry.warning, headerbar entry.warning:focus, .primary-toolbar entry.warning:focus { color:@selection_foreground@; border-color:@warning_active@; }
entry.error, entry.error:focus, headerbar entry.error, .primary-toolbar entry.error, headerbar entry.error:focus, .primary-toolbar entry.error:focus { background-color:@error@; color:@selection_foreground@; border-color:@error_active@; }
entry.error image { color:@selection_foreground@; }
entry.warning selection, entry.warning selection:focus, entry.error selection, entry.error selection:focus, headerbar entry.warning selection, headerbar entry.error selection, .primary-toolbar entry.warning selection, .primary-toolbar entry.error selection { background-color:@selection_foreground@; color:@foreground@; }
.sidebar, .sidebar .view, .sidebar row, placessidebar { background-color:@surface@; color:@foreground@; }
row:selected, .sidebar row:selected, row:selected label, .sidebar row:selected label { background-color:@selection@; color:@selection_foreground@; }
menubar { background-color:@surface@; color:@foreground@; box-shadow:none; }
menubar > menuitem:hover { background-color:@selection@; color:@selection_foreground@; box-shadow:none; }
menu, .menu, .context-menu, popover, popover.background, popover contents, tooltip, tooltip.background, tooltip.csd, tooltip * { background-color:@popup@; background-image:none; color:@foreground@; border-color:@border@; border-radius:@widget_radius@px; }
tooltip, tooltip.background, tooltip.csd { border:1px solid @border@; border-radius:@app_radius@px; padding:4px 8px; box-shadow:inset 0 1px @highlight_rgba@; }
menuitem { background-color:transparent; }
menuitem:hover, popover row:hover, popover row:selected, .context-menu menuitem:hover { background-color:@selection@; color:@selection_foreground@; }
menuitem:hover label, menuitem:hover image, menuitem:hover arrow, menuitem:hover accelerator { color:@selection_foreground@; }
menuitem:disabled, menuitem:disabled label { color:@disabled@; }
popover label, popover image, popover row label, popover button label { color:@foreground@; }
popover modelbutton.flat, popover.background modelbutton.flat { background-color:transparent; color:@foreground@; background-image:none; box-shadow:none; border-radius:@app_radius@px; }
popover modelbutton.flat label, popover modelbutton.flat image, popover.background modelbutton.flat label { color:@foreground@; }
popover modelbutton.flat:hover, popover modelbutton.flat:checked { background-color:@selection@; color:@selection_foreground@; }
popover modelbutton.flat:hover label, popover modelbutton.flat:checked label, popover modelbutton.flat:hover image { color:@selection_foreground@; }
popover modelbutton.flat:disabled, popover modelbutton.flat:disabled label { color:@disabled@; }
notebook > header { background-color:@surface@; border-color:@border@; }
notebook > header tab { background-color:transparent; color:@muted@; border-radius:@app_radius@px @app_radius@px 0 0; padding:7px 12px; }
notebook > header tab:checked { background-color:@elevated@; color:@foreground@; border-color:@accent@; box-shadow:inset 0 2px @accent@; }
notebook > header tab:hover { color:@foreground@; }
progressbar trough, scale trough, levelbar trough { background-color:@border@; border-color:@border@; }
progressbar progress, scale highlight, levelbar block.filled { background-color:@selection@; border-color:@selection@; }
levelbar block.low { background-color:@warning@; border-color:@warning@; }
levelbar block.high { background-color:@success@; border-color:@success@; }
scale slider { background-color:@foreground@; background-image:none; border:1px solid @control_border@; box-shadow:none; }
scale slider:hover { background-color:@selection_pale@; }
scrollbar { background-color:@background@; }
scrollbar slider { background-color:@control_border@; border-radius:@app_radius@px; }
scrollbar slider:hover { background-color:@muted@; }
check, radio { background-image:none; background-color:@elevated@; color:@selection_foreground@; border:1px solid @control_border@; }
check:checked, radio:checked, check:indeterminate, radio:indeterminate { background-color:@selection@; border-color:@selection_active@; color:@selection_foreground@; }
switch { background-color:@elevated@; border:1px solid @control_border@; color:@foreground@; }
switch:checked { background-color:@selection@; border-color:@selection_active@; color:@selection_foreground@; }
switch slider { background-color:@foreground@; border:1px solid @control_border@; box-shadow:none; }
switch:checked slider { background-color:@selection_foreground@; border-color:@selection_active@; }
infobar.info, infobar.question, infobar.info > revealer > box, infobar.question > revealer > box { background-color:@elevated@; color:@foreground@; border-color:@border@; }
infobar.warning, infobar.warning > revealer > box { background-color:@warning@; color:@selection_foreground@; }
infobar.error, infobar.error > revealer > box { background-color:@error@; color:@selection_foreground@; }
infobar.warning > revealer > box label, infobar.error > revealer > box label { color:@selection_foreground@; }
infobar.info *:link, infobar.question *:link { color:@accent@; }
infobar.warning *:link, infobar.error *:link { color:@selection_foreground@; }
.osd, .osd.background, .osd.popover, .osd toolbar { background-color:@popup@; color:@foreground@; border-color:@border@; border-radius:@widget_radius@px; box-shadow:inset 0 1px @highlight_rgba@; }
frame > border, .frame { border-color:@border@; }
separator { background-color:@border@; }
calendar { background-color:@surface@; color:@foreground@; border-color:@border@; }
calendar:selected { background-color:@selection@; color:@selection_foreground@; border-radius:@app_radius@px; }
calendar.header { background-color:@elevated@; }
spinbutton button { border-radius:0; box-shadow:none; }
""")
# Three coloured window discs: minimize = warning, maximize = success, close = error; glyph ink = selection_foreground.
DISCS={'minimize':'warning','maximize':'success','close':'error'}
def gtk3_disc(c):return f'-gtk-gradient(radial, center center, 0, center center, 0.5, to({c}), to(transparent))'
def gtk4_disc(c):return f'radial-gradient(circle closest-side, {c} 0%, {c} 94%, transparent 100%)'
def discs(disc):
 sel=lambda cls,st='':', '.join(f'{p} button.titlebutton{cls}{st}' for p in ('headerbar','.titlebar'))
 css=f'{sel("")} {{ min-width:16px; min-height:16px; padding:2px; margin:0 2px; border:0; border-radius:100%; box-shadow:none; color:{T["selection_foreground"]}; -gtk-icon-shadow:none; background-color:transparent; }}\n'
 for cls,role in DISCS.items():
  c='.'+cls
  css+=f'{sel(c)}, {sel(c,":hover:backdrop")} {{ background-image:{disc(T[role])}; color:{T["selection_foreground"]}; }}\n'
  css+=f'{sel(c,":hover")} {{ background-image:{disc(T[role+"_hover"])}; color:{T["selection_foreground"]}; }}\n'
  css+=f'{sel(c,":active")}, {sel(c,":active:backdrop")} {{ background-image:{disc(T[role+"_active"])}; color:{T["selection_foreground"]}; }}\n'
  css+=f'{sel(c,":backdrop")} {{ background-image:{disc(T["border"])}; color:{T["muted"]}; }}\n'
 return css
locks=tok("""
/* Cinnamon screensaver / lock (cinnamon-screensaver is GTK3): .csstage declares theme support; #screensaver covers the stage id. */
.csstage, #screensaver { color:@foreground@; }
.csstage .unlockbox { background-color:@surface@; color:@foreground@; border:1px solid @border@; border-radius:@widget_radius@px; padding:24px; text-shadow:none; box-shadow:inset 0 1px @highlight_rgba@; }
.csstage .clock { background-color:@surface@; background-image:none; color:@foreground@; text-shadow:none; border-radius:@widget_radius@px; padding:16px 24px; }
.csstage .clock label { color:@foreground@; text-shadow:none; opacity:1; }
.csstage .toppanel, .csstage .audiopanel, .csstage .infopanel { background-color:@surface@; background-image:none; color:@foreground@; border-color:@border@; border-radius:@widget_radius@px; box-shadow:inset 0 1px @highlight_rgba@; }
.csstage .notificationwidget, .csstage .powerwidget, .csstage .trackname, .csstage .albumartist { color:@foreground@; text-shadow:none; }
.csstage .auth-message { color:@error@; }
.csstage .caps-message { color:@warning@; }
.csstage .framedimage { border:3px solid @accent@; border-radius:@app_radius@px; }
.csstage .passwordentry { background-color:@background@; background-image:none; color:@foreground@; border:1px solid @control_border@; border-radius:@app_radius@px; caret-color:@accent@; }
.csstage .passwordentry:focus { border-color:@focus@; }
.csstage .transparentbutton, .csstage .osk-button { background-color:@elevated@; background-image:none; color:@foreground@; border:1px solid @control_border@; border-radius:@app_radius@px; }
.csstage .transparentbutton image, .csstage .osk-button image { color:@foreground@; }
.csstage .transparentbutton:hover, .csstage .osk-button:hover { background-color:@selection@; color:@selection_foreground@; }
.csstage .transparentbutton:hover image, .csstage .osk-button:hover image { color:@selection_foreground@; }
.csstage .transparentbutton:disabled image, .csstage .osk-button:disabled image { color:@disabled@; }
.csstage .volumeslider { color:@accent@; background-color:@background@; }
.csstage .osk-popover { background-color:@surface@; color:@foreground@; }
""")
gtk3_only=tok("""
*:focus { outline-color:@focus@; outline-style:solid; outline-width:1px; outline-offset:-3px; }
.csd.popup decoration { border-radius:@widget_radius@px; box-shadow:0 3px 12px @shadow@, 0 0 0 1px @border@; }
tooltip.csd decoration { border-radius:@app_radius@px; }
decoration { border-radius:@app_radius@px @app_radius@px 0 0; box-shadow:0 4px 16px @shadow@, 0 0 0 1px @border@; }
decoration:backdrop { box-shadow:0 3px 10px rgba(0,0,0,0.18), 0 0 0 1px @border@; }
""")+discs(gtk3_disc)
gtk4_only=tok("""
*:focus-visible { outline-color:@focus@; outline-style:solid; outline-width:1px; outline-offset:-3px; }
window.csd { border-radius:@app_radius@px @app_radius@px 0 0; box-shadow:0 4px 16px @shadow@, 0 0 0 1px @border@; }
window.csd:backdrop { box-shadow:0 3px 10px rgba(0,0,0,0.18), 0 0 0 1px @border@; }
popover > arrow, popover > contents { background-color:@popup@; color:@foreground@; border-color:@border@; }
popover > contents { border-radius:@widget_radius@px; box-shadow:0 3px 12px @shadow@, inset 0 1px @highlight_rgba@; }
columnview.view:selected, columnview.view:selected:focus, columnview.view > child:selected, gridview > child:selected, listview > row:selected, listview > row:selected label { color:@selection_foreground@; }
spinbutton text.warning, spinbutton text.warning:focus-within, spinbutton text.warning > image, entry.warning:focus-within, entry.warning > image { color:@selection_foreground@; }
spinbutton text.error, spinbutton text.error:focus-within, entry.error:focus-within { background-color:@error@; color:@selection_foreground@; }
spinbutton text.error > image, entry.error > image { color:@selection_foreground@; }
entry.warning > selection, spinbutton text.warning > selection, entry.error > selection, spinbutton text.error > selection { background-color:@selection_foreground@; color:@foreground@; }
""")+discs(gtk4_disc)
aqua=tok("""
/* Original Aqua light materials; content areas are opaque and untextured. */
@define-color theme_base_color @elevated@;
@define-color link_color @secondary@;
@define-color wm_title @foreground@;
@define-color wm_title_unfocused @muted@;
window.background, dialog.background { background-image:repeating-linear-gradient(to bottom,@background@ 0px,@background@ 1px,@surface@ 1px,@surface@ 2px); }
.view, textview, textview text, treeview.view, entry, searchentry { background-image:none; background-color:@elevated@; }
headerbar, .titlebar, toolbar { background-image:linear-gradient(to bottom,@elevated@,@surface@); color:@foreground@; }
headerbar .title, .titlebar .title, headerbar label.title, .titlebar label.title { color:@foreground@; text-shadow:none; font-weight:bold; }
headerbar:backdrop .title, .titlebar:backdrop .title { color:@muted@; }
headerbar { min-height:30px; }
label.link, link, button.link, *:link, *:link:hover, *:link:active, infobar.info *:link, infobar.question *:link { color:@secondary@; }
button { background-image:linear-gradient(to bottom,@elevated@,@surface@); border-radius:12px; }
button:active, button:checked, button.suggested-action, button.default { background-color:@selection@; background-image:linear-gradient(to bottom,@accent@ 0px,@selection@ 7px,@selection@ 100%); color:@selection_foreground@; }
button.suggested-action:hover, button.default:hover { background-image:none; background-color:@secondary@; color:@selection_foreground@; }
button:disabled, button:disabled label { opacity:1; color:@disabled@; background-image:none; }
*:disabled { opacity:1; }
button:focus, entry:focus, spinbutton:focus, searchentry:focus { outline:2px solid @focus@; outline-offset:1px; }
button.destructive-action { background-image:none; }
headerbar button.titlebutton, .titlebar button.titlebutton { border-radius:100%; }
scale slider, switch slider { background-color:@elevated@; }
/* Override inherited low-opacity dark-theme text at its effective specificity. */
treeview.view header button, treeview.view header button label, treeview.view header button:hover, treeview.view header button:active, treeview.view header button:active:hover { color:@foreground@; background-color:@surface@; background-image:none; }
scale value, scale marks, scale value.top, scale value.bottom { color:@muted@; opacity:1; }
notebook > header > tabs > tab, notebook > header > tabs > tab label, notebook > header > tabs > arrow { color:@muted@; opacity:1; }
notebook > header > tabs > tab:checked, notebook > header > tabs > tab:checked label, notebook > header > tabs > tab:hover:not(:checked), notebook > header > tabs > tab:hover:not(:checked) label { color:@foreground@; opacity:1; }
notebook > header > tabs > tab:disabled, notebook > header > tabs > tab:disabled label, treeview.view header button:disabled, treeview.view header button:disabled label { color:@disabled@; opacity:1; }

""")
for kit,extra in [('gtk-3.0',gtk3_only+locks),('gtk-4.0',gtk4_only)]:
 p=OUT/kit/'gtk.css';p.write_text(p.read_text()+shared+extra+aqua+discs(gtk3_disc if kit=='gtk-3.0' else gtk4_disc))
 (OUT/kit/'gtk-dark.css').write_text('@import url("gtk.css");\n')
# GTK2 uses native Murrine rendering, without pixmap engines or inherited PNGs.
shutil.rmtree(OUT/'gtk-2.0');(OUT/'gtk-2.0').mkdir()
roles={'bg_color':'background','base_color':'surface','fg_color':'foreground','text_color':'foreground','selected_bg_color':'selection','selected_fg_color':'selection_foreground','tooltip_bg_color':'surface','tooltip_fg_color':'foreground','link_color':'accent'}
gtk2='gtk-color-scheme = "'+r'\n'.join(k+':'+T[v] for k,v in roles.items())+'"\n'
gtk2+=tok("""
gtk-icon-sizes = "gtk-button=16,16"
style "gnu-darwin-aqua" {
 xthickness=2
 ythickness=2
 bg[NORMAL]=@bg_color
 bg[PRELIGHT]="@elevated@"
 bg[SELECTED]=@selected_bg_color
 bg[ACTIVE]="@elevated@"
 bg[INSENSITIVE]=@bg_color
 fg[NORMAL]=@fg_color
 fg[PRELIGHT]=@fg_color
 fg[SELECTED]=@selected_fg_color
 fg[ACTIVE]=@fg_color
 fg[INSENSITIVE]="@disabled@"
 text[NORMAL]=@text_color
 text[SELECTED]=@selected_fg_color
 text[INSENSITIVE]="@disabled@"
 base[NORMAL]=@base_color
 base[SELECTED]=@selected_bg_color
 base[INSENSITIVE]=@bg_color
 engine "murrine" {
  roundness=@app_radius@
  contrast=1.0
  glazestyle=0
  glowstyle=0
  reliefstyle=0
  focus_color="@focus@"
 }
}
class "*" style "gnu-darwin-aqua"
""")
(OUT/'gtk-2.0/gtkrc').write_text(gtk2)
# Cinnamon shell: brushed-silver panel slab with a milky inner highlight, clock island, frosted translucent popups/OSD.
shell=tok("""
/* GNU-Darwin Aqua shell: brushed silver top bar and compact bottom dock, blue selection and focus. */
stage { color:@foreground@; font-family:@ui_family@; font-size:@ui_size@pt; }
#panel { background-color:@top_background@; background-gradient-direction:none; border:0; border-image:none; padding:0 8px; margin:0; box-shadow:inset 0 1px @highlight_rgba@; color:@top_foreground@; font-weight:normal; }
#panel.panel-top { border-radius:0 0 @widget_radius@px @widget_radius@px; }
#panel.panel-bottom { border-radius:@widget_radius@px @widget_radius@px 0 0; }
#panelLeft, #panelCenter, #panelRight { background-color:transparent; border:0; margin:0; padding:0; spacing:4px; }
.applet-box { background-color:transparent; color:@top_foreground@; border:0; border-radius:@app_radius@px; margin:0 1px; padding:0 7px; }
.applet-box:hover { background-color:@elevated@; color:@foreground@; }
.applet-box:checked { background-color:@elevated@; color:@foreground@; box-shadow:inset 0 -2px @accent@; }
#panelCenter .applet-box { background-color:@island_background@; color:@island_foreground@; border-radius:@widget_radius@px; padding:0 12px; box-shadow:inset 0 1px @highlight_rgba_strong@; }
#panelCenter .applet-box:hover, #panelCenter .applet-box:checked { background-color:@elevated@; }
.applet-label, .applet-icon { color:@top_foreground@; font-size:@ui_size@pt; }
.workspace-graph { padding:7px 4px; margin:0; spacing:4px; }
.workspace-graph .workspace, .workspace-button { background-color:@surface@; color:@muted@; border:1px solid @control_border@; border-radius:@app_radius@px; }
.workspace-graph .workspace:active, .workspace-button:outlined { background-color:@selection@; border-color:@selection_active@; color:@selection_foreground@; }
.grouped-window-list-box { margin:0; padding:0; border:0; background-color:transparent; spacing:4px; }
.grouped-window-list-item-box { margin:0 1px; padding:0 7px; border:0; background-color:transparent; border-radius:@app_radius@px; color:@top_foreground@; }
.grouped-window-list-item-box:hover { background-color:@elevated@; }
.grouped-window-list-item-box:active, .grouped-window-list-item-box:focus { background-color:@surface@; box-shadow:inset 0 -2px @accent@; }
.grouped-window-list-item-box .grouped-window-list-item-label { color:@top_foreground@; }
.popup-menu-content, .appmenu-background .popup-menu-content, .modal-dialog { border-image:none; background-color:@popup@; background-gradient-direction:vertical; background-gradient-start:@popup_top@; background-gradient-end:@popup@; color:@foreground@; border:1px solid @border@; border-radius:@widget_radius@px; box-shadow:0 3px 12px @shadow@; }
.popup-menu-item { color:@foreground@; border-radius:@app_radius@px; }
.popup-menu-item:active, .popup-menu-item:focus { background-color:@selection@; color:@selection_foreground@; }
.popup-menu-item:active StLabel, .popup-menu-item:focus StLabel, .popup-menu-item:active StIcon, .popup-menu-item:focus StIcon { color:@selection_foreground@; }
.popup-menu-item:insensitive { color:@disabled@; }
.popup-separator-menu-item { background-color:@border@; }
.appmenu-sidebar { background-color:@surface@; color:@foreground@; border-color:@border@; }
.appmenu-background .appmenu-sidebar StLabel, .appmenu-background .appmenu-sidebar StIcon,
.appmenu-sidebar-button, .appmenu-sidebar-button StLabel, .appmenu-sidebar-button StIcon { color:@foreground@; }
.appmenu-sidebar-button:hover { background-color:@elevated@; color:@foreground@; }
.appmenu-system-button, .appmenu-sidebar .appmenu-system-button { background-color:@elevated@; color:@foreground@; border-color:@control_border@; border-radius:@app_radius@px; }
.appmenu-system-button:hover, .appmenu-sidebar .appmenu-system-button:hover { background-color:@selection@; color:@selection_foreground@; }
.appmenu-system-button StIcon, .appmenu-background .appmenu-sidebar .appmenu-system-button StIcon { color:@foreground@; }
.appmenu-system-button:hover StIcon, .appmenu-sidebar .appmenu-system-button:hover StIcon { color:@selection_foreground@; }
.appmenu-system-button-shutdown, .appmenu-sidebar .appmenu-system-button-shutdown { background-color:@selection@; color:@selection_foreground@; }
/* Explicit child foreground overrides inherited sidebar icon rules on blue shutdown fill. */
.appmenu-background .appmenu-sidebar .appmenu-system-button-shutdown StIcon, .appmenu-background .appmenu-sidebar .appmenu-system-button-shutdown:hover StIcon, .appmenu-sidebar .appmenu-system-button-shutdown StIcon, .appmenu-system-button-shutdown StIcon, .appmenu-system-button-shutdown:hover StIcon, .appmenu-background .appmenu-sidebar .appmenu-system-button:hover StIcon { color:@selection_foreground@; }
/* Safe light shutdown surface: runtime xsi icon retained dark ink despite symbolic CSS. Preserve its exact shape and use the established opaque surface in both states. */
.appmenu-background .appmenu-sidebar .appmenu-system-button-shutdown, .appmenu-background .appmenu-sidebar .appmenu-system-button-shutdown:hover, .appmenu-sidebar .appmenu-system-button-shutdown, .appmenu-sidebar .appmenu-system-button-shutdown:hover { background-color:@elevated@; color:@foreground@; }
.appmenu-background .appmenu-sidebar .appmenu-system-button-shutdown StIcon, .appmenu-background .appmenu-sidebar .appmenu-system-button-shutdown:hover StIcon { color:@foreground@; }

.appmenu-category-button, .appmenu-application-button, .appmenu-category-button StLabel, .appmenu-category-button StIcon, .appmenu-application-button StLabel { color:@foreground@; border-radius:@app_radius@px; }
.appmenu-application-button .appmenu-application-button-description { color:@muted@; }
.appmenu-category-button-selected, .appmenu-category-button:hover, .appmenu-application-button-selected, .appmenu-application-button:hover { background-color:@selection@; color:@selection_foreground@; }
.appmenu-category-button-selected StLabel, .appmenu-category-button-selected StIcon, .appmenu-application-button-selected StLabel, .appmenu-application-button-selected StIcon,
.appmenu-application-button-selected .appmenu-application-button-description, .appmenu-application-button:hover .appmenu-application-button-description { color:@selection_foreground@; }
.appmenu-category-button-greyed, .appmenu-category-button-greyed StLabel { color:@disabled@; }
.menu-favorites-box, .menu-favorites-button { background-color:transparent; color:@foreground@; border-radius:@app_radius@px; }
.menu-favorites-button:hover, .menu-category-button-selected, .menu-application-button-selected { background-color:@selection@; color:@selection_foreground@; }
.menu-category-button-selected StLabel, .menu-application-button-selected StLabel, .menu-application-button-selected .menu-application-button-label { color:@selection_foreground@; }
.menu-search-entry, StEntry, #menu-search-entry { background-color:@background@; color:@foreground@; border:1px solid @control_border@; border-radius:@app_radius@px; box-shadow:none; caret-color:@accent@; selection-background-color:@selection@; selected-color:@selection_foreground@; }
StEntry:focus, #menu-search-entry:focus, .menu-search-entry:focus { border-color:@focus@; }
#notification, #notification.multi-line-notification, .notification, .notification-with-image, .osd-window, .media-keys-osd, .workspace-switch-osd, .info-osd, .workspace-osd, .sound-player-overlay { background-color:@popup@; background-gradient-direction:vertical; background-gradient-start:@popup_top@; background-gradient-end:@popup@; color:@foreground@; border:1px solid @border@; border-radius:@widget_radius@px; border-image:none; box-shadow:0 3px 12px @shadow@; }
#notification StLabel, .notification StLabel, #notification .notification-body, #notification .notification-title { color:@foreground@; }
#notification .notification-button, #notification .notification-icon-button { background-color:@elevated@; color:@foreground@; border:1px solid @control_border@; border-radius:@app_radius@px; }
#notification .notification-button:hover, #notification .notification-icon-button:hover { background-color:@selection@; color:@selection_foreground@; }
#Tooltip { border-image:none; background-color:@popup@; color:@foreground@; border:1px solid @border@; border-radius:@app_radius@px; border-image:none; padding:5px 9px; box-shadow:0 2px 8px @shadow@; }
.switcher-list, .workspace-thumbnails-background, .window-caption, .expo-workspaces-name-entry, .expo-background { border-image:none; background-color:@popup@; color:@foreground@; border-color:@border@; border-radius:@widget_radius@px; }
.switcher-list .item-box:selected, .switcher-list .item-box:outlined, .thumbnail-box:selected, .thumbnail-box:outlined { background-color:@selection@; color:@selection_foreground@; border-color:@selection_active@; }
.window-caption:focus, .workspace-thumbnails .workspace-thumbnail:selected, .workspace-controls .workspace-thumbnail:selected { border-color:@focus@; }
.calendar-today, .calendar-day-base:hover, .calendar-day-selected { background-color:@selection@; color:@selection_foreground@; border-radius:@app_radius@px; }
.calendar-other-month-day, .calendar-nonwork-day { color:@muted@; }
.calendar-month-label, .datemenu-date-label, .calendar-change-month-back, .calendar-change-month-forward { color:@foreground@; }
.check-box StBin, .radiobutton StBin { background-color:@elevated@; border:1px solid @control_border@; border-radius:3px; }
.check-box:checked StBin, .radiobutton:checked StBin { background-color:@selection@; border-color:@selection_active@; }
.toggle-switch, .toggle-switch-us, .toggle-switch-intl { background-color:@elevated@; border:1px solid @control_border@; border-radius:12px; background-image:none; }
.toggle-switch:checked, .toggle-switch-us:checked, .toggle-switch-intl:checked { background-color:@selection@; border-color:@selection_active@; background-image:none; }
.slider, .popup-slider-menu-item, .slider-menu-item { -slider-height:4px; -slider-background-color:@border@; -slider-border-color:@border@; -slider-active-background-color:@selection@; -slider-active-border-color:@selection@; -slider-handle-radius:6px; -slider-handle-color:@foreground@; -slider-handle-border-color:@control_border@; }
.run-dialog, .run-dialog-entry, .keyboard-layout-dialog { border-image:none; background-color:@popup@; color:@foreground@; border:1px solid @border@; border-radius:@widget_radius@px; }
.run-dialog-entry { background-color:@background@; border-color:@control_border@; }
.run-dialog-entry:focus { border-color:@focus@; }
.modal-dialog-button, .modal-dialog-button-box .modal-dialog-button { background-color:@elevated@; color:@foreground@; border:1px solid @control_border@; border-radius:@app_radius@px; }
.modal-dialog-button:hover, .modal-dialog-button:focus, .modal-dialog-button:default { background-color:@selection@; color:@selection_foreground@; border-color:@selection_active@; }
.end-session-dialog-button-list .modal-dialog-button:focus { border-color:@focus@; }
.tile-preview, .tile-hud, .snap-osd { background-color:@highlight_rgba@; border:1px solid @selection@; border-radius:@app_radius@px; }
.tile-preview.snap, .tile-hud.snap { background-color:@highlight_rgba@; border-color:@secondary@; }
.magnifier-zoom-region, .magnifier-zoom-region.full-screen { border:2px solid @accent@; }
.osd-window StLabel, .media-keys-osd StLabel, .info-osd StLabel { color:@foreground@; }
.level, .level-bar { -barlevel-height:6px; -barlevel-background-color:@border@; -barlevel-border-color:@border@; -barlevel-active-background-color:@selection@; -barlevel-active-border-color:@selection@; -barlevel-overdrive-color:@warning@; -barlevel-overdrive-border-color:@warning@; }
.notification-applet-padding, .notification-applet-icon-not-urgent { color:@top_foreground@; }
.notification-applet-icon-urgent { color:@accent@; }
.system-status-icon, .system-status-icon:hover { color:@top_foreground@; }
""")
shell+=tok("""
/* Continuous top bar; only the lower CENTER zone paints a compact dock. Native grouped actors request the full panel height, so dock vertical padding, margin and borders must remain zero. */
#panel.panel-top { background-color:@top_background@; background-gradient-direction:vertical; background-gradient-start:@elevated@; background-gradient-end:@surface@; border-radius:0; border-bottom:1px solid @border@; box-shadow:none; }
#panel.panel-bottom { background-color:transparent; background-image:none; background-gradient-direction:none; border:0; border-radius:0; box-shadow:none; padding:0; }
#panel.panel-bottom #panelLeft, #panel.panel-bottom #panelRight { background-color:transparent; border:0; box-shadow:none; }
#panel.panel-bottom #panelCenter { background-color:@surface@; background-gradient-direction:vertical; background-gradient-start:@elevated@; background-gradient-end:@surface@; border:0; border-radius:10px 10px 0 0; padding:0 8px; margin:0; box-shadow:inset 0 1px @border@; }
#panelCenter .applet-box, #panelCenter .applet-box:hover, #panelCenter .applet-box:checked { background-color:transparent; box-shadow:none; padding:0; }
#panel.panel-bottom .grouped-window-list-item-box { padding:0 5px; margin:0 2px; }
#panel.panel-bottom .grouped-window-list-item-box:focus, #panel.panel-bottom .grouped-window-list-item-box:active { background-color:transparent; box-shadow:inset 0 -3px @selection@; }
#panel.panel-top .applet-box { border-radius:0; }
#panel.panel-top .applet-box:hover, #panel.panel-top .applet-box:checked { background-color:@selection@; color:@selection_foreground@; }
#panel.panel-top .applet-box:hover .applet-label, #panel.panel-top .applet-box:hover .applet-icon, #panel.panel-top .applet-box:checked .applet-label, #panel.panel-top .applet-box:checked .applet-icon { color:@selection_foreground@; }
""")
p=OUT/'cinnamon/cinnamon.css';p.write_text(p.read_text()+shell)
# Native window frame: silver fascia, three coloured discs, corner radius from geometry.
p=OUT/'metacity-1/metacity-theme-3.xml';tree=ET.parse(p);r=tree.getroot()
CONST={'C_title_focused':'foreground','C_title_unfocused':'muted','C_wm_bg':'surface','C_wm_bg_unfocused':'background','C_wm_border':'border','C_wm_border_unfocused':'border','C_wm_highlight':'highlight',
'C_button_close_bg_focused':'accent','C_button_close_bg_hover':'accent_hover','C_button_close_bg_active':'accent_active','C_icon_close_bg':'selection_foreground',
'C_button_bg_hover':'elevated','C_button_bg_active':'control_border','C_icon_bg_focused':'foreground','C_icon_bg_unfocused':'muted','C_icon_bg_hover':'foreground','C_icon_bg_active':'foreground'}
for c in r.findall('constant'):c.set('value',T[CONST[c.get('name')]])
# Native titlebar silver gloss, matching GTK chrome.
for grad in r.findall("draw_ops[@name='titlebar_fill_focused']/gradient"):
 grad.findall('color')[0].set('value',T['elevated']);grad.findall('color')[1].set('value',T['surface'])

MDISC={'minimize':'warning','maximize':'success','unmaximize':'success','close':'error'}
disc_ops=0
for ops in r.findall('draw_ops'):
 name=ops.get('name','');kind=name.split('_')[0]
 if kind not in MDISC:continue
 role=MDISC[kind]
 col=T['border'] if 'unfocused' in name else T[role+'_active'] if 'pressed' in name else T[role+'_hover'] if 'prelight' in name else T[role]
 ink=T['muted'] if 'unfocused' in name else T['selection_foreground']
 imgs=ops.findall('image');bg=[i for i in imgs if i.get('filename')=='button-bg.svg'];icons=[i for i in imgs if i.get('filename')!='button-bg.svg']
 if bg:bg[0].set('colorize',col)
 else:
  icon=icons[0];new=ET.Element('image',dict(icon.attrib,filename='button-bg.svg',colorize=col));new.tail=icon.tail;ops.insert(list(ops).index(icon),new)
 for i in icons:i.set('colorize',ink)
 disc_ops+=1
for g in r.findall('frame_geometry'):
 for k in ['rounded_top_left','rounded_top_right']:
  if g.get(k) not in (None,'false'):g.set(k,T['app_radius'])
r.find('info/name').text=NAME;r.find('info/description').text=D['direction'];r.find('info/copyright').text=(r.find('info/copyright').text or '')+' Recoloured for GNU-Darwin Aqua by the Desktop Theme Studio agent team, 2026.';tree.write(p,encoding='utf-8',xml_declaration=True)
(OUT/'metacity-1/button-bg.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18"><circle cx="9" cy="9" r="7.5" fill="#ffffff"/></svg>\n')
(OUT/'index.theme').write_text(f'[Desktop Entry]\nType=X-GNOME-Metatheme\nName={NAME}\nComment={D["direction"]}\nEncoding=UTF-8\n\n[X-GNOME-Metatheme]\nGtkTheme={NAME}\nMetacityTheme={NAME}\nIconTheme={NAME} icons\nCursorTheme={NAME} cursors\nButtonLayout={G["button_layout"]}\n')
# Theme-chooser thumbnails: flat previews drawn from the palette instead of the inherited green Mint-Y bitmaps.
try:
 from PIL import Image,ImageDraw
 def thumb(path,kind):
  im=Image.new('RGB',(120,80),rgb(T['desktop']));d=ImageDraw.Draw(im)
  if kind=='cinnamon':
   d.rounded_rectangle((0,0,119,17),radius=6,fill=rgb(T['top_background']));d.line((1,1,118,1),fill=rgb(mix(T['top_background'],T['foreground'],.08)))
   d.rounded_rectangle((34,64,86,79),radius=5,fill=rgb(T['surface']),outline=rgb(T['border']));d.rounded_rectangle((14,26,104,60),radius=6,fill=rgb(T['surface']),outline=rgb(T['border']))
   d.rounded_rectangle((20,32,98,40),radius=3,fill=rgb(T['selection']))
  else:
   d.rounded_rectangle((10,10,110,70),radius=6,fill=rgb(T['background']),outline=rgb(T['border']));d.rectangle((11,11,109,24),fill=rgb(T['surface']))
   for i,role in enumerate(['error','warning','success']):d.ellipse((17+i*10,13,25+i*10,21),fill=rgb(T[role]))
   d.rounded_rectangle((20,34,64,46),radius=4,fill=rgb(T['selection']));d.rounded_rectangle((20,52,100,62),radius=4,fill=rgb(T['surface']),outline=rgb(T['control_border']))
  im.save(path)
 thumb(OUT/'cinnamon/thumbnail.png','cinnamon');thumb(OUT/'gtk-3.0/thumbnail.png','gtk');thumb(OUT/'metacity-1/thumbnail.png','metacity');thumbs='regenerated from palette (PIL)'
except ImportError:
 for n in ['cinnamon/thumbnail.png','gtk-3.0/thumbnail.png','metacity-1/thumbnail.png']:(OUT/n).unlink(missing_ok=True)
 thumbs='removed (PIL unavailable)'
(OUT/'SOURCE.md').write_text(f"""# {NAME} — desktop theme sources

* Base: packaged Mint-Y-Dark snapshot (mint-themes, GPL-3+; see COPYRIGHT) kept pristine in `../base/`.
* Recolouring: every Mint-Y hex/rgba role-mapped to `design.json` palette tokens by `../build.py`, adapted by the Desktop Theme Studio agent team on 2026-09-29.
* GTK3/GTK4: all referenced PNG control assets replaced with flat state SVGs; GTK2 uses native Murrine rules with no pixmaps.
* Window controls: three coloured discs (minimize = warning, maximize = success, close = error, white glyph ink) in GTK3, GTK4 and Metacity.
* Cinnamon shell, lock-screen (.csstage), window-frame and material (brushed silver) styling authored for this preset.
* Thumbnails: {thumbs}.
* Icons (`{NAME} icons`) and cursors (`{NAME} cursors`) are built by a separate worker.
""")
# Discard unused desktop-environment styles instead of shipping unrelated palette leftovers.
for n in ['libadwaita-1.5','libadwaita-1.7','openbox-3','xfwm4']:
 if (OUT/n).exists():shutil.rmtree(OUT/n)
missing=[];parses=0
for f in OUT.rglob('*'):
 if f.is_file() and f.suffix in ['.svg','.xml']:ET.parse(f);parses+=1
 if f.suffix=='.css':
  for ref in re.findall(r'url\(["\x27]?([^)"\x27]+)',f.read_text()):
   if ':' not in ref and not(f.parent/ref).exists():missing.append(str(f)+':'+ref)
counts={d.name:sum(1 for x in d.rglob('*') if x.is_file()) for d in sorted(OUT.iterdir()) if d.is_dir()}
report={'name':NAME,'theme':str(OUT),'base':str(BASE),'base_files':sum(1 for x in BASE.rglob('*') if x.is_file()),'design_sha256':hashlib.sha256((ROOT/'design.json').read_bytes()).hexdigest(),
'files_per_dir':counts,'css_lines':{k:len((OUT/k).read_text().splitlines()) for k in ['gtk-3.0/gtk.css','gtk-4.0/gtk.css','cinnamon/cinnamon.css','gtk-2.0/gtkrc']},
'svg_control_assets':len(replaced),'xml_svg_parsed':parses,'missing_assets':missing,'gtk_raster_assets':sum(1 for k in ['gtk-3.0','gtk-4.0'] for x in (OUT/k).rglob('*.png') if x.name!='thumbnail.png'),
'metacity_disc_draw_ops':disc_ops,'thumbnails':thumbs,
'role_map':{role:{'token':T[role],'mint_y_hexes':['#'+c for c in cs]} for role,cs in ROLES.items()},
'derived_tokens':{k:T[k] for k in ['success_hover','success_active','error_hover','warning_hover','selection_hover','selection_active','selection_pale','accent_hover','accent_active','secondary_hover','secondary_active','secondary_dark','muted_hover','muted_active','error_active','warning_active','disabled','highlight','highlight_rgba','popup','popup_top','shadow']},
'window_controls':{'minimize':T['warning'],'maximize':T['success'],'close':T['error'],'glyph_ink':T['selection_foreground'],'unfocused_disc':T['border'],'unfocused_ink':T['muted']},
'applied':False,
'limitations':['libadwaita-1.x, openbox-3 and xfwm4 directories dropped as in the Quiet Sage lineage; libadwaita apps keep their own palette.','Popup/OSD surfaces are opaque; transparent bottom panel actor requires private compositor verification.','Icons and cursors are built by desktop/build_assets.py; app-specific icon fallbacks retain source branding.','Lock/login, per-application GTK4 and interaction verification belong to the coordinator.']}
(HERE/'build-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('role_map',)},indent=2))
assert not missing
