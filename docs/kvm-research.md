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
