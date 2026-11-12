# NVIDIA BMC KVM — protocol notes (research only, not wired into the app)

Reverse-engineered against **Neutrino4-n0 BMC 10.36.72.43** (NVIDIA BMC Web UI,
Redfish 1.17). Verified working end-to-end from Python.

## What did NOT work

- Redfish session `X-Auth-Token` as the WebSocket subprotocol → **401**.
  The Redfish login (`POST /redfish/v1/SessionService/Sessions`) returns 201
  with `X-Auth-Token` but **issues no cookies at all**, so that path cannot
  authenticate the KVM socket.

## What works

**Step 1 — log in through the *web* endpoint, not Redfish**

```
POST https://<bmc>/login
Content-Type: application/json

{"username": "root", "password": "<bmc password>"}
```

Returns `200` and sets two cookies:

```
XSRF-TOKEN=<32 chars>; Path=/; SameSite=Strict; Secure
BMCWEB-SESSION=<20 chars>; Path=/; SameSite=Strict; Secure; HttpOnly
```

**Step 2 — open the KVM socket**

```
wss://<bmc>/kvm/0
Sec-WebSocket-Protocol: <value of XSRF-TOKEN>
Cookie: XSRF-TOKEN=<...>; BMCWEB-SESSION=<...>
Origin: https://<bmc>
```

All three parts are required. The token goes in the **subprotocol header**,
not in `Authorization`.

**Step 3 — plain RFB 3.8 handshake**

| Step | Direction | Bytes |
|---|---|---|
| banner | server → client | `RFB 003.008\n` |
| version | client → server | `RFB 003.008\n` |
| security types | server → client | `01 01` → `[1]` = **None**, no VNC password |
| choose None | client → server | `01` |
| auth result | server → client | `00000000` (ok) |
| ClientInit | client → server | `01` (shared) |
| ServerInit | server → client | 24 bytes: w, h, bpp, depth, big-endian, true-colour, r/g/b max, name |
| SetEncodings | client → server | type 2 |
| FBUpdateRequest | client → server | type 3 |

## Observed framebuffer

```
640x480, 32 bpp, depth 32, little-endian, true-colour, max (255,255,255)
```
Raw encoding (0) delivers the full frame: **1,228,800 bytes** of BGRA pixels.
On a powered-off node the pixels are all zero (black screen) — a black KVM view
does not mean the connection failed.

## WebSocket framing caveat

`websockets` delivers one WS frame per `recv()`, and a single RFB message can
span many frames (the 1.2 MB frame arrived as ~3 chunks). Always read through a
buffered reader that accumulates until N bytes are available; reading frame by
frame desynchronises the stream (symptom: `unknown message type`).

## Also learned

- `GraphicalConsole: {"ConnectTypesSupported": ["KVMIP"], "MaxConcurrentSessions": 4}`
  is advertised in `/redfish/v1/Managers/BMC_0`.
- The BMC's own SPA routes the console at `#/console/kvm` and builds the socket
  as `new RFB(wss://host/kvm/0, {wsProtocols: [authStore.token]})`.
- `GET /kvm/0` over plain HTTPS returns 404 — the path only exists as a
  WebSocket upgrade, which is why earlier HTTP probes looked empty.

## Status

Research complete; **not integrated into rack-manager**. Wiring it up would need
a WebSocket-capable server (rackd is `http.server`), a proxy that keeps BMC
credentials server-side, and a noVNC front-end.

---

# Round 2 — live console and input (verified)

Host: Neutrino4-n0 BMC `10.36.72.43`, node powered **On**.

## The framebuffer really is live

Desktop name reported in ServerInit is **`OpenBMC IKVM`**. A full raw frame is
307,200 pixels (640x480); on a powered-on host that had no graphical output it
held **99 non-black pixels — exactly the white mouse pointer**, drawn at its
last known position. So the capture path is correct and a mostly black image is
a property of the host, not a broken client.

## Input messages

| Event | Type | Wire format |
|---|---|---|
| Pointer | `5` | `05` `buttonmask` `x:u16` `y:u16` |
| Key | `4` | `04` `down:bool` `pad:u16` `keysym:u32` |

Captured from the BMC's own web client (CDP `Network.webSocketFrameSent`) to
confirm the encoding rather than guess it:

```
press 'a'  -> 04 01 0000 00000061   (KeyEvent, down, keysym 0x61)
release 'a'-> 04 00 0000 00000061
mouse move -> 05 00 0057 0046       (mask 0 => nothing pressed, x=87 y=70)
```

Byte-for-byte identical to what this client sends, so the input path is right.

## Proof that pointer input lands

Move the pointer to the centre and re-read the frame: the white pointer moved
from `(348, 82)` to `(320, 240)` — the exact coordinates that were sent. The
cursor is composited locally by the BMC against the captured framebuffer, which
makes it a reliable end-to-end check of the input path.

## Keyboard has no observable effect *on this host*

Keys are accepted (the socket stays healthy, no error frame comes back) but the
framebuffer never changes, because the host has no graphical console consuming
input. This is a property of the target, not evidence of a broken key path — the
wire format above matches the BMC's own client exactly.

**Consequence for testing:** do not conclude "keyboard does not work" from a
black screen. Verify the frame encoding against a real browser session instead.

## Practical notes for a future integration

- The pointer is drawn by the BMC, so it appears even when the host draws
  nothing — useful as a liveness signal without waking the host.
- Raw encoding full-frame is ~1.23 MB per update at 640x480x32. Requesting
  incremental updates (`incremental=1`) is what keeps it usable for a viewer.
- Keysym values are X11 keysyms (`0xff53` = Right arrow, `0xff14` = ScrollLock),
  not USB HID codes. A front-end must translate from `KeyboardEvent.code`.
