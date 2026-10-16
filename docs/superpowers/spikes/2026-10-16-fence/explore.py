import json, sys, numpy as np
sys.path.insert(0, sys.argv[1]); from truth import truth
SP=sys.argv[1]
F=[json.loads(l) for l in open(f"{SP}/boxes_16min.jsonl")]
T=truth()
t=np.array([f["t"] for f in F]); inplay=np.zeros(len(F),bool)
for a,b,_ in T: inplay|=(t>=a)&(t<b)
def persons(f,conf=0.25): return [b for b in f["b"] if b[0]==0 and b[1]>=conf]
def foot(b): return b[5]
def cx(b): return (b[2]+b[4])/2
def h(b): return b[5]-b[3]
# region guesses from the frame
def far_box(f):
    c=[b for b in persons(f,0.1) if 0.405<=foot(b)<=0.47 and 0.40<=cx(b)<=0.665 and h(b)<0.12]
    return max(c,key=lambda b:b[1]) if c else None
def near_box(f):
    c=[b for b in persons(f) if 0.47<foot(b)<=0.80 and 0.12<=cx(b)<=0.95]
    return max(c,key=h) if c else None
def track(sel):
    pos=[]; 
    for f in F:
        b=sel(f); pos.append((cx(b),foot(b),h(b)) if b else None)
    v=np.zeros(len(F)); pres=np.zeros(len(F))
    for i in range(1,len(F)):
        if pos[i]: pres[i]=1
        if pos[i] and pos[i-1]:
            d=np.hypot(pos[i][0]-pos[i-1][0],pos[i][1]-pos[i-1][1])/max(pos[i][2],1e-3)
            v[i]=min(d*5,3)  # body lengths/s, clipped vs id swaps
    return pres,v,pos
fp,fv,fpos=track(far_box); np_,nv,npos=track(near_box)
ball=np.array([sum(1 for b in f["b"] if b[0]==32) for f in F])
def roll(x,w=5): return np.convolve(x,np.ones(w)/w,'same')
def auc(x):
    x=np.asarray(x); p=x[inplay]; n=x[~inplay]
    r=np.argsort(np.argsort(np.concatenate([p,n])))
    return (r[:len(p)].sum()-len(p)*(len(p)-1)/2)/(len(p)*len(n))
print("in-play frac",inplay.mean())
for name,x in [("far present",fp),("far present (1s)",roll(fp)),("far speed (1s)",roll(fv)),("far speed (2s)",roll(fv,10)),
               ("near present",np_),("near speed (1s)",roll(nv)),("near speed (2s)",roll(nv,10)),
               ("min speed (2s)",np.minimum(roll(fv,10),roll(nv,10))),("sum speed (2s)",roll(fv,10)+roll(nv,10)),
               ("ball dets (2s)",roll(ball,10))]:
    print(f"{name:20s} AUC {auc(x):.2f}  mean in {x[inplay].mean():.3f} out {x[~inplay].mean():.3f}")
np.save(f"{SP}/sig.npy",np.vstack([t,inplay,fp,fv,np_,nv,ball]))
