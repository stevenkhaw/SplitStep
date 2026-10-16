import json, sys, numpy as np
from splitstep import resources
from splitstep.accel import detect_accel
from splitstep.detect.vision import _run_frames
from splitstep.resources import ffmpeg_exe
from ultralytics import YOLO
proxy, out, secs = sys.argv[1], sys.argv[2], sys.argv[3]
W,H=960,540
acc=detect_accel()
cmd=[ffmpeg_exe(),"-v","error"]+(["-hwaccel",acc.hwaccel] if acc.hwaccel else [])+["-t",secs,"-i",proxy,"-vf",f"fps=5,scale={W}:-2","-f","rawvideo","-pix_fmt","rgb24","-"]
m=YOLO(resources.yolo_weights())
with open(out,"w") as f:
    for i,raw in enumerate(_run_frames(cmd,W*H*3)):
        fr=np.frombuffer(raw,dtype=np.uint8).reshape(H,W,3)
        r=m.predict(fr,imgsz=960,classes=[0,32,38],conf=0.1,device=acc.torch_device,verbose=False)[0]
        bx=[[int(c),round(float(p),3)]+[round(v,4) for v in (x1/W,y1/H,x2/W,y2/H)] for (x1,y1,x2,y2),c,p in zip(r.boxes.xyxy.tolist(),r.boxes.cls.tolist(),r.boxes.conf.tolist())]
        f.write(json.dumps({"t":i*200,"b":bx})+"\n")
        if i%500==0: print(i,flush=True)
