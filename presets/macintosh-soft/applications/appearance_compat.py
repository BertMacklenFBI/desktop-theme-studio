"""Appearance-only transforms for recognized current configuration layouts.

No I/O, command execution or implicit baseline rebasing occurs here.
"""
import re

PROMPT_SKELETON = "# Plum Afterglow: compact directory prompt, no command hooks or alias changes.\nif [[ $- == *i* && ${TERM:-dumb} != dumb ]]; then\n    PS1='\\[\\e[RGBm\\]\\w\\[\\e[0m\\] \\[\\e[RGBm\\]❯\\[\\e[0m\\] '\nfi\n"
RGB = re.compile(r'38;2;(\d{1,3});(\d{1,3});(\d{1,3})')

def prompt_colors(text, first, second):
    matches=list(RGB.finditer(text))
    if len(matches)!=2 or any(int(c)>255 for m in matches for c in m.groups()) or RGB.sub('RGB',text)!=PROMPT_SKELETON:
        raise RuntimeError('Unknown prompt layout; refusing nonappearance shell changes')
    replacements=iter([first,second])
    return RGB.sub(lambda _:next(replacements),text)

def kitty_include(text, filename, label):
    if not re.fullmatch(r'[a-z0-9-]+\.conf',filename):raise ValueError('Unexpected candidate Kitty filename')
    marker='# Desktop Theme Studio candidate appearance: '+label
    block='\n'+marker+'\ninclude '+filename+'\n'
    if marker in text:
        if text.count(marker)!=1 or not text.endswith(block):raise RuntimeError('Candidate Kitty block changed or moved')
        return text
    # Remove only exact selections of this target, preserving every other include,
    # comment, command and preference. Last include wins in Kitty.
    lines=text.splitlines(keepends=True)
    matches=[i for i,line in enumerate(lines) if line.rstrip('\r\n')=='include '+filename]
    if len(matches)>1:raise RuntimeError('Duplicate candidate Kitty includes; explicit review required')
    if matches:lines.pop(matches[0])
    return ''.join(lines)+block

def css_append(text, css, label):
    marker='/* Desktop Theme Studio candidate appearance: '+label+' */'
    block='\n'+marker+'\n'+css.rstrip()+'\n'
    if marker in text:
        if text.count(marker)!=1 or not text.endswith(block):raise RuntimeError('Candidate CSS block changed or moved')
        return text
    return text+block

def library_link(text, filename):
    tag='<link rel="stylesheet" href="'+filename+'">'
    if text.count('</head>')!=1:raise RuntimeError('Unknown Music Library head layout')
    if text.count(tag)>1:raise RuntimeError('Duplicate Music Library appearance link')
    # Existing links and image/action attributes are retained. Move only this
    # appearance link last, after the current generic collection stylesheet.
    if tag in text:text=text.replace(tag+'\n','').replace(tag,'')
    return text.replace('</head>',tag+'\n</head>')

def prompt_hook_presence(raw, suffix):
    """Recognize one unchanged hook at any line boundary without moving it."""
    count=raw.count(suffix)
    if count>1:raise RuntimeError('Duplicate prompt hooks; refusing shell rewrite')
    if count==1:
        start=raw.index(suffix)
        if start and raw[start-1:start]!=b'\n' and not suffix.startswith(b'\n'):
            raise RuntimeError('Unknown embedded prompt hook')
        return True
    if b'.config/plum-afterglow/prompt.bash' in raw:
        raise RuntimeError('Unknown prompt hook layout; refusing shell rewrite')
    return False

def replace_literal_rgba(text, surface_rgb):
    # Older native wrappers used one literal color. Current wrappers consume
    # appearance.json dynamically and require no Python/source transformation.
    if 'return Gdk.RGBA(channels[0],channels[1],channels[2],1)' in text:
        if text.count('return Gdk.RGBA(channels[0],channels[1],channels[2],1)')!=1:
            raise RuntimeError('Unknown dynamic native background layout')
        return text
    pattern=r'Gdk\.RGBA\(\d{1,3}/255,\d{1,3}/255,\d{1,3}/255,1\)'
    if len(re.findall(pattern,text))!=1:raise RuntimeError('Unknown native Music Library background layout')
    return re.sub(pattern,'Gdk.RGBA('+','.join(str(v)+'/255' for v in surface_rgb)+',1)',text)

def selection_text(palette):
    def lum(color):
        values=[int(color[i:i+2],16)/255 for i in (1,3,5)]
        return sum((v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4)*w for v,w in zip(values,[.2126,.7152,.0722]))
    def contrast(color):
        a,b=sorted([lum(color),lum(palette['accent'])])
        return (b+.05)/(a+.05)
    return max([palette['base'],palette['text']],key=contrast)

def variables(palette):
    keys=['base','surface0','surface1','border','text','muted','accent','secondary','battery','tile','accent-hover','indeterminate','selection-text']
    lines=['// THEME_VARIABLES_START — maintained by scripts/theme-sync.py.']
    for key in keys:lines.append('$'+key+': '+(selection_text(palette) if key=='selection-text' else palette[key])+';')
    return '\n'.join(lines+['// THEME_VARIABLES_END'])
