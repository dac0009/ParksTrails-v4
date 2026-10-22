import json, numpy as np
D=json.load(open('build/geom.json'))
def f(v): return ('%.1f'%v).rstrip('0').rstrip('.')
def path(lines,close=False):
    out=[]
    for l in lines:
        if len(l)<2: continue
        out.append('M'+' L'.join(f(x)+' '+f(y) for x,y in l)+('Z' if close else ''))
    return ''.join(out)
O={'frame':[1270,802]}
O['outside']=path(D['outside'],True); O['water']=path(D['water'],True); O['roads']=path(D['roads'],True); O['boundary']=path(D['boundary'])
# names: keep strong clusters, fold faint antialias clusters into nearest strong colour visually (just draw them)
O['names']=[{'c':n['color'],'d':path(n['lines'])} for n in D['names']]
O['off']=path(D['net_official']); O['una']=path(D['net_unauth']); O['oth']=path(D['net_other'])
pal=['#008300','#2a78d6','#eda100','#e87ba4','#6250d6','#1baf7a','#1c5cab','#e34948']
items=[]
for k,v in D['loops'].items():
    pts=np.array([p for l in v['lines'] for p in l]); items.append((pts[:,0].mean(),k,v,pts))
items.sort(key=lambda t:t[0])
allpts={k:pts for _,k,_,pts in items}
loops=[]
for i,(cx,k,v,pts) in enumerate(items):
    others=np.vstack([p for kk,p in allpts.items() if kk!=k])
    d=np.min(np.linalg.norm(pts[:,None,:]-others[None,:,:],axis=2),axis=1)
    # prefer far from other loops, and toward top of the loop
    score=d - 0.15*(pts[:,1]-pts[:,1].min())
    b=pts[int(np.argmax(score))]
    loops.append({'id':'ABCDEFGH'[i],'src':k,'c':pal[i],'d':path(v['lines']),'badge':[round(float(b[0]),1),round(float(b[1]),1)],
                  'bbox':v['bbox']})
    print('ABCDEFGH'[i],k,pal[i],v['bbox'])
O['loops']=loops
O['band']=path([l for c in D['dec_comps'] for l in c['lines']])
O['need']=path([l for c in D['dec_comps'] for l in c['need']])
sp=D['spot']; rb=sp['red_bbox']
H=[l for l in loops if l['src']=='red'][0]; hb=H['bbox']
s=((hb[2]-hb[0])/(rb[2]-rb[0])+(hb[3]-hb[1])/(rb[3]-rb[1]))/2
cx4,cy4=(rb[0]+rb[2])/2,(rb[1]+rb[3])/2; cx1,cy1=(hb[0]+hb[2])/2,(hb[1]+hb[3])/2
O['spot']={'t':[round(cx1-cx4*s,3),round(cy1-cy4*s,3),round(s,5)],'road':path(sp['road'],True),'water':path(sp['water_poly'],True),
           'stream':path(sp['stream']),'grey':path(sp['grey']),'boundary':path(sp['boundary']),'red':path(sp['red'])}
print('spot transform',O['spot']['t'])
js='window.MAPDATA='+json.dumps(O,separators=(',',':'))+';'
open('../../docs/mapdata.js','w').write(js); print('bytes',len(js))

# ---- split the spotlight loop at its two side-trail junctions (Blue side vs Red side) ----
from shapely.geometry import LineString as _LS
from shapely.ops import substring as _sub
red=_LS(D['spot']['red'][0]); Lr=red.length
# junction positions along the loop, found where side trails meet it (see README)
J1=min(((red.project(__import__('shapely').geometry.Point(p))),p) for l in D['spot']['grey'] for p in (l[0],l[-1]) if red.distance(__import__('shapely').geometry.Point(p))<12 and 250<p[0]<350)[0]
J2=red.project(__import__('shapely').geometry.Point(833,668))
a,b_=sorted([J1,J2]); blue=_sub(red,a,b_); r1=_sub(red,b_,Lr); r2=_sub(red,0,a)
redarc=list(r1.coords)+list(r2.coords)[1:]
pth=lambda c:'M'+' L'.join('%.1f %.1f'%(x,y) for x,y in c)
O['spot']['blue']=pth(blue.coords); O['spot']['redarc']=pth(redarc)
O['spot']['junctions']=[[round(c,1) for c in red.interpolate(s).coords[0]] for s in (a,b_)]
O['spot']['bluemid']=[round(c,1) for c in blue.interpolate(0.5,normalized=True).coords[0]]
O['spot']['redmid']=[round(c,1) for c in _LS(redarc).interpolate(0.35,normalized=True).coords[0]]
ft=0.76*5280/Lr
print('Blue side %.0f ft, Red side %.0f ft (records: 1328 / 2706)'%(blue.length*ft,_LS(redarc).length*ft))
open('../../docs/mapdata.js','w').write('window.MAPDATA='+json.dumps(O,separators=(',',':'))+';')
