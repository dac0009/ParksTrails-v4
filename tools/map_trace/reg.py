import cv2, numpy as np, json
import os; S=os.environ.get('MAPS_DIR','../../')
os.makedirs('build',exist_ok=True)
ref=cv2.imread(S+'map1_trail_routes.png',0)
orb=cv2.ORB_create(8000)
k1,d1=orb.detectAndCompute(ref,None)
out={}
for f in ['map2_designation','map3_core_loops','map5_decision','map4_spotlight']:
    im=cv2.imread(S+f+'.png',0)
    k2,d2=orb.detectAndCompute(im,None)
    m=cv2.BFMatcher(cv2.NORM_HAMMING,crossCheck=True).match(d2,d1)
    p2=np.float32([k2[x.queryIdx].pt for x in m]); p1=np.float32([k1[x.trainIdx].pt for x in m])
    A,inl=cv2.estimateAffinePartial2D(p2,p1,method=cv2.RANSAC,ransacReprojThreshold=2.0,maxIters=20000)
    s=np.hypot(A[0,0],A[1,0]); 
    print(f,'matches',len(m),'inliers',int(inl.sum()),'scale %.4f'%s,'rot %.3f'%np.degrees(np.arctan2(A[1,0],A[0,0])),'t',A[:,2].round(1))
    out[f]=A.tolist()
json.dump(out,open('build/affine.json','w'))
