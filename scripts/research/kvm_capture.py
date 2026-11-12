"""Grab one full frame from the BMC KVM and save it as a PNG."""
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
    def __init__(self, ws): self.ws=ws; self.buf=b""
    async def n(self, k, t=20):
        while len(self.buf)<k:
            self.buf += await asyncio.wait_for(self.ws.recv(), timeout=t)
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

def png(w,h,bgra,path):
    # RFB gives BGRA little-endian; PNG wants RGBA
    raw=bytearray()
    stride=w*4
    for y in range(h):
        raw.append(0)
        row=bgra[y*stride:(y+1)*stride]
        for x in range(0,len(row),4):
            b,g,r,a = row[x],row[x+1],row[x+2],row[x+3]
            raw += bytes((r,g,b,255))
    def chunk(t,d):
        c=struct.pack(">I",len(d))+t+d
        return c+struct.pack(">I", zlib.crc32(t+d)&0xffffffff)
    png=b"\x89PNG\r\n\x1a\n"
    png+=chunk(b"IHDR", struct.pack(">IIBBBBB", w,h,8,6,0,0,0))
    png+=chunk(b"IDAT", zlib.compress(bytes(raw),6))
    png+=chunk(b"IEND", b"")
    open(path,"wb").write(png)

async def main():
    ck,hdr=login()
    ws=await websockets.connect(f"wss://{BMC}/kvm/0", ssl=ctx, subprotocols=[ck["XSRF-TOKEN"]],
        additional_headers={"Cookie":hdr,"Origin":f"https://{BMC}"},
        open_timeout=20, max_size=None, ping_interval=None)
    r=R(ws)
    print("banner:", await r.n(12))
    await ws.send(b"RFB 003.008\n")
    cnt=(await r.n(1))[0]; print("sec types:", list(await r.n(cnt)))
    await ws.send(b"\x01")
    print("auth:", struct.unpack(">I", await r.n(4))[0])
    await ws.send(b"\x01")
    init=await r.n(24)
    w,h=struct.unpack(">HH", init[:4]); bpp=init[4]
    namelen=struct.unpack(">I", init[20:24])[0]
    name=await r.n(namelen) if namelen else b""
    print(f"desktop: {w}x{h} bpp={bpp} name={name!r}")

    await ws.send(struct.pack(">BxH",2,1)+struct.pack(">i",0))   # raw only
    await ws.send(struct.pack(">BBHHHH",3,0,0,0,w,h))

    while True:
        mt=(await r.n(1))[0]
        if mt==0:
            _,nrect=struct.unpack(">BH", await r.n(3))
            fb=bytearray(w*h*bpp//8)
            for _ in range(nrect):
                x,y,rw,rh,enc=struct.unpack(">HHHHi", await r.n(12))
                if enc!=0:
                    print("  non-raw enc", enc); continue
                px=await r.n(rw*rh*(bpp//8))
                for row in range(rh):
                    s=row*rw*(bpp//8); d=((y+row)*w+x)*(bpp//8)
                    fb[d:d+rw*(bpp//8)]=px[s:s+rw*(bpp//8)]
            png(w,h,bytes(fb),"/tmp/kvm_frame.png")
            # report how much is non-black -> tells us if a real screen is there
            nonblack=sum(1 for i in range(0,len(fb),4) if fb[i] or fb[i+1] or fb[i+2])
            print(f"frame saved: {nrect} rects, non-black pixels: {nonblack}/{w*h}")
            break
        elif mt==1: await r.n(5); n=struct.unpack(">H",await r.n(2))[0]; await r.n(n*6)
        elif mt==2: pass
        elif mt==3: await r.n(3); ln=struct.unpack(">I",await r.n(4))[0]; await r.n(ln)
        else: print("msg", mt); break
    await ws.close()
asyncio.run(main())
