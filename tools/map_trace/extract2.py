import cv2, numpy as np, json
exec(open('extract.py').read().split("D={}")[0])   # reuse helpers
D=json.load(open('build/geom.json'))
# ---- map1 trail-name colors ----
hsv=cv2.cvtColor(m1,cv2.COLOR_BGR2HSV); Hh,Ss,Vv=[hsv[:,:,i].astype(int) for i in range(3)]
stream=(Hh>=85)&(Hh<=108)&(Ss<150)
water=near(m1,(171,225,246),20)
m=(Ss>75)&(Vv<230)&(Vv>40)&~stream&~water
m=m&clean(cv2.dilate(m.astype(np.uint8),np.ones((3,3),np.uint8)),25)
ys,xs=np.nonzero(m)
L=lab(m1)[m]; px=rgb(m1)[m]
K=8
_,lbl,cent=cv2.kmeans(L.astype(np.float32),K,None,(cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,100,0.2),10,cv2.KMEANS_PP_CENTERS)
lbl=lbl.ravel(); names=[]
for k in range(K):
    sel=lbl==k; c=px[sel].mean(0).astype(int)
    mk=np.zeros((H,W),bool); mk[ys[sel],xs[sel]]=True
    lines=trace(clean(cv2.dilate(mk.astype(np.uint8),np.ones((3,3),np.uint8)),20).astype(np.uint8),5,0.6)
    tot=sum(LineString(x).length for x in lines)
    print(k,sel.sum(),'#%02x%02x%02x'%tuple(c),'x',xs[sel].min(),xs[sel].max(),'len_px',round(tot))
    names.append({'color':'#%02x%02x%02x'%tuple(c),'lines':lines,'len':tot})
D['names']=names
# ---- map4 spotlight (own frame) ----
m4=cv2.imread(S+'map4_spotlight.png'); h4=cv2.cvtColor(m4,cv2.COLOR_BGR2HSV); H4,S4,V4=[h4[:,:,i].astype(int) for i in range(3)]
r4=rgb(m4)
red=((H4<=8)|(H4>=170))&(S4>100)&(V4>90)
red=clean(cv2.dilate(red.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool),100)
grey=(S4<30)&(Vv.mean()>0)&(r4.mean(2)>100)&(r4.mean(2)<175)
grey=clean(grey,25)&~cv2.dilate(red.astype(np.uint8),np.ones((5,5),np.uint8)).astype(bool)
wat4=(H4>=88)&(H4<=108)&(S4>40)&(V4>180)
wat4=clean(wat4,30)
road4=(r4.min(2)>=248); road4=clean(road4,200)
blk4=clean(r4.sum(2)<90,100)
sp={'red':trace(red.astype(np.uint8),10,0.8),'grey':trace(cv2.dilate(grey.astype(np.uint8),np.ones((2,2),np.uint8)),8,0.8),
    'water_poly':poly(cv2.morphologyEx(wat4.astype(np.uint8),cv2.MORPH_CLOSE,np.ones((3,3),np.uint8)),400,0.8),
    'stream':trace(wat4.astype(np.uint8),15,0.8),'road':poly(road4.astype(np.uint8),150,0.8),'boundary':trace(cv2.dilate(blk4.astype(np.uint8),np.ones((2,2),np.uint8)),20,0.8)}
ys,xs=np.nonzero(red); sp['red_bbox']=[int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())]
sp['red_len_px']=round(sum(LineString(x).length for x in sp['red']),1)
print('map4 red bbox',sp['red_bbox'],'len',sp['red_len_px'],'pieces',len(sp['red']),'grey',len(sp['grey']),'water',len(sp['water_poly']),'stream',len(sp['stream']),'road',len(sp['road']))
D['spot']=sp
json.dump(D,open('build/geom.json','w'))
