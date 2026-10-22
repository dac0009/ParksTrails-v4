import cv2, numpy as np, json
from skimage.morphology import skeletonize
from shapely.geometry import LineString, Polygon
import os; S=os.environ.get('MAPS_DIR','../../')
os.makedirs('build',exist_ok=True)
AF=json.load(open('build/affine.json'))
W,H=1270,802
def load(f,key=None):
    im=cv2.imread(S+f)
    if key: im=cv2.warpAffine(im,np.array(AF[key]),(W,H),flags=cv2.INTER_NEAREST,borderValue=(255,255,255))
    return im
m1=load('map1_trail_routes.png'); m2=load('map2_designation.png')
m3=load('map3_core_loops.png','map3_core_loops'); m5=load('map5_decision.png','map5_decision')
def rgb(im): return im[:,:,::-1].astype(int)
def lab(im): return cv2.cvtColor(im,cv2.COLOR_BGR2LAB).astype(np.float32)
def near(im,c,tol):
    L=lab(im); cl=cv2.cvtColor(np.uint8([[c[::-1]]]),cv2.COLOR_BGR2LAB)[0,0].astype(np.float32)
    return np.linalg.norm(L-cl,axis=2)<tol
def clean(mask,minpx):
    n,l,st,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    keep=np.zeros(n,bool); keep[1:]=st[1:,4]>=minpx
    return keep[l]

# ---------- skeleton -> polylines ----------
NB=[(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]
def trace(mask,minlen=4,simp=0.7):
    sk=skeletonize(mask>0)
    pts=set(zip(*np.nonzero(sk)))
    def nbrs(p): return [(p[0]+a,p[1]+b) for a,b in NB if (p[0]+a,p[1]+b) in pts]
    deg={p:len(nbrs(p)) for p in pts}
    nodes={p for p in pts if deg[p]!=2}
    seen=set(); lines=[]
    def walk(a,b):
        path=[a,b]; prev,cur=a,b
        while cur not in nodes:
            nx=[q for q in nbrs(cur) if q!=prev and frozenset((cur,q)) not in seen]
            if not nx: break
            seen.add(frozenset((cur,nx[0]))); prev,cur=cur,nx[0]; path.append(cur)
        return path
    for n in nodes:
        for q in nbrs(n):
            e=frozenset((n,q))
            if e in seen: continue
            seen.add(e); lines.append(walk(n,q))
    # pure cycles
    for p in pts:
        for q in nbrs(p):
            e=frozenset((p,q))
            if e in seen: continue
            seen.add(e); lines.append(walk(p,q))
    out=[]
    for l in lines:
        if len(l)<2: continue
        ls=LineString([(c,r) for r,c in l])
        if ls.length<minlen: continue
        ls=ls.simplify(simp)
        out.append([[round(x,1),round(y,1)] for x,y in ls.coords])
    return out
def poly(mask,minarea,simp=1.0):
    cs,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
    out=[]
    for c in cs:
        if cv2.contourArea(c)<minarea: continue
        if len(c)<4: continue
        p=Polygon(c[:,0,:]).buffer(0)
        geoms=[p] if p.geom_type=='Polygon' else list(getattr(p,'geoms',[]))
        for g in geoms:
            if g.area<minarea: continue
            g=g.simplify(simp)
            out.append([[round(x,1),round(y,1)] for x,y in g.exterior.coords])
    return out
D={}
R1=rgb(m1)
# ---------- water ----------
water=near(m1,(171,225,246),14)
water=cv2.morphologyEx(water.astype(np.uint8),cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
water=clean(water,150)
D['water']=poly(water,150,0.8)
# ---------- boundary & park ----------
blk=(R1.sum(2)<90)
blk=clean(blk,200)
D['boundary']=trace(cv2.dilate(blk.astype(np.uint8),np.ones((3,3),np.uint8)),20,0.8)
n,l=cv2.connectedComponents((~cv2.dilate(blk.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)).astype(np.uint8),connectivity=4)
park=(l==l[380,600]); park=cv2.morphologyEx(park.astype(np.uint8),cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
D['park']=poly(park,5000,1.0)
outside=near(m1,(239,239,229),7)|(R1.min(2)>=247)|near(m1,(226,226,214),8)
outside=cv2.morphologyEx(outside.astype(np.uint8),cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
outside=clean(outside,3000)
D['outside']=poly(outside,3000,1.0)
print('OUT',len(D['outside']),'WATER',len(poly(water,150,0.8)))
# ---------- roads ----------
white=(R1.min(2)>=247)
white=clean(white,60)
D['roads']=poly(cv2.dilate(white.astype(np.uint8),np.ones((2,2),np.uint8)),30,0.6)
# ---------- trail network by designation (map2) ----------
hsv2=cv2.cvtColor(m2,cv2.COLOR_BGR2HSV); Sa=hsv2[:,:,1]
H2,S2,V2=[hsv2[:,:,i].astype(int) for i in range(3)]
stream2=(H2>=85)&(H2<=108)&(S2<150)
off=(H2>=112)&(H2<=135)&(S2>70)&(V2<235)&~stream2           # blue/indigo
una=(H2>=8)&(H2<=26)&(S2>70)&(V2<235)                        # orange/tan
teal=(H2>=88)&(H2<=104)&(S2>=150)&(V2<200)
def netmask(mk):
    d=cv2.dilate(mk.astype(np.uint8),np.ones((3,3),np.uint8))
    return clean(d,25).astype(np.uint8)
for k,mk in [('official',off),('unauth',una),('other',teal)]:
    nm=netmask(mk)
    D['net_'+k]=trace(nm,5,0.6)
    pass
    print('NET',k,'%.2f mi'%(sum(LineString(x).length for x in D['net_'+k])*0.76/285.2))
# ---------- loops (map3) ----------
LC={'green':(53,167,42),'tealgreen':(54,137,100),'blue':(24,120,168),'maroon':(139,43,70),'navy':(60,69,131),
    'purple':(158,34,125),'olive':(162,156,44),'red':(174,36,47)}
hsv3=cv2.cvtColor(m3,cv2.COLOR_BGR2HSV)
sat3=(hsv3[:,:,1]>90)&(hsv3[:,:,2]<235)
L3=lab(m3)
names=list(LC); C=np.array([cv2.cvtColor(np.uint8([[c[::-1]]]),cv2.COLOR_BGR2LAB)[0,0] for c in LC.values()],np.float32)
dist=np.linalg.norm(L3[:,:,None,:]-C[None,None],axis=3)
am=dist.argmin(2); dm=dist.min(2)
loops={}
for i,nm in enumerate(names):
    mk=sat3&(am==i)&(dm<(24 if nm=='olive' else 38))
    ring=cv2.dilate(mk.astype(np.uint8),np.ones((3,3),np.uint8))
    ring=clean(ring,40).astype(np.uint8); mk=mk&(ring>0)
    ring=cv2.morphologyEx(ring,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)))
    cs,hier=cv2.findContours(ring,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_NONE)
    # holes: hierarchy parent != -1
    holes=[c for c,h in zip(cs,hier[0]) if h[3]!=-1 and cv2.contourArea(c)>30] if hier is not None else []
    holes.sort(key=cv2.contourArea,reverse=True)
    lines=trace(ring,6,0.6)
    ys,xs=np.nonzero(mk)
    if len(xs)==0: print(nm,"EMPTY"); continue
    loops[nm]={'px':int(mk.sum()),'bbox':[int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())],'lines':lines,'nholes':len(holes),
               'skel_len':round(sum(LineString(l).length for l in lines),1)}
    print(nm,loops[nm]['px'],loops[nm]['bbox'],'holes',len(holes),'skel_len',loops[nm]['skel_len'],'pieces',len(lines))
D['loops']=loops
# ---------- decision (map5) ----------
hsv5=cv2.cvtColor(m5,cv2.COLOR_BGR2HSV)
redor=near(m5,(240,72,0),30)&(hsv5[:,:,1]>150)
pinkband=near(m5,(220,125,195),22)
pinkline=(near(m5,(240,72,120),26)|near(m5,(240,72,168),26)|near(m5,(240,72,96),20))&(hsv5[:,:,1]>100)
Hh,Ss,Vv=[hsv5[:,:,i].astype(int) for i in range(3)]
pinkhue=(Hh>=138)&(Hh<=172)&(Ss>45)&(Vv>140)
band=cv2.morphologyEx(pinkhue.astype(np.uint8),cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)))
band=clean(band,80)
bandz=cv2.dilate(band.astype(np.uint8),np.ones((9,9),np.uint8)).astype(bool)
D['dec_band']=trace(cv2.dilate(band.astype(np.uint8),np.ones((3,3),np.uint8)),8,0.8)
D['dec_need']=trace(cv2.dilate((clean(redor,6)&bandz).astype(np.uint8),np.ones((2,2),np.uint8)),4,0.6)
D['dec_other_unauth']=trace(cv2.dilate((clean(redor,6)&~bandz).astype(np.uint8),np.ones((2,2),np.uint8)),4,0.6)
for k in D:
    if k!='loops': print(k,len(D[k]),'paths')
# decision band components with lengths
bd=cv2.dilate(band.astype(np.uint8),np.ones((3,3),np.uint8)); bd=cv2.morphologyEx(bd,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7)))
n,l,st,_=cv2.connectedComponentsWithStats(bd,8)
comps=[]
for i in range(1,n):
    if st[i,4]<60: continue
    ls=trace((l==i).astype(np.uint8),6,0.6)
    need=trace(((l==i)|False).astype(np.uint8)*0,1)  # placeholder
    nm_=cv2.dilate((l==i).astype(np.uint8),np.ones((5,5),np.uint8)).astype(bool)&clean(redor,6)
    comps.append({'bbox':[int(st[i,0]),int(st[i,1]),int(st[i,0]+st[i,2]),int(st[i,1]+st[i,3])],'lines':ls,
        'len_px':round(sum(LineString(x).length for x in ls),1),'need':trace(cv2.dilate(nm_.astype(np.uint8),np.ones((2,2),np.uint8)),4,0.6)})
    comps[-1]['need_px']=round(sum(LineString(x).length for x in comps[-1]['need']),1)
    print('band',comps[-1]['bbox'],'len_px',comps[-1]['len_px'],'mi %.2f'%(comps[-1]['len_px']*0.002643),'need_px',comps[-1]['need_px'])
D['dec_comps']=comps
json.dump(D,open('build/geom.json','w'))
