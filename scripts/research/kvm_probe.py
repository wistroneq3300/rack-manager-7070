"""Complete an RFB session with a proper buffered reader."""
import asyncio, ssl, json, struct, urllib.request, http.cookiejar
import websockets

BMC="10.36.72.43"; USER="root"; PW="0penBmc"
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
cj=http.cookiejar.CookieJar()
op=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                               urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(f"https://{BMC}/login",
    data=json.dumps({"username":USER,"password":PW}).encode(),
    headers={"Content-Type":"application/json"}, method="POST"), timeout=15)
ck={c.name:c.value for c in cj}
hdr="; ".join(f"{k}={v}" for k,v in ck.items())

class Reader:
    def __init__(self, ws): self.ws=ws; self.buf=b""
    async def read(self, n, timeout=20):
        while len(self.buf) < n:
            chunk = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            self.buf += chunk if isinstance(chunk,(bytes,bytearray)) else chunk.encode()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

async def main():
    ws=await websockets.connect(f"wss://{BMC}/kvm/0", ssl=ctx, subprotocols=[ck["XSRF-TOKEN"]],
        additional_headers={"Cookie":hdr,"Origin":f"https://{BMC}"},
        open_timeout=15, max_size=None, ping_interval=None)
    r=Reader(ws)
    print("banner:", await r.read(12))
    await ws.send(b"RFB 003.008\n")
    count=(await r.read(1))[0]
    types=await r.read(count)
    print("security types:", list(types))
    await ws.send(bytes([1]))
    print("security result:", struct.unpack(">I", await r.read(4))[0])

    await ws.send(b"\x01")
    init=await r.read(24)
    w,hh=struct.unpack(">HH", init[0:4])
    bpp,depth,bigend,truecol=init[4],init[5],init[6],init[7]
    rmax,gmax,bmax=struct.unpack(">HHH", init[8:14])
    print(f"framebuffer {w}x{hh} bpp={bpp} depth={depth} bigEndian={bigend} trueColor={truecol} max=({rmax},{gmax},{bmax})")

    encs=[0, 1, 2, 5, 6, -223, -224]   # Raw, CopyRect, RRE, Hextile, zlib, DesktopSize, LastRect
    await ws.send(struct.pack(">BxH", 2, len(encs)) + b"".join(struct.pack(">i",e) for e in encs))
    await ws.send(struct.pack(">BBHHHH", 3, 0, 0, 0, w, hh))

    for attempt in range(200):
        mt=(await r.read(1))[0]
        if mt==0:
            _,nrect=struct.unpack(">BH", await r.read(3))
            print(f"FramebufferUpdate with {nrect} rect(s)")
            total=0
            for _ in range(nrect):
                rx,ry,rw,rh,enc=struct.unpack(">HHHHi", await r.read(12))
                if enc==0:
                    px=await r.read(rw*rh*(bpp//8))
                    total+=len(px)
                else:
                    print(f"  rect enc={enc} {rw}x{rh} (skipped)")
                    continue
                if _==0:
                    print(f"  first rect {rw}x{rh} @({rx},{ry}) raw {bpp//8}bpp")
            print(f"RESULT: FRAMEBUFFER OK  ({total} bytes in this update)")
            break
        elif mt==1:   # SetColourMapEntries
            await r.read(3); n=struct.unpack(">H", await r.read(2))[0]; await r.read(n*6)
        elif mt==2:   # Bell
            pass
        elif mt==3:   # ServerCutText
            await r.read(3); ln=struct.unpack(">I", await r.read(4))[0]; await r.read(ln)
        else:
            print("unknown msg type", mt, "(stream may be compressed; stopping)"); break
    await ws.close()
asyncio.run(main())
