"""Appearance assets derived only from the canonical Ocean-Silk palette."""
def kitty(t,colors):
    rows={'font_family':'Hack','font_size':'11.0','background_opacity':'1.0','background':t['terminal'],'foreground':t['terminal_foreground'],'cursor':colors[3],'cursor_text_color':t['terminal'],'selection_background':t['selection'],'selection_foreground':t['selection_foreground'],'active_tab_background':t['surface'],'active_tab_foreground':t['foreground'],'inactive_tab_background':t['terminal'],'inactive_tab_foreground':colors[8],'url_color':colors[4]}
    rows.update({f'color{i}':c for i,c in enumerate(colors)})
    return '# Ocean-Silk — appearance only\n'+'\n'.join(f'{k} {v}' for k,v in rows.items())+'\n'
def konsole(t,colors):
    value='[General]\nDescription=Ocean-Silk\nOpacity=1\n'
    for section,col in [('Background',t['terminal']),('Foreground',t['terminal_foreground'])]+[(f'Color{i%8}'+('Intense' if i>=8 else ''),c) for i,c in enumerate(colors)]:value+=f'\n[{section}]\nColor='+','.join(str(int(col[i:i+2],16)) for i in (1,3,5))+'\n'
    return value

def ptyxis(t,colors):
    value='[Palette]\nName=Ocean-Silk\n'
    for section in ['Light','Dark']:
        value+='\n['+section+']\nBackground='+t['terminal']+'\nForeground='+t['terminal_foreground']+'\nCursor='+colors[3]+'\n'+'\n'.join(f'Color{i}={c}' for i,c in enumerate(colors))+'\n'
    return value

def ptyxis_css(t):
    roles={'accent-bg':t['selection'],'accent-fg':t['selection_foreground'],'accent':t['focus'],'window-bg':t['surface'],'window-fg':t['foreground'],'view-bg':t['terminal'],'view-fg':t['terminal_foreground'],'headerbar-bg':t['surface'],'headerbar-fg':t['foreground'],'headerbar-backdrop':t['elevated'],'headerbar-border':t['border'],'sidebar-bg':t['elevated'],'sidebar-fg':t['foreground'],'sidebar-backdrop':t['elevated'],'card-bg':t['elevated'],'card-fg':t['foreground'],'dialog-bg':t['surface'],'dialog-fg':t['foreground'],'popover-bg':t['surface'],'popover-fg':t['foreground']}
    return '/* Ocean-Silk app-local chrome. */\n:root {\n'+''.join('  --'+key+'-color: '+value+';\n' for key,value in roles.items())+'}\n'

def kde(t):
    roles={}
    for section in ['View','Window','Button','Tooltip','Selection','Header']:
        selected=section=='Selection';fg=t['selection_foreground'] if selected else t['foreground'];bg=t['selection'] if selected else t['surface']
        roles['Colors:'+section]={'BackgroundNormal':bg,'BackgroundAlternate':bg if selected else t['elevated'],'ForegroundNormal':fg,'ForegroundInactive':fg if selected else t['muted'],'ForegroundActive':fg,'ForegroundLink':fg if selected else t['focus'],'ForegroundVisited':fg if selected else t['focus'],'ForegroundNegative':fg if selected else t['error'],'ForegroundNeutral':fg if selected else t['warning'],'ForegroundPositive':fg if selected else t['success'],'DecorationFocus':t['focus'],'DecorationHover':t['accent']}
    return roles

def kde_scheme(t):
    value='# Ocean-Silk interface only; document and painting colors unchanged.\n[General]\nName=Ocean-Silk\nColorScheme=Ocean-Silk\n\n[ColorEffects:Inactive]\nEnable=false\nChangeSelectionColor=false\n'
    for section,values in kde(t).items():
        value+='\n['+section+']\n'+''.join(key+'='+','.join(str(int(col[i:i+2],16)) for i in (1,3,5))+'\n' for key,col in values.items())
    return value

def sourceview(t):
    roles={'text':(t['foreground'],t['surface']),'selection':(t['selection_foreground'],t['selection']),'current-line':(None,t['elevated']),'line-numbers':(t['muted'],t['elevated']),'def:comment':(t['muted'],None),'def:keyword':(t['focus'],None),'def:string':(t['success'],None),'def:number':(t['warning'],None),'def:type':(t['focus'],None),'def:error':(t['error'],None)}
    text='<?xml version="1.0" encoding="UTF-8"?>\n<style-scheme id="ocean-silk-current" name="Ocean-Silk" version="1.0"><author>Desktop Theme Studio</author>\n'
    for name,(fg,bg) in roles.items():text+='<style name="'+name+'"'+(' foreground="'+fg+'"' if fg else '')+(' background="'+bg+'"' if bg else '')+'/>\n'
    return text+'</style-scheme>\n'

def library(t):
    return f'''/* Ocean-Silk: UI only. Existing DOM IDs, search and media handlers remain intact. */
html,body {{ background:{t['surface']} !important;color:{t['foreground']}; }}
#panel {{ background:{t['surface']};color:{t['foreground']}; }}
#panel .card,#panel .card-music {{ background:{t['surface']};color:{t['foreground']};border:0;border-radius:4px;padding:16px;box-shadow:none; }}
#panel .card-music h2 {{ color:{t['foreground']};font:600 16px Ubuntu,sans-serif;background:none;border:0;letter-spacing:0;margin-bottom:16px; }}
#panel .record-row {{ background:{t['elevated']};border:0;border-radius:4px;padding:12px;box-shadow:none;gap:12px; }}
#panel .record-cover {{ width:72px;height:72px;flex-basis:72px;border:0;border-radius:4px;padding:0; }}
#panel .np-title {{ color:{t['foreground']};font:600 18px Ubuntu,sans-serif; }}
#panel .np-artist,#panel #np-album,#panel .track-times,#panel .music-library summary span {{ color:{t['muted']}; }}
#panel .np-controls {{ background:none;border:0;padding:12px 0;box-shadow:none;gap:12px; }}
#panel .np-btn,#panel #np-playpause {{ background:{t['surface']};color:{t['foreground']};border:1px solid {t['control_border']};border-radius:4px;min-width:40px;min-height:36px;box-shadow:none; }}
#panel #np-playpause,#panel #np-playpause:hover {{ background:{t['selection']};color:{t['selection_foreground']}; }}
#panel .np-btn:hover {{ background:{t['elevated']}; }}
#panel .cava {{ background:none;border:0;border-radius:0;padding:0;height:24px; }}
#panel .cava-bar {{ background:{t['focus']} !important;border-radius:0; }}
#panel .music-library {{ border-top:1px solid {t['border']};padding-top:16px; }}
#panel .music-library summary {{ color:{t['foreground']};font-size:16px; }}
#panel .music-search {{ background:{t['surface']};color:{t['foreground']};border:1px solid {t['control_border']};border-radius:4px;min-height:40px;font-size:16px; }}
#panel .music-search::placeholder {{ color:{t['muted']}; }}
#panel .music-result {{ color:{t['foreground']};background:{t['surface']};border-bottom:1px solid {t['border']}; }}
#panel .music-result:hover {{ background:{t['elevated']}; }}
#panel .mr-title {{ color:{t['foreground']}; }}
#panel .mr-artist {{ color:{t['muted']}; }}
#panel #track-progress::-webkit-progress-bar {{ background:{t['elevated']}; }}
#panel #track-progress::-webkit-progress-value {{ background:{t['focus']}; }}
#panel #music-state {{ color:{t['focus']}; }}
#panel :focus-visible {{ outline:2px solid {t['focus']};outline-offset:2px; }}
'''

def gtile(t):
    return f'''/* Ocean-Silk gTile appearance; existing tiling behavior and geometry retained. */
.grid-panel {{ background-color:{t['surface']};border:1px solid {t['control_border']};border-radius:6px; }}
.grid-title,.grid-panel .settings-label,.grid-panel .tile-label {{ color:{t['foreground']}; }}
.table-element {{ background-color:{t['elevated']};border:1px solid {t['control_border']};border-radius:4px; }}
.table-element:hover,.table-element:activate {{ background-color:{t['selection']};color:{t['selection_foreground']}; }}
.table-element:hover .tile-label,.table-element:activate .tile-label {{ color:{t['selection_foreground']}; }}
.settings-button {{ background-color:{t['elevated']};color:{t['foreground']};border:1px solid {t['control_border']};border-radius:4px; }}
.settings-button:activate,.settings-button:hover,.settings-button:activate:hover {{ background-color:{t['selection']};color:{t['selection_foreground']};border:1px solid {t['focus']}; }}
.settings-button:hover .settings-label,.settings-button:activate .settings-label {{ color:{t['selection_foreground']}; }}
.grid-preview:activate {{ background-color:rgba({int(t['accent'][1:3],16)},{int(t['accent'][3:5],16)},{int(t['accent'][5:7],16)},0.3);border:2px solid {t['focus']}; }}
'''
