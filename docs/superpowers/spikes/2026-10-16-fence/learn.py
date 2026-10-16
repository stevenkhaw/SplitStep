import json,sys,numpy as np
from scipy.stats import rankdata
SP=sys.argv[1]; sys.path.insert(0,SP); from truth import truth, FEAT
from splitstep.detect.features import read_features
F=[json.loads(l) for l in open(f"{SP}/boxes_16min.jsonl")]; T=truth(); N=len(F)
t=np.array([f["t"] for f in F]); ip=np.zeros(N,bool)
for a,b,_ in T: ip|=(t>=a)&(t<b)
cx=lambda b:(b[2]+b[4])/2; ft=lambda b:b[5]; h=lambda b:b[5]-b[3]; w=lambda b:b[4]-b[2]
def track(cands):
    """greedy nearest-neighbour single track over candidate boxes"""
    out=[None]*N; prev=None
    for i,f in enumerate(F):
        c=cands(f)
        if not c: out[i]=None; continue
        if prev is None: b=max(c,key=h)
        else: b=min(c,key=lambda b:abs(cx(b)-prev[0])+abs(ft(b)-prev[1]))
        out[i]=(cx(b),ft(b),h(b),w(b),b[3]); prev=out[i]
    return out
far=track(lambda f:[b for b in f["b"] if b[0]==0 and b[1]>.1 and 0.405<=ft(b)<=0.47 and 0.40<=cx(b)<=0.665 and h(b)<.12])
near=track(lambda f:[b for b in f["b"] if b[0]==0 and b[1]>.25 and 0.53<ft(b)<0.85 and 0.12<cx(b)<0.95])
def arr(tr,k,fill=np.nan): return np.array([p[k] if p else fill for p in tr])
def spd(tr):
    v=np.zeros(N)
    for i in range(1,N):
        if tr[i] and tr[i-1]: v[i]=min(np.hypot(tr[i][0]-tr[i-1][0],tr[i][1]-tr[i-1][1])/tr[i][2]*5,3)
    return v
def lat(tr):
    v=np.zeros(N)
    for i in range(1,N):
        if tr[i] and tr[i-1]: v[i]=min(abs(tr[i][0]-tr[i-1][0])/tr[i][2]*5,3)
    return v
fr=[f for f in read_features(FEAT)][:N]; hits=np.array([f.hits for f in fr],float)
ball=np.array([sum(1 for b in f["b"] if b[0]==32) for f in F],float)
def R(x,wn): x=np.nan_to_num(x); return np.convolve(x,np.ones(wn)/wn,'same')
def RS(x,wn):  # rolling std
    m=R(x,wn); return np.sqrt(np.maximum(R(np.nan_to_num(x)**2,wn)-m*m,0))
def past(x,wn): # trailing window mean
    x=np.nan_to_num(x); c=np.cumsum(np.r_[0,x]); i=np.arange(N); lo=np.maximum(0,i-wn+1); return (c[i+1]-c[lo])/(i+1-lo)
def fut(x,wn): return past(x[::-1],wn)[::-1]
nasp=arr(near,2)/arr(near,3); ntop=arr(near,4); nft=arr(near,1); fcx=arr(far,0); ncx=arr(near,0)
fs,ns,fl,nl=spd(far),spd(near),lat(far),lat(near)
feats={}
for wn in (5,10,20,40):
    feats[f"fs{wn}"]=R(fs,wn); feats[f"ns{wn}"]=R(ns,wn); feats[f"fl{wn}"]=R(fl,wn); feats[f"nl{wn}"]=R(nl,wn)
    feats[f"hit{wn}"]=R(hits,wn); feats[f"ball{wn}"]=R(ball,wn)
    feats[f"aspsd{wn}"]=RS(nasp,wn); feats[f"topsd{wn}"]=RS(ntop,wn); feats[f"fcxsd{wn}"]=RS(fcx,wn); feats[f"ncxsd{wn}"]=RS(ncx,wn)
    feats[f"hitp{wn}"]=past(hits,wn); feats[f"hitf{wn}"]=fut(hits,wn)
feats["nft"]=np.nan_to_num(nft,nan=0.6); feats["nft_dev"]=np.abs(feats["nft"]-0.64)
feats["fpres"]=R(np.array([p is not None for p in far],float),10); feats["npres"]=R(np.array([p is not None for p in near],float),10)
names=list(feats); X=np.vstack([feats[k] for k in names]).T
def auc(s,y):
    r=rankdata(np.concatenate([s[y],s[~y]])); p=y.sum(); return (r[:p].sum()-p*(p+1)/2)/(p*(~y).sum())
print("single best:",sorted(((round(auc(X[:,j],ip),2),n) for j,n in enumerate(names)),reverse=True)[:10])
def fit(X,y,l2=1.0,it=3000,lr=0.1):
    mu,sd=X.mean(0),X.std(0)+1e-9; Z=(X-mu)/sd; wv=np.zeros(Z.shape[1]); b=0.
    for _ in range(it):
        p=1/(1+np.exp(-(Z@wv+b))); g=Z.T@(p-y)/len(y)+l2*wv/len(y); wv-=lr*g; b-=lr*(p-y).mean()
    return lambda X:1/(1+np.exp(-(((X-mu)/sd)@wv+b))), wv
block=(t//240000).astype(int); pred=np.zeros(N)
for k in np.unique(block):
    m,_=fit(X[block!=k],ip[block!=k].astype(float)); pred[block==k]=m(X[block==k])
print("held-out combined AUC", round(auc(pred,ip),2))
np.save(f"{SP}/pred.npy",np.vstack([t,pred,ip]))
_,wv=fit(X,ip.astype(float)); print("top weights",sorted(zip(np.round(wv,2),names),key=lambda z:-abs(z[0]))[:10])
