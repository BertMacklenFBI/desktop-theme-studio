import json,subprocess
from pathlib import Path
p=Path('presets/gnu-darwin-aqua');d=json.loads((p/'design.json').read_text());c=d['palette']
a=['/usr/bin/convert','-size','1200x960','xc:#F8F9FA','-font','Liberation-Sans','-fill','#202328','-pointsize','32','-annotate','+40+55','GNU-Darwin Aqua','-pointsize','17','-annotate','+40+85','Panther / Tiger direction | sampled silver, pinstripes and glossy blue']
for i,(k,v) in enumerate(c.items()):
 x=40+(i%4)*290;y=115+(i//4)*125
 a+=['-fill',v,'-stroke','#A6ABB2','-draw',f'rectangle {x},{y} {x+255},{y+64}','-stroke','none','-fill','#202328','-pointsize','17','-annotate',f'+{x}+{y+88}',k,'-pointsize','15','-annotate',f'+{x}+{y+109}',v]
a+=['-fill','#535A64','-pointsize','17','-annotate','+40+915','34px top panel | 68px centered dock | left traffic lights | Liberation Sans / Mono',str(p/'identity/palette-sheet.png')];subprocess.run(a,check=True)
