"""KVM (IPMI console) relay for the NVIDIA BMC.

The BMC exposes its console as a plain RFB 3.8 session over a websocket at
``wss://<bmc>/kvm/0``. Authenticating it takes all three of:

  * a cookie pair from the *web* login endpoint (``POST /login``) — the
    Redfish session token does not work here and issues no cookies at all;
  * that ``XSRF-TOKEN`` value echoed back as the websocket subprotocol;
  * an ``Origin`` header matching the BMC.

Rack Manager cannot let the browser talk to the BMC directly: that would hand
the BMC password to every page load and require the browser to be on the BMC
network. So rackd keeps the credentials server side, opens the socket itself,
and relays frames. The browser only ever sees raw RFB bytes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.request
import http.cookiejar
from pathlib import Path

import inventory as inv

GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
WS_TIMEOUT = 15.0
IDLE_TIMEOUT = 300.0          # a console nobody touches is closed after 5 min


class KvmError(Exception):
    pass


# ---------------------------------------------------------------------------
# Minimal websocket client, so rackd needs no extra dependency.
# ---------------------------------------------------------------------------
class _WsClient:
    """Blocking RFC 6455 client: enough for the RFB byte stream."""

    def __init__(self, host, port, path, extra_headers, ssl_ctx):
        raw = socket.create_connection((host, port), timeout=WS_TIMEOUT)
        self.sock = ssl_ctx.wrap_socket(raw, server_hostname=host)
        self.sock.settimeout(WS_TIMEOUT)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f'GET {path} HTTP/1.1',
            f'Host: {host}:{port}',
            'Upgrade: websocket',
            'Connection: Upgrade',
            f'Sec-WebSocket-Key: {key}',
            'Sec-WebSocket-Version: 13',
        ]
        for k, v in extra_headers.items():
            lines.append(f'{k}: {v}')
        self.sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
        self._buf = b''
        head = self._read_until(b'\r\n\r\n')
        status = head.split(b'\r\n', 1)[0]
        if b'101' not in status:
            raise KvmError(f'websocket upgrade rejected: {status.decode(errors="replace")}')
        self._frag = b''

    # -- raw socket helpers -------------------------------------------------
    def _read_until(self, marker):
        while marker not in self._buf:
            chunk = self.sock.recv(16384)
            if not chunk:
                raise KvmError('BMC closed the connection during handshake')
            self._buf += chunk
        head, _, rest = self._buf.partition(marker)
        self._buf = rest
        return head

    def _need(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise KvmError('BMC closed the KVM connection')
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    # -- framing ------------------------------------------------------------
    def send(self, data: bytes):
        """Send one binary frame (no fragmentation; RFB chunks are small)."""
        if not data:
            return
        header = bytearray([0x82])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack('>H', n)
        else:
            header.append(0x80 | 127)
            header += struct.pack('>Q', n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        """Next payload. Handles ping/pong/close and continuation frames."""
        while True:
            b1, b2 = self._need(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack('>H', self._need(2))[0]
            elif length == 127:
                length = struct.unpack('>Q', self._need(8))[0]
            mask = self._need(4) if masked else b''
            payload = self._need(length)
            if masked:
                payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
            if opcode == 0x8:                     # close
                raise KvmError('BMC closed the KVM session')
            if opcode == 0x9:                     # ping -> pong
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x0:                     # continuation
                self._frag += payload
                if b1 & 0x80:
                    out, self._frag = self._frag, b''
                    return out
                continue
            self._frag = payload
            if b1 & 0x80:
                out, self._frag = self._frag, b''
                return out

    def _send_control(self, opcode, payload=b''):
        mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
        self.sock.sendall(header + bytes(b ^ mask[i & 3] for i, b in enumerate(payload)))

    def close(self):
        try:
            self._send_control(0x8)
        except Exception:                                        # noqa: BLE001
            pass
        try:
            self.sock.close()
        except Exception:                                        # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# BMC login -> KVM socket
# ---------------------------------------------------------------------------
def _bmc_creds(node_name):
    node = inv.lookup_node(node_name)
    if node is None:
        raise KvmError(f'找不到節點 {node_name!r}')
    host = node.get('bmc_ip')
    if not host:
        raise KvmError(f'{node_name} 沒有 BMC 位址')
    user = node.get('bmc_user') or inv.BMC_USER
    pw = node.get('bmc_pass') or inv.BMC_PASS
    return host, user, pw


def _login(host, user, password):
    """Web login, because only that endpoint hands out the cookie pair."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        f'https://{host}/login',
        data=json.dumps({'username': user, 'password': password}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with opener.open(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:                                     # noqa: BLE001
        raise KvmError(f'BMC 登入失敗：{exc}') from exc
    cookies = {c.name: c.value for c in jar}
    if 'XSRF-TOKEN' not in cookies:
        raise KvmError('BMC 未回傳 XSRF-TOKEN，KVM 無法認證')
    return cookies, '; '.join(f'{k}={v}' for k, v in cookies.items())


def connect(node_name):
    """Open a KVM socket; returns (ws, first_rfb_bytes)."""
    host, user, password = _bmc_creds(node_name)
    cookies, cookie_hdr = _login(host, user, password)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ws = _WsClient(host, 443, '/kvm/0', {
        'Cookie': cookie_hdr,
        'Origin': f'https://{host}',
        'Sec-WebSocket-Protocol': cookies['XSRF-TOKEN'],
    }, ctx)
    # The BMC speaks first with the RFB version banner.
    banner = ws.recv()
    return ws, banner


# ---------------------------------------------------------------------------
# Browser side: how rackd hands the socket over
# ---------------------------------------------------------------------------
class BrowserSocket:
    """Server side of an upgraded connection to our own page."""

    def __init__(self, rfile, wfile):
        self.rfile = rfile
        self.wfile = wfile
        self._lock = threading.Lock()

    @staticmethod
    def accept_key(key):
        return base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()

    def send(self, data: bytes):
        if not data:
            return
        with self._lock:
            header = bytearray([0x82])
            n = len(data)
            if n < 126:
                header.append(n)
            elif n < 65536:
                header.append(126)
                header += struct.pack('>H', n)
            else:
                header.append(127)
                header += struct.pack('>Q', n)
            try:
                self.wfile.write(bytes(header) + data)
                self.wfile.flush()
            except Exception:                                    # noqa: BLE001
                raise KvmError('瀏覽器端已斷線')

    def recv(self):
        """Payload of the next frame, or None when the client went away."""
        try:
            head = self.rfile.read(2)
        except Exception:                                        # noqa: BLE001
            return None
        if len(head) < 2:
            return None
        b1, b2 = head[0], head[1]
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack('>H', self.rfile.read(2))[0]
        elif length == 127:
            length = struct.unpack('>Q', self.rfile.read(8))[0]
        mask = self.rfile.read(4) if masked else b''
        payload = self.rfile.read(length) if length else b''
        if masked and payload:
            payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            self._pong(payload)
            return self.recv()
        if opcode == 0xA:
            return self.recv()
        return payload

    def _pong(self, payload):
        with self._lock:
            try:
                self.wfile.write(bytes([0x8A, len(payload)]) + payload)
                self.wfile.flush()
            except Exception:                                    # noqa: BLE001
                pass

    def close(self):
        try:
            with self._lock:
                self.wfile.write(bytes([0x88, 0]))
                self.wfile.flush()
        except Exception:                                        # noqa: BLE001
            pass


class Registry:
    """Live console sessions, so the idle reaper can close them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}
        self._seq = 0
        threading.Thread(target=self._reaper, daemon=True).start()

    def start(self, node):
        with self._lock:
            self._seq += 1
            sid = self._seq
            self._sessions[sid] = {'node': node, 'started': time.time(),
                                   'seen': time.time()}
        return sid

    def touch(self, sid):
        with self._lock:
            s = self._sessions.get(sid)
            if s:
                s['seen'] = time.time()

    def finish(self, sid):
        with self._lock:
            self._sessions.pop(sid, None)

    def live_count(self):
        with self._lock:
            return len(self._sessions)

    def _reaper(self):
        while True:
            time.sleep(60)
            now = time.time()
            with self._lock:
                stale = [k for k, v in self._sessions.items()
                         if now - v['seen'] > IDLE_TIMEOUT]
            for sid in stale:
                self.finish(sid)


REGISTRY = Registry()


def pump(browser, ws, node_name):
    """Relay bytes both ways until either side goes away.

    rackd has already opened the BMC socket and sent the banner, because the
    101 must not go out when the console is unreachable."""
    sid = REGISTRY.start(node_name)
    stop = threading.Event()

    def upstream():
        """BMC -> browser."""
        try:
            while not stop.is_set():
                browser.send(ws.recv())
        except Exception:                                        # noqa: BLE001
            pass
        finally:
            stop.set()

    t = threading.Thread(target=upstream, daemon=True)
    t.start()
    try:
        while not stop.is_set():
            data = browser.recv()
            if data is None:
                break
            REGISTRY.touch(sid)
            ws.send(data)
    except Exception:                                            # noqa: BLE001
        pass
    finally:
        stop.set()
        ws.close()
        browser.close()
        REGISTRY.finish(sid)
