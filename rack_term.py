"""Interactive SSH shells for the rack UI.

One TerminalSession per open browser window: a paramiko SSH connection running
an interactive shell, a reader thread that appends decoded output to a growing
buffer, and long-poll readers that drain it.

Transport is deliberately plain HTTP rather than WebSocket: rackd runs on
http.server with no third-party server library, and a hand-rolled WebSocket
upgrade would be far more code (and more failure modes) than

    GET  /api/term/<sid>/stream?after=N   -> blocks up to ~25 s, returns new bytes
    POST /api/term/<sid>/input            -> keystrokes

Credentials never leave the server: the browser names a node and a kind, and
the passwords come from the inventory, exactly as the power actions do.
"""

from __future__ import annotations

import threading
import time
import uuid

import paramiko

import inventory as inv

# A wedged shell would otherwise pin a thread and a connection for ever.
IDLE_TIMEOUT = 60 * 60           # seconds without any traffic before reap
MAX_SESSIONS = 12                # 2-3 operators x a handful of windows each
CONNECT_TIMEOUT = 10
KEEPALIVE = 30
POLL_HOLD = 25.0                 # longest a /stream request may block
BACKLOG = 2 * 1024 * 1024        # retained output per session

KINDS = {
    'os': ('OS', 'os'),
    'bmc': ('BMC', 'bmc'),
    'bf4_os': ('BF4 OS', 'bf4_os'),
    'bf4_bmc': ('BF4 BMC', 'bf4_bmc'),
}


class SessionError(Exception):
    """Raised with a message fit to show in the terminal window."""


def _resolve(node_name, kind):
    """(host, label, user, password) for a node + kind, or raise SessionError.

    Credentials come from the inventory record, which lets a node override the
    tray defaults; the browser never sees them.
    """
    if kind not in KINDS:
        raise SessionError(f'unknown target {kind!r}')
    label = KINDS[kind][0]
    node = inv.lookup_node(node_name)
    if node is None:
        raise SessionError(f'找不到節點 {node_name!r}')

    if kind == 'os':
        host = node['os_ip']
        user = node.get('os_user') or inv.OS_USER
        password = node.get('os_pass') or inv.OS_PASS
    elif kind == 'bmc':
        host = node['bmc_ip']
        user = node.get('bmc_user') or inv.BMC_USER
        password = node.get('bmc_pass') or inv.BMC_PASS
    else:
        bf4_os, bf4_bmc = inv.bf4_node(node['tray'], node['node'])
        host = bf4_os if kind == 'bf4_os' else bf4_bmc
        if kind == 'bf4_os':
            user, password = inv.BF4_OS_USER, inv.BF4_OS_PASS
        else:
            user, password = inv.BF4_BMC_USER, inv.BF4_BMC_PASS

    if not host:
        raise SessionError(f'{node_name} 沒有 {label} 位址')
    return host, label, user, password


def reachable(host, port=22, timeout=1.5):
    """Cheap TCP probe so the UI can grey out addresses that will not answer."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class TerminalSession:
    def __init__(self, node, kind):
        self.sid = uuid.uuid4().hex[:16]
        self.node = node
        self.kind = kind
        host, label, user, password = _resolve(node, kind)
        self.host, self.label, self.user = host, label, user

        self._lock = threading.Lock()
        self._buf = bytearray()
        self._data_event = threading.Event()
        self._closed_event = threading.Event()
        self._client = None
        self._shell = None
        self.error = None
        self.closed_reason = None
        self.last_seen = time.time()

        self._connect(password)

    # -- connection -------------------------------------------------------
    def _connect(self, password):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                self.host, port=22, username=self.user, password=password,
                timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT,
                auth_timeout=CONNECT_TIMEOUT, look_for_keys=False,
                allow_agent=False,
            )
        except paramiko.AuthenticationException:
            raise SessionError(f'{self.user}@{self.host} 認證失敗（帳密不符）')
        except Exception as exc:                                 # noqa: BLE001
            raise SessionError(f'連不上 {self.user}@{self.host}：{exc}')

        transport = client.get_transport()
        if transport:
            transport.set_keepalive(KEEPALIVE)
        try:
            shell = client.invoke_shell(term='xterm-256color', width=120, height=32)
        except Exception as exc:                                 # noqa: BLE001
            client.close()
            raise SessionError(f'{self.host} 無法開啟 shell：{exc}')

        self._client, self._shell = client, shell
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            try:
                data = self._shell.recv(65536)
            except Exception:                                    # noqa: BLE001
                data = b''
            if not data:
                self._finish('連線已結束')
                return
            with self._lock:
                self._buf += data
                if len(self._buf) > BACKLOG:
                    del self._buf[:len(self._buf) - BACKLOG]
                self.last_seen = time.time()
            self._data_event.set()

    def _finish(self, reason):
        if self._closed_event.is_set():
            return
        self.closed_reason = reason
        self._closed_event.set()
        self._data_event.set()

    # -- io ---------------------------------------------------------------
    @property
    def offset(self):
        with self._lock:
            return len(self._buf)

    def read(self, after, hold=POLL_HOLD):
        """Bytes produced since `after`, or b'' once `hold` elapsed."""
        deadline = time.time() + hold
        while True:
            with self._lock:
                if len(self._buf) > after:
                    return bytes(self._buf[after:]), len(self._buf)
                closed, reason = self._closed_event.is_set(), self.closed_reason
            if closed:
                return b'', after
            remaining = deadline - time.time()
            if remaining <= 0:
                return b'', after
            self._data_event.wait(min(remaining, 1.0))
            self._data_event.clear()

    def write(self, data):
        if not data or self._shell is None:
            return
        self.last_seen = time.time()
        try:
            self._shell.send(data)
        except Exception:                                        # noqa: BLE001
            self._finish('寫入失敗，連線可能已斷')

    def resize(self, cols, rows):
        if not (self._shell and cols and rows):
            return
        try:
            self._shell.resize_pty(width=int(cols), height=int(rows))
            self.last_seen = time.time()
        except Exception:                                        # noqa: BLE001
            pass

    def close(self, reason='已關閉'):
        self._finish(reason)
        for closable in (self._shell, self._client):
            try:
                if closable:
                    closable.close()
            except Exception:                                    # noqa: BLE001
                pass
        self._shell = self._client = None

    @property
    def alive(self):
        return not self._closed_event.is_set()


class SessionRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}
        threading.Thread(target=self._reaper, daemon=True).start()

    def _reaper(self):
        while True:
            time.sleep(60)
            now = time.time()
            with self._lock:
                stale = [s for s in self._sessions.values()
                         if not s.alive or now - s.last_seen > IDLE_TIMEOUT]
            for s in stale:
                s.close('閒置逾時')
                with self._lock:
                    self._sessions.pop(s.sid, None)

    def open(self, node, kind):
        with self._lock:
            live = sum(1 for s in self._sessions.values() if s.alive)
            if live >= MAX_SESSIONS:
                raise SessionError(
                    f'已開啟 {live} 個終端（上限 {MAX_SESSIONS}），請先關閉一些視窗')
        session = TerminalSession(node, kind)
        with self._lock:
            self._sessions[session.sid] = session
        return session

    def get(self, sid):
        with self._lock:
            s = self._sessions.get(sid)
        if s is None:
            raise SessionError(f'終端 {sid} 不存在或已結束')
        return s

    def close(self, sid):
        with self._lock:
            s = self._sessions.pop(sid, None)
        if s:
            s.close()

    def live_count(self):
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.alive)


REGISTRY = SessionRegistry()
