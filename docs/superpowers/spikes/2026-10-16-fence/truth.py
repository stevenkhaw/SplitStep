import csv
from pathlib import Path
FEAT=Path('/Volumes/SanDisk_2TB/SplitStep/sessions/2026-10-12/sources/01/features.jsonl')
END_MS=16*60*1000
def ts(s):
    m,sec=s.strip().split(':'); return (int(m)*60+int(sec))*1000
def truth():
    rows=list(csv.DictReader(open('tests/fixtures/truth/2026-10-12_source01.tsv'),delimiter='\t'))
    return [(ts(r['in']),ts(r['out']),r['note']) for r in rows]
def evaluate(intervals, label=''):
    T=truth(); iv=[(i.start_ms,i.end_ms) for i in intervals if i.start_ms<END_MS]
    hit=0; starts=[]; ends=[]; missed=[]
    for a,b,note in T:
        ov=[(s,e) for s,e in iv if min(b,e)-max(a,s)>0]
        cov=sum(min(b,e)-max(a,s) for s,e in ov)/(b-a)
        if cov>=0.5:
            hit+=1
            starts.append((min(s for s,_ in ov)-a)/1000); ends.append((max(e for _,e in ov)-b)/1000)
        else: missed.append(f"{a//60000}:{a//1000%60:02d}")
    det=sum(min(e,END_MS)-s for s,e in iv)
    inplay=sum(max(0,min(b,e)-max(a,s)) for s,e in iv for a,b,_ in T)
    med=lambda xs: sorted(xs)[len(xs)//2] if xs else float('nan')
    print(f"{label:28s} recall {hit}/{len(T)}  clips {len(iv):3d}  precision(time) {inplay/det if det else 0:.0%}  "
          f"start err med {med(starts):+.1f}s  end err med {med(ends):+.1f}s  missed {' '.join(missed)}")
