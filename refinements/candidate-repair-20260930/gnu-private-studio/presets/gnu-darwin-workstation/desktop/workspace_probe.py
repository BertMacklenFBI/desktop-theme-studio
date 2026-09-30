"""Private-session native workspace geometry, color, pixel and click checks. No host input/settings."""
from pathlib import Path
import ctypes, ctypes.util, hashlib, json, math, os, time

STATES = ('', 'shaded', 'hover', 'shaded hover', 'outlined', 'outlined shaded', 'outlined hover', 'outlined shaded hover')

def measurement_js(expression):
    return """(() => {const a=EXPRESSION;
 const rgba=c=>[c.red,c.green,c.blue,c.alpha];
 const bounds=b=>({p:b.get_transformed_position(),s:b.get_transformed_size(),visible:b.visible,mapped:b.mapped});
 const radii=n=>[0,1,2,3].map(c=>n.get_border_radius(c));
 return {screen:[global.screen_width,global.screen_height],panelHeight:a._panelHeight,panel:bounds(a.panel.actor),strip:bounds(a.actor),stripRadii:radii(a.actor.get_theme_node()),
 vertical:a.actor.get_vertical(),active:global.workspace_manager.get_active_workspace_index(),buttons:a.buttons.map(b=>{
 const actor=b.actor,label=actor.get_children()[0],old=actor.get_style_pseudo_class();
 try {return {bounds:bounds(actor),label:bounds(label),labelInk:rgba(label.get_theme_node().get_foreground_color()),text:label.get_text(),reactive:actor.reactive,tooltip:!!b._tooltip,workspaceName:b.workspace_name,
 current:old,ink:rgba(actor.get_theme_node().get_foreground_color()),fill:rgba(actor.get_theme_node().get_background_color()),
 states:STATES.map(state=>{actor.set_style_pseudo_class(state);const n=actor.get_theme_node();return {state:state,radii:radii(n),ink:rgba(n.get_foreground_color()),fill:rgba(n.get_background_color())};})};}
 finally {actor.set_style_pseudo_class(old);}})};})()""".replace('EXPRESSION',expression).replace('STATES',json.dumps(STATES))

def validate(measured, expected_colors, shape, orientation):
    strip,panel=measured['strip'],measured['panel']
    if measured['screen'] != [2880,1800]:raise RuntimeError('Native review display is not2880x1800: '+str(measured['screen']))
    if not strip['visible'] or not strip['mapped'] or min(strip['s'])<=0:raise RuntimeError('Workspace strip is not mapped')
    def inside(child,parent):
        return all(child['p'][axis]>=parent['p'][axis]-1 and child['p'][axis]+child['s'][axis]<=parent['p'][axis]+parent['s'][axis]+1 for axis in (0,1))
    if not inside(strip,panel):raise RuntimeError('Workspace strip overflows its native panel')
    if min(measured['stripRadii']) < min(strip['s'])/2-1:raise RuntimeError('Workspace strip is not fully rounded')
    if len(measured['buttons'])!=4 or [b['text'] for b in measured['buttons']]!=['1','2','3','4']:raise RuntimeError('Native numbered workspaces are not1,2,3,4')
    axis=1 if orientation=='vertical' else 0;previous=None
    for button in measured['buttons']:
        box,label=button['bounds'],button['label'];w,h=box['s']
        if not box['visible'] or not box['mapped'] or min(w,h)<=0:raise RuntimeError('Workspace button is not mapped')
        if not button['reactive'] or not button['tooltip'] or not isinstance(button['workspaceName'],str):raise RuntimeError('Native button behavior/name tooltip missing')
        if not label['visible'] or not label['mapped'] or min(label['s'])<=0:raise RuntimeError('Workspace label is not mapped')
        if not inside(box,strip) or not inside(label,box):raise RuntimeError('Native button/label clipping or strip overflow')
        if shape=='circle' and (abs(w-h)>1 or abs(w-64)>1):raise RuntimeError('Workstation native circle is not64x64: '+str(box))
        if shape=='pill' and (abs(w-60)>1 or w<h+2):raise RuntimeError('Native pill is not60px wide and horizontal: '+str(box))
        if abs(box['s'][0 if orientation=='vertical' else 1]-measured['panelHeight'])>1:raise RuntimeError('Native cross-axis differs from applet panel height')
        if any(min(state['radii'])<min(w,h)/2-1 for state in button['states']):raise RuntimeError('A native state is not fully rounded')
        if previous is not None and abs(box['p'][axis]-previous-4)>1:raise RuntimeError('Native workspace gap differs from4px')
        previous=box['p'][axis]+box['s'][axis]
        for state in button['states']:
            reference=expected_colors[state['state'] or 'normal']
            if state['ink']!=reference['foreground'] or state['fill']!=reference['background']:raise RuntimeError('Native foreground/background state changed: '+str(state))
    return True

def pixels(path, measured):
    from PIL import Image
    image=Image.open(path).convert('RGB')
    if list(image.size)!=measured['screen']:raise RuntimeError('Workspace capture dimensions differ from native display')
    active=measured['buttons'][measured['active']];box=active['bounds'];x,y=box['p'];w,h=box['s'];fill=active['fill'][:3];ink=active['labelInk'][:3]
    def rgb(px,py):return list(image.getpixel((int(round(px)),int(round(py)))))
    def near(a,b):return max(abs(x-y) for x,y in zip(a,b))<=20
    corners=[rgb(x+1,y+1),rgb(x+w-2,y+1),rgb(x+1,y+h-2),rgb(x+w-2,y+h-2)]
    if any(near(c,fill) for c in corners):raise RuntimeError('Selected workspace corners still paint rectangular fill')
    center=rgb(x+w/2,y+5)
    if not near(center,fill):raise RuntimeError('Selected workspace rounded fill is not painted')
    label=active['label'];lx,ly=label['p'];lw,lh=label['s'];crop=image.crop((math.floor(lx),math.floor(ly),math.ceil(lx+lw),math.ceil(ly+lh)))
    # Native FreeType LCD glyph edges blend independently per RGB channel; a thin '1'
    # may contain no solid foreground pixel. Require foreground-directed contrast
    # inside the actual native label bounds, alongside the exact ThemeNode ink checks.
    direction=[a-b for a,b in zip(ink,fill)];denominator=sum(v*v for v in direction)
    if denominator<100:raise RuntimeError('Native number ink has no contrast against its fill')
    def painted_ink(pixel):
        delta=[a-b for a,b in zip(pixel,fill)]
        projection=sum(a*b for a,b in zip(delta,direction))/denominator
        in_blend_range=all(min(a,b)-32<=c<=max(a,b)+32 for a,b,c in zip(ink,fill,pixel))
        return max(abs(v) for v in delta)>20 and projection>.12 and in_blend_range
    ink_pixels=sum(1 for p in crop.getdata() if painted_ink(p))
    if ink_pixels<3:raise RuntimeError('Native workspace number ink is not painted')
    return {'passed':True,'corners':corners,'center_fill':center,'expected_fill':fill,'label_ink_pixels':ink_pixels,'capture_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}

def click_all(evaluate, expression):
    private=os.environ.get('DISPLAY','');hosts=[os.environ.get(k,'') for k in ('JESTA_HOST_DISPLAY','CRYOSTAT_HOST_DISPLAY','GNU_DARWIN_WORKSTATION_HOST_DISPLAY')]
    if not private or any(h and private.split('.')[0]==h.split('.')[0] for h in hosts):raise RuntimeError('Refusing non-private workspace input')
    X=ctypes.CDLL(ctypes.util.find_library('X11'));T=ctypes.CDLL(ctypes.util.find_library('Xtst'))
    X.XOpenDisplay.argtypes=[ctypes.c_char_p];X.XOpenDisplay.restype=ctypes.c_void_p
    X.XFlush.argtypes=[ctypes.c_void_p];X.XCloseDisplay.argtypes=[ctypes.c_void_p]
    T.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
    T.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
    d=X.XOpenDisplay(private.encode());assert d
    original=evaluate('global.workspace_manager.get_active_workspace_index()');visits=[]
    try:
        for selected in (1,2,3,0):
            deadline=time.monotonic()+4;previous=None;stable=0
            while time.monotonic()<deadline:
                b=evaluate('(() => {const b='+expression+'.buttons['+str(selected)+'].actor;return {p:b.get_transformed_position(),s:b.get_transformed_size()};})()')
                stable=stable+1 if b==previous else 0;previous=b
                if stable>=3:break
                time.sleep(.15)
            if stable<3:raise RuntimeError('Workspace click target did not settle')
            px,py=b['p'];w,h=b['s'];T.XTestFakeMotionEvent(d,-1,int(px+w/2),int(py+h/2),0);X.XFlush(d);time.sleep(.1)
            T.XTestFakeButtonEvent(d,1,1,0);T.XTestFakeButtonEvent(d,1,0,0);X.XFlush(d)
            deadline=time.monotonic()+3
            while evaluate('global.workspace_manager.get_active_workspace_index()')!=selected:
                if time.monotonic()>deadline:raise RuntimeError('Private workspace click failed: '+str(selected+1))
                time.sleep(.1)
            visits.append(selected+1);time.sleep(.15)
        T.XTestFakeMotionEvent(d,-1,2870,1700,0);X.XFlush(d)
    finally:
        evaluate('global.workspace_manager.get_workspace_by_index('+str(original)+').activate(global.get_current_time()); true');X.XCloseDisplay(d)
    return visits

def run_probe(evaluate, screenshot, run, expected_colors, *, expression='imports.ui.appletManager.filterDefinitionsByUUID("workspace-switcher@cinnamon.org")[0].applet',shape='pill',orientation='horizontal',click=True):
    code=measurement_js(expression);deadline=time.monotonic()+12;last_error=None
    while time.monotonic()<deadline:
        try:
            measured=evaluate(code)
            if bool(measured['vertical']) != (orientation=='vertical'):raise RuntimeError('Workspace orientation not settled')
            validate(measured,expected_colors,shape,orientation);break
        except Exception as error:last_error=error;time.sleep(.3)
    else:raise RuntimeError('Native workspace geometry did not qualify: '+str(last_error))
    visits=click_all(evaluate,expression) if click else []
    time.sleep(1.3);measured=evaluate(code);validate(measured,expected_colors,shape,orientation)
    capture=Path(run)/('workspace-native-'+orientation+'.png');screenshot(capture);after=evaluate(code)
    if measured['strip']!=after['strip'] or [b['bounds'] for b in measured['buttons']]!=[b['bounds'] for b in after['buttons']]:raise RuntimeError('Native workspace moved during capture')
    result={'shape':shape,'orientation':orientation,'actual':measured,'capture':str(capture),'clicked_workspaces':visits,'current_workspace_restored':True}
    (Path(run)/('workspace-native-'+orientation+'-measurements.json')).write_text(json.dumps(result,indent=2)+'\n')
    result['pixels']=pixels(capture,measured)
    (Path(run)/('workspace-native-'+orientation+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    return result

EXPECTED_STATE_COLORS = {'normal': {'background': [179, 178, 176, 255], 'foreground': [16, 16, 16, 255]}, 'shaded': {'background': [179, 178, 176, 255], 'foreground': [16, 16, 16, 255]}, 'hover': {'background': [208, 208, 208, 255], 'foreground': [16, 16, 16, 255]}, 'shaded hover': {'background': [208, 208, 208, 255], 'foreground': [16, 16, 16, 255]}, 'outlined': {'background': [51, 54, 65, 255], 'foreground': [255, 255, 255, 255]}, 'outlined shaded': {'background': [51, 54, 65, 255], 'foreground': [255, 255, 255, 255]}, 'outlined hover': {'background': [51, 54, 65, 255], 'foreground': [255, 255, 255, 255]}, 'outlined shaded hover': {'background': [51, 54, 65, 255], 'foreground': [255, 255, 255, 255]}}
