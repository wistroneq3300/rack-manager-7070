"""Test that keyboard/mouse events reach the remote — read-only, no power ops.
We only move the mouse pointer and never click, so nothing can be actuated.
If the cursor moves in a subsequent frame, input works."""
import asyncio, ssl, json, struct, urllib.request, http.cookiejar, zlib
import websockets

BMC="10.36.72.43"; USER="root"; PW="0penBmc"
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE

def login():
    cj=http.cookiejar.CookieJar()
    op=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                   urllib.request.HTTPCookieProcessor(cj))
    op.open(urllib.request.Request(f"https://{BMC}/login",
        data=json.dumps({"username":USER,"password":PW}).encode(),
        headers={"Content-Type":"application/json"}, method="POST"), timeout=20)
    ck={c.name:c.value for c in cj}
    return ck, "; ".join(f"{k}={v}" for k,v in ck.items())

class R:
    def __init__(s, w): s.ws=w; s.buf=b""
    async def n(s,k,t=15):
        while len(s.buf)<k: s.buf += await asyncio.wait_for(s.ws.recv(), timeout=t)
        o,s.buf=s.buf[:k],s.buf[k:]; return o

def find_cursor(fb,w,h,bpp):
    """Brightest pixel cluster — the white pointer on a black console."""
    best=None
    step=bpp//8
    for i in range(0,len(fb),step):
        r,g,b = fb[i+2],fb[i+1],fb[i]
        if r>200 and g>200 and b>200:
            p=i//step; x,y=p%w, p//w
            if best is None: best=[x,y,0,0]
            best[2]+=1
            best[3]=x; best[0]=min(best[0],x); best[1]=min(best[1],y)
    return best

async def read_frame(r, ws, w, h, bpp):
    await ws.send(struct.pack(">BBHHHH",3,1,0,0,w,h))   # incremental request
    fb=bytearray(w*h*(bpp//8))
    for _ in range(300):
        mt=(await r.n(1))[0]
        if mt==0:
            _,nr=struct.unpack(">BH", await r.n(3))
            for _ in range(nr):
                x,y,rw,rh,enc=struct.unpack(">HHHHi", await r.n(12))
                if enc!=0: continue
                px=await r.n(rw*rh*(bpp//8))
                for row in range(rh):
                    s=row*rw*(bpp//8); d=((y+row)*w+x)*(bpp//8)
                    fb[d:d+rw*(bpp//8)]=px[s:s+rw*(bpp//8)]
            return fb
        elif mt==1: await r.n(5); n=struct.unpack(">H",await r.n(2))[0]; await r.n(n*6)
        elif mt==2: pass
        elif mt==3: await r.n(3); ln=struct.unpack(">I",await r.n(4))[0]; await r.n(ln)
    return fb

async def main():
    ck,hdr=login()
    ws=await websockets.connect(f"wss://{BMC}/kvm/0", ssl=ctx, subprotocols=[ck["XSRF-TOKEN"]],
        additional_headers={"Cookie":hdr,"Origin":f"https://{BMC}"},
        open_timeout=20, max_size=None, ping_interval=None)
    r=R(ws)
    await r.n(12); await ws.send(b"RFB 003.008\n")
    c=(await r.n(1))[0]; await r.n(c); await ws.send(b"\x01"); await r.n(4)
    await ws.send(b"\x01")
    init=await r.n(24); w,h,bpp=struct.unpack(">HH",init[:4])+ (init[4],)
    nl=struct.unpack(">I",init[20:24])[0]; await r.n(nl) if nl else None
    print(f"desktop {w}x{h}")
    await ws.send(struct.pack(">BxH",2,1)+struct.pack(">i",0))
    await ws.send(struct.pack(">BBHHHH",3,0,0,0,w,h))
    fb=await read_frame(r,ws,w,h,bpp)
    c0=find_cursor(fb,w,h,bpp)
    print("cursor before:", c0 and c0[:2])

    # pointer event: message 5, button mask 0 (no buttons pressed), x, y
    tx,ty = w//2, h//2
    await ws.send(struct.pack(">BBHH", 5, 0, tx, ty))
    print(f"sent pointer move -> ({tx},{ty})")
    await asyncio.sleep(0.6)
    fb2=await read_frame(r,ws,w,h,bpp)
    c1=find_cursor(fb2,w,h,bpp)
    print("cursor after :", c1 and c1[:2])
    moved = c0 and c1 and (abs(c0[0]-c1[0])>5 or abs(c1[0]-tx)<8)
    print("RESULT: pointer", "WORKS" if moved else "no visible change")
    await ws.close()
asyncio.run(main())
