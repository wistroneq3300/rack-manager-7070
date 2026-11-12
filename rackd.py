#!/usr/bin/env python3
"""Rack Manager web service for the Neutrino rack.

    ./rackd.py                serve on 0.0.0.0:7070
    ./rackd.py --port 7071

Read-only by default: /api/status only pings and reads Redfish. Every power
route requires an explicit confirmation token that the browser obtains from a
dry-run of the same request, so a stray POST cannot cycle a node.

Standard library only (plus system ping/curl/ssh/sshpass).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import inventory as inv
import rack_flash as flash
import rack_hw
import rack_probe as probe
import rack_term

HERE = Path(__file__).resolve().parent
HTML = HERE / 'index.html'

# Pending confirmations: token -> {payload, expires}. A power request must first
# ask for the token (?dry_run=1), then repeat it with the token attached.
_PENDING = {}
_PENDING_LOCK = threading.Lock()
TOKEN_TTL = 120

# Serialises power actions so two operators cannot interleave cycles on the
# same target.
_ACTION_LOCK = threading.Lock()


def _issue_token(payload):
    token = secrets.token_urlsafe(18)
    with _PENDING_LOCK:
        now = time.time()
        for key in [k for k, v in _PENDING.items() if v['expires'] < now]:
            _PENDING.pop(key, None)
        _PENDING[token] = {'payload': payload, 'expires': now + TOKEN_TTL}
    return token


def _consume_token(token, payload):
    """True only when the token exists, has not expired and matches the payload."""
    with _PENDING_LOCK:
        entry = _PENDING.pop(token or '', None)
    if not entry:
        return False
    if entry['expires'] < time.time():
        return False
    return entry['payload'] == payload


def _collect_status_uncached():
    """Ping every node and read BMC/shelf state, in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    def node_status(rec):
        tray, i, name, os_ip, bmc_ip = rec
        bf4_os, bf4_bmc = inv.bf4_node(tray, i)
        # BF4 DPUs sit on their own management network; reachability is the
        # only live signal we have for them. Pinged here rather than in a
        # second pass so a row's addresses and their state can never be
        # paired up out of order.
        return {
            'tray': tray,
            'node': i,
            'name': name,
            'os_ip': os_ip,
            'bmc_ip': bmc_ip,
            'bf4_os_ip': bf4_os,
            'bf4_bmc_ip': bf4_bmc,
            'os_hostname': inv.node_hostname(tray, i),
            # Stored, not probed: a MAC only changes when hardware does, and
            # reading it costs an SSH session per node.
            **inv.node_macs(tray, i),
            'os_up': probe.ping(os_ip),
            'bmc_up': probe.ping(bmc_ip),
            'bf4_os_up': probe.ping(bf4_os) if bf4_os else False,
            'bf4_bmc_up': probe.ping(bf4_bmc) if bf4_bmc else False,
        }

    recs = list(inv.nodes())
    # 227 probes per poll; the old 32 workers needed seven serial batches.
    with ThreadPoolExecutor(max_workers=64) as ex:
        node_rows = list(ex.map(node_status, recs))
        # BMC PowerState is a second round trip; only ask reachable BMCs.
        reachable = [r for r in node_rows if r['bmc_up']]
        states = dict(zip(
            [r['name'] for r in reachable],
            ex.map(lambda r: probe.power_state(r['bmc_ip']), reachable),
        ))
        shelf_ips = list(inv.POWER_SHELVES.items())
        shelf_ping = list(ex.map(lambda kv: probe.ping(kv[1]), shelf_ips))

    for row in node_rows:
        row['power'] = states.get(row['name'], 'Unknown')

    trays = {}
    for row in node_rows:
        trays.setdefault(row['tray'], []).append(row)

    shelves = [
        {'shelf': num, 'ip': ip, 'up': up}
        for (num, ip), up in zip(shelf_ips, shelf_ping)
    ]
    return {
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'trays': [{'tray': t, 'tray_name': inv.tray_name(t),
                   'nodes': sorted(rows, key=lambda r: r['node'])}
                  for t, rows in sorted(trays.items())],
        'shelves': shelves,
        'summary': {
            'nodes': len(node_rows),
            'os_up': sum(1 for r in node_rows if r['os_up']),
            'bmc_up': sum(1 for r in node_rows if r['bmc_up']),
            'powered_on': sum(1 for r in node_rows if r['power'] == 'On'),
        },
    }


# A poll costs ~230 probes, so several open tabs would multiply it. The page
# refreshes on its own every 20 s, which is well inside this window, so serving
# a shared snapshot costs no perceptible freshness.
STATUS_TTL = 5
_status_lock = threading.Lock()
_status_cache = {'at': 0.0, 'data': None}


def collect_status():
    """Cached snapshot; concurrent callers wait for one collection, not N."""
    with _status_lock:
        if _status_cache['data'] and time.time() - _status_cache['at'] < STATUS_TTL:
            return _status_cache['data']
        data = _collect_status_uncached()
        _status_cache['at'] = time.time()
        _status_cache['data'] = data
        return data


CLASSED_PATH = {"path": "index.html", "content": "text/html; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    server_version = 'rackd'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (time.strftime('%H:%M:%S'),
                                        fmt % args))

    # -- helpers ----------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except ValueError:
            return {}

    def _static(self, path):
        """Serve files from ./static only; the resolved path must stay inside."""
        target = (HERE / path.lstrip('/')).resolve()
        if not target.is_relative_to(HERE / 'static') or not target.is_file():
            return self._json({'error': 'not found'}, 404)
        ctype = {
            '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg',
            '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        }.get(target.suffix.lower(), 'application/octet-stream')
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=3600')
        self.end_headers()
        self.wfile.write(data)

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        if url.path in ('/', '/index.html'):
            try:
                data = HTML.read_bytes()
            except OSError:
                return self._json({'error': 'index.html missing'}, 500)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
            return
        if url.path == '/api/status':
            return self._json(collect_status())
        if url.path.startswith('/static/'):
            return self._static(url.path)
        if url.path == '/api/flash/packages':
            return self._json({'ok': True, 'packages': flash.list_packages(),
                               'dir': str(flash.FW_DIR)})
        if url.path == '/api/inventory':
            detail = []
            for tray in sorted(inv.TRAYS):
                rec = inv.TRAYS[tray]
                detail.append({
                    'tray': tray, 'name': rec['name'],
                    'nodes': [{'slot': n.get('slot'), 'os_ip': n['os_ip'],
                               'bmc_ip': n['bmc_ip'],
                               'bf4_os_ip': inv.bf4_node(tray, i)[0],
                               'bf4_bmc_ip': inv.bf4_node(tray, i)[1],
                               'os_hostname': n.get('os_hostname', '')}
                              for i, n in enumerate(rec['nodes'])],
                })
            return self._json({
                'trays': sorted(inv.TRAYS),
                'shelves': inv.POWER_SHELVES,
                'detail': detail,
            })
        if url.path == '/api/term/probe':
            name = qs.get('node', [''])[0]
            seen = []
            for kind in rack_term.KINDS:
                try:
                    host, label, _u, _p = rack_term._resolve(name, kind)
                except rack_term.SessionError as exc:
                    seen.append({'kind': kind, 'ok': False, 'error': str(exc)})
                    continue
                seen.append({'kind': kind, 'host': host, 'label': label,
                             'ok': rack_term.reachable(host)})
            return self._json({'ok': True, 'targets': seen})

        if url.path.startswith('/api/term/') and url.path.endswith('/stream'):
            sid = url.path[len('/api/term/'):-len('/stream')]
            after = int(qs.get('after', ['0'])[0] or 0)
            return self._term_stream(sid, after)

        return self._json({'error': 'not found'}, 404)

    def _term_stream(self, sid, after):
        """Long-poll: hold the request until output arrives or POLL_HOLD ends."""
        try:
            session = rack_term.REGISTRY.get(sid)
        except rack_term.SessionError as exc:
            return self._json({'ok': False, 'closed': True, 'error': str(exc)}, 404)
        data, offset = session.read(after)
        payload = {
            'ok': True,
            'data': base64.b64encode(data).decode('ascii'),
            'offset': offset,
            'closed': not session.alive,
            'reason': session.closed_reason,
        }
        self._json(payload)

    def do_POST(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        body = self._body()
        dry = qs.get('dry_run', ['0'])[0] in ('1', 'true')
        token = qs.get('token', [''])[0]

        if url.path == '/api/term/open':
            try:
                session = rack_term.REGISTRY.open(body.get('node') or '',
                                                  body.get('kind') or '')
            except rack_term.SessionError as exc:
                return self._json({'ok': False, 'error': str(exc)}, 400)
            return self._json({'ok': True, 'sid': session.sid,
                               'label': session.label, 'host': session.host,
                               'user': session.user,
                               'live': rack_term.REGISTRY.live_count()})

        if url.path.startswith('/api/term/'):
            rest = url.path[len('/api/term/'):]
            sid, _, what = rest.partition('/')
            try:
                session = rack_term.REGISTRY.get(sid)
            except rack_term.SessionError as exc:
                return self._json({'ok': False, 'error': str(exc)}, 404)
            if what == 'input':
                raw = body.get('data') or ''
                try:
                    session.write(base64.b64decode(raw))
                except (ValueError, binascii.Error):
                    return self._json({'ok': False, 'error': 'bad payload'}, 400)
                return self._json({'ok': True})
            if what == 'resize':
                session.resize(body.get('cols'), body.get('rows'))
                return self._json({'ok': True})
            if what == 'close':
                rack_term.REGISTRY.close(sid)
                return self._json({'ok': True})
            return self._json({'error': 'not found'}, 404)

        if url.path == '/api/node/power':
            name, action = body.get('node'), body.get('action')
            if not inv.lookup_node(name or ''):
                return self._json({'ok': False, 'error': 'unknown node'}, 400)
            payload = {'kind': 'node', 'node': name, 'action': action}
            return self._dispatch(payload, dry, token,
                                  lambda: probe.node_action(name, action))

        if url.path == '/api/tray/power':
            tray, action = body.get('tray'), body.get('action')
            if tray not in inv.TRAYS:
                return self._json({'ok': False, 'error': 'unknown tray'}, 400)
            payload = {'kind': 'tray', 'tray': tray, 'action': action}
            return self._dispatch(payload, dry, token,
                                  lambda: {'ok': True,
                                           'results': probe.tray_action(tray, action)})

        if url.path == '/api/shelf/power':
            shelf, action = body.get('shelf'), body.get('action')
            if shelf not in inv.POWER_SHELVES:
                return self._json({'ok': False, 'error': 'unknown power shelf'}, 400)
            if action == 'status':
                # Read-only, so it must not require a confirmation token.
                return self._json(probe.shelf_action(shelf, action))
            payload = {'kind': 'shelf', 'shelf': shelf, 'action': action}
            return self._dispatch(payload, dry, token,
                                  lambda: probe.shelf_action(shelf, action))

        if url.path == '/api/tray/probe':
            # Preview only: SSH each OS to read its real hostname so the
            # operator can confirm the mapping before anything is stored.
            parsed, err = _parse_tray_body(body)
            if err:
                return self._json({'ok': False, 'error': err}, 400)
            return self._json({'ok': True, 'nodes': _probe_nodes(parsed)})

        if url.path == '/api/tray/add':
            parsed, err = _parse_tray_body(body)
            if err:
                return self._json({'ok': False, 'error': err}, 400)
            tray = parsed['tray']
            if tray is None:
                tray = inv.next_tray_index()
            if not body.get('force') and tray in inv.TRAYS:
                return self._json({'ok': False,
                                   'error': f'tray {tray} already exists '
                                            f'({inv.tray_name(tray)}); '
                                            f'pass force=1 to replace it'}, 409)
            probed = _probe_nodes(parsed)
            nodes = []
            for i, nb in enumerate(probed):
                nodes.append({
                    'slot': i + 1,
                    'os_ip': nb['os_ip'], 'bmc_ip': nb['bmc_ip'],
                    'os_user': nb['os_user'], 'os_pass': nb['os_pass'],
                    'bmc_user': nb['bmc_user'], 'bmc_pass': nb['bmc_pass'],
                    'os_hostname': nb['os_hostname'] or '',
                    'bmc_hostname': '',
                })
            rec = inv.add_tray(tray, parsed['name'], nodes)
            return self._json({'ok': True, 'tray': tray, 'name': rec['name'],
                               'nodes': probed})

        if url.path == '/api/tray/remove':
            tray = body.get('tray')
            if not isinstance(tray, int) or tray not in inv.TRAYS:
                return self._json({'ok': False, 'error': 'unknown tray'}, 400)
            inv.remove_tray(tray)
            return self._json({'ok': True, 'removed': tray})

        if url.path == '/api/macs/refresh':
            # One SSH login per node (OS), one more for its DPU, one Redfish
            # walk per BMC. Kept off the poll path for exactly that reason.
            return self._json(_refresh_macs(body.get('only') or None))

        if url.path == '/api/hostnames/refresh':
            # Re-read every node's hostname over SSH and store it, so the
            # overview can show what the OS actually calls itself next to the
            # configured IP. Nodes that do not answer keep their old value.
            return self._json(_refresh_hostnames())

        # ---- firmware flash -------------------------------------------------
        if url.path == '/api/flash/upload':
            name = body.get('name') or ''
            data = body.get('data_b64') or ''
            try:
                raw = base64.b64decode(data, validate=True)
            except (ValueError, binascii.Error):
                return self._json({'ok': False, 'error': 'invalid base64 payload'}, 400)
            path, err = flash.save_package(name, raw)
            if err:
                return self._json({'ok': False, 'error': err}, 400)
            kind, conf, reasons = flash.classify(path.name, path.stat().st_size)
            return self._json({'ok': True, 'name': path.name,
                               'size': path.stat().st_size,
                               'kind': kind, 'confidence': conf,
                               'reasons': reasons})

        if url.path == '/api/flash/job/delete':
            ok, err = flash.delete_job(body.get('id') or '')
            if not ok:
                return self._json({'ok': False, 'error': err}, 409)
            return self._json({'ok': True, 'jobs': flash.list_jobs()})

        if url.path == '/api/flash/jobs/clear':
            removed, kept = flash.clear_jobs()
            return self._json({'ok': True, 'removed': removed, 'kept': kept,
                               'jobs': flash.list_jobs()})

        if url.path == '/api/flash/delete':
            if flash.delete_package(body.get('name') or ''):
                return self._json({'ok': True})
            return self._json({'ok': False, 'error': 'package not found'}, 404)

        if url.path == '/api/flash/run':
            kind = body.get('kind')
            pkg = body.get('package')
            targets = body.get('targets')
            if kind not in flash.RECIPES:
                return self._json({'ok': False, 'error': f'unknown kind {kind}'}, 400)
            pkg_path = flash.safe_package_path(pkg or '')
            if not pkg_path:
                return self._json({'ok': False, 'error': 'package not found'}, 400)
            # Skip the mismatch check only when the operator explicitly
            # acknowledged the warning in the upload / run dialog.
            if not body.get('ack_mismatch'):
                k, conf, _ = flash.classify(pkg_path.name, pkg_path.stat().st_size)
                if k and k != kind:
                    return self._json({'ok': False, 'error':
                        f'檔案 {pkg_path.name} 判定為 {k.upper()}，與所選的 '
                        f'{kind.upper()} 不符（若確定要刷，請在確認視窗勾選）',
                        'mismatch': {'detected': k, 'chosen': kind}}, 409)
            nodes, terr = _resolve_targets(targets)
            if terr:
                return self._json({'ok': False, 'error': terr}, 400)
            if not nodes:
                return self._json({'ok': False, 'error': 'no nodes selected'}, 400)
            payload = {'kind': kind, 'package': pkg, 'targets': targets,
                       'nodes': nodes}
            if dry:
                return self._json({'dry_run': True, 'payload': payload,
                                   'nodes': nodes,
                                   'token': _issue_token(payload),
                                   'ttl': TOKEN_TTL})
            if not _consume_token(token, payload):
                return self._json({'ok': False,
                                   'error': 'confirmation token missing or expired; '
                                            'request it with dry_run=1 first'}, 409)
            if not _ACTION_LOCK.acquire(blocking=False):
                return self._json({'ok': False,
                                   'error': 'another power/flash action is running'}, 429)
            try:
                job = flash.start_job(kind, pkg, nodes, dry_run=False)
            finally:
                _ACTION_LOCK.release()
            return self._json({'ok': True, 'job': job})

        if url.path == '/api/flash/dryrun':
            # Preview which nodes would be touched, without a token.
            kind = body.get('kind')
            if kind not in flash.RECIPES:
                return self._json({'ok': False, 'error': f'unknown kind {kind}'}, 400)
            nodes, terr = _resolve_targets(body.get('targets'))
            if terr:
                return self._json({'ok': False, 'error': terr}, 400)
            recipe = flash.RECIPES[kind]
            pkg = body.get('package') or ''
            pkg_path = flash.safe_package_path(pkg) if pkg else None
            pkg_kind, pkg_conf, pkg_why = (
                flash.classify(pkg_path.name, pkg_path.stat().st_size)
                if pkg_path else (None, None, []))
            plan = []
            for n in nodes:
                rec = inv.lookup_node(n) or {}
                plan.append({'node': n, 'bmc_ip': rec.get('bmc_ip'),
                             'version': flash.read_version(
                                 rec.get('bmc_ip'), kind,
                                 rec.get('bmc_user'), rec.get('bmc_pass'))
                             if rec.get('bmc_ip') else None})
            return self._json({'ok': True, 'kind': kind,
                               'label': recipe['label'],
                               'targets': recipe['targets'],
                               'after': recipe['after'],
                               'after_label': flash.AFTER_LABEL[recipe['after']],
                               'post_note': recipe['post_note'],
                               'package': pkg,
                               'package_kind': pkg_kind,
                               'package_confidence': pkg_conf,
                               'package_reasons': pkg_why,
                               'package_mismatch': bool(pkg_kind and pkg_kind != kind),
                               'nodes': plan})

        if url.path == '/api/node/hw':
            # Read-only: runs Config-Neutrino.sh on the node and parses it.
            name = body.get('node')
            if not name:
                return self._json({'ok': False, 'error': 'node required'}, 400)
            return self._json(rack_hw.read_node(name))

        if url.path == '/api/flash/job':
            job = flash.get_job(body.get('id') or '')
            if not job:
                return self._json({'ok': False, 'error': 'unknown job'}, 404)
            return self._json({'ok': True, 'job': job})

        if url.path == '/api/flash/jobs':
            return self._json({'ok': True, 'jobs': flash.list_jobs()})

        if url.path == '/api/flash/probe':
            job = flash.get_job(body.get('id') or '')
            if not job:
                return self._json({'ok': False, 'error': 'unknown job'}, 404)
            rows = flash.probe_running(job['id'], body.get('nodes') or [],
                                       job['kind'])
            return self._json({'ok': True, 'nodes': rows})

        if url.path == '/api/flash/restarted':
            job = flash.mark_restarted(body.get('id') or '',
                                       body.get('nodes') or [],
                                       body.get('action'))
            if not job:
                return self._json({'ok': False, 'error': 'unknown job'}, 404)
            return self._json({'ok': True, 'job': job})

        if url.path == '/api/flash/activated':
            job = flash.mark_activated(body.get('id') or '', body.get('versions') or {})
            if not job:
                return self._json({'ok': False, 'error': 'unknown job'}, 404)
            return self._json({'ok': True, 'job': job})

        return self._json({'error': 'not found'}, 404)

    def _dispatch(self, payload, dry, token, run):
        if dry:
            return self._json({'dry_run': True, 'payload': payload,
                               'token': _issue_token(payload),
                               'ttl': TOKEN_TTL})
        if not _consume_token(token, payload):
            return self._json({'ok': False,
                               'error': 'confirmation token missing or expired; '
                                        'request it with dry_run=1 first'}, 409)
        if not _ACTION_LOCK.acquire(blocking=False):
            return self._json({'ok': False, 'error': 'another power action is in progress'}, 429)
        try:
            return self._json(run())
        finally:
            _ACTION_LOCK.release()


# ------------------------------------------------------------- add tray ---

IP_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')


def _valid_ip(value):
    if not isinstance(value, str) or not IP_RE.match(value):
        return False
    return all(0 <= int(part) <= 255 for part in value.split('.'))


def _parse_tray_body(body):
    """Validate the Add Tray payload. Returns (parsed, error_message)."""
    if not isinstance(body, dict):
        return None, 'body must be a JSON object'
    tray = body.get('tray')
    if tray is not None and not (isinstance(tray, int) and tray >= 0):
        return None, 'tray must be a non-negative integer'
    name = (body.get('name') or '').strip()
    if not name:
        name = f'Neutrino{tray if tray is not None else inv.next_tray_index()}'
    if not re.match(r'^[A-Za-z0-9._-]{1,48}$', name):
        return None, 'name may only contain letters, digits, dot, dash, underscore'

    raw_nodes = body.get('nodes')
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return None, 'nodes must be a non-empty list'
    if len(raw_nodes) > inv.INVENTORY_UNITS:
        return None, f'at most {inv.INVENTORY_UNITS} nodes per tray'

    nodes, seen = [], set()
    for i, n in enumerate(raw_nodes):
        if not isinstance(n, dict):
            return None, f'node {i}: must be an object'
        os_ip = (n.get('os_ip') or '').strip()
        bmc_ip = (n.get('bmc_ip') or '').strip()
        if not _valid_ip(os_ip):
            return None, f'node {i}: invalid os_ip {os_ip!r}'
        if not _valid_ip(bmc_ip):
            return None, f'node {i}: invalid bmc_ip {bmc_ip!r}'
        for ip in (os_ip, bmc_ip):
            if ip in seen:
                return None, f'duplicate ip {ip} within the tray'
            seen.add(ip)
        nodes.append({
            'os_ip': os_ip, 'bmc_ip': bmc_ip,
            'os_user': (n.get('os_user') or inv.OS_USER).strip(),
            'os_pass': n.get('os_pass') if n.get('os_pass') is not None else inv.OS_PASS,
            'bmc_user': (n.get('bmc_user') or inv.BMC_USER).strip(),
            'bmc_pass': n.get('bmc_pass') if n.get('bmc_pass') is not None else inv.BMC_PASS,
        })
    return {'tray': tray, 'name': name, 'nodes': nodes}, None


def _probe_nodes(parsed):
    """SSH every OS for its hostname and check each BMC, in parallel."""
    def one(n):
        host, why = probe.probe_os_hostname(n['os_ip'], n['os_user'], n['os_pass'])
        power, bmc_why = probe.probe_bmc(n['bmc_ip'], n['bmc_user'], n['bmc_pass'])
        return dict(n, os_hostname=host, os_detail=why,
                    bmc_power=power, bmc_detail=bmc_why)

    nodes = parsed['nodes']
    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        return list(pool.map(one, nodes))


def _refresh_macs(only=None):
    """Read the MACs behind each configured address and store them.

    Runs off the poll path: every node costs an SSH login (OS, plus one for its
    DPU) and a Redfish walk (BMC), and a MAC only changes when hardware does.
    """
    recs = [r for r in inv.nodes() if not only or r[2] == only]

    def one(rec):
        tray, i, name, os_ip, bmc_ip = rec
        node = inv.lookup_node(name) or {}
        macs = {}
        bf4_os, bf4_bmc = inv.bf4_node(tray, i)
        if bf4_os:
            # DPU OS login, not the host's: the BF4 runs its own image.
            mac, _ = probe.probe_os_mac(bf4_os, inv.BF4_OS_USER, inv.BF4_OS_PASS)
            macs['bf4_os_mac'] = mac or ''
        if bf4_bmc:
            # Straight into the BMC over ssh, as the values it reports for
            # itself; no detour through the DPU OS.
            mac, _ = probe.probe_bf4_bmc_mac(
                bf4_bmc, inv.BF4_BMC_USER, inv.BF4_BMC_PASS)
            macs['bf4_bmc_mac'] = mac or ''
        if os_ip:
            mac, os_why = probe.probe_os_mac(
                os_ip, node.get('os_user') or inv.OS_USER,
                node.get('os_pass') or inv.OS_PASS)
            macs['os_mac'] = mac or ''
        else:
            os_why = 'no ip'
        if bmc_ip:
            mac, _ = probe.probe_bmc_mac(
                bmc_ip, node.get('bmc_user') or inv.BMC_USER,
                node.get('bmc_pass') or inv.BMC_PASS,
                fallback_mac=probe.arp_mac(bmc_ip))
            macs['bmc_mac'] = mac or ''
        if macs:
            inv.set_node_macs(tray, i, macs)
        return {'node': name, 'os_mac': macs.get('os_mac', ''),
                'bmc_mac': macs.get('bmc_mac', ''),
                'bf4_os_mac': macs.get('bf4_os_mac', ''),
                'bf4_bmc_mac': macs.get('bf4_bmc_mac', ''), 'detail': os_why}

    with ThreadPoolExecutor(max_workers=16) as pool:
        rows = list(pool.map(one, recs))
    return {
        'ok': True, 'total': len(rows),
        'os_mac': sum(1 for r in rows if r['os_mac']),
        'bmc_mac': sum(1 for r in rows if r['bmc_mac']),
        'bf4_os_mac': sum(1 for r in rows if r['bf4_os_mac']),
        'bf4_bmc_mac': sum(1 for r in rows if r['bf4_bmc_mac']),
        'nodes': rows,
    }


def _refresh_hostnames():
    """Re-probe every configured node and store the hostnames that answered."""
    recs = list(inv.nodes())

    def one(rec):
        tray, i, name, os_ip, _bmc_ip = rec
        node = inv.lookup_node(name) or {}
        host, why = probe.probe_os_hostname(
            os_ip, node.get('os_user') or inv.OS_USER,
            node.get('os_pass') or inv.OS_PASS)
        return tray, i, name, host, why

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(one, recs))

    rows = []
    updated = 0
    for tray, i, name, host, why in results:
        stored = bool(host) and inv.set_node_hostname(tray, i, host)
        if stored:
            updated += 1
        rows.append({'node': name, 'os_hostname': host or '',
                     'detail': why, 'saved': stored})
    return {'ok': True, 'updated': updated, 'total': len(rows), 'nodes': rows}


# ---------------------------------------------------------------- targets ---

def _resolve_targets(targets):
    """Expand a target spec into a de-duplicated node name list.

    Accepted shapes:
      {'mode':'all'}
      {'mode':'tray','trays':[0,3]}
      {'mode':'node','nodes':['Neutrino1-n2', ...]}
    """
    if not isinstance(targets, dict):
        return [], 'targets must be an object'
    mode = targets.get('mode')
    out, seen = [], set()

    def add(name):
        if name in seen:
            return None
        rec = inv.lookup_node(name)
        if not rec:
            return f'unknown node {name}'
        seen.add(name)
        out.append(name)
        return None

    if mode == 'all':
        for _t, _i, name, _o, _b in inv.nodes():
            add(name)
        return out, None

    if mode == 'tray':
        trays = targets.get('trays')
        if not isinstance(trays, list) or not trays:
            return [], 'trays must be a non-empty list'
        for tray in trays:
            if not isinstance(tray, int) or tray not in inv.TRAYS:
                return [], f'unknown tray {tray}'
            rec = inv.TRAYS[tray]
            for i in range(len(rec['nodes'])):
                err = add(f'{rec["name"]}-n{i}')
                if err:
                    return [], err
        return out, None

    if mode == 'node':
        names = targets.get('nodes')
        if not isinstance(names, list) or not names:
            return [], 'nodes must be a non-empty list'
        for name in names:
            if not isinstance(name, str):
                return [], 'node names must be strings'
            err = add(name)
            if err:
                return [], err
        return out, None

    return [], f'unknown mode {mode!r}'


def _ssl_context(cert, key):
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


def _run_server(host, port, handler, cert=None, key=None):
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    if cert and key:
        server.socket = _ssl_context(cert, key).wrap_socket(
            server.socket, server_side=True)
    return server


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', type=int, default=7070)
    p.add_argument('--tls-port', type=int, default=None,
                   help='serve the same app over TLS on this port as well')
    p.add_argument('--cert', default=str(HERE / 'tls' / 'cert.pem'))
    p.add_argument('--key', default=str(HERE / 'tls' / 'key.pem'))
    args = p.parse_args(argv)

    if not HTML.is_file():
        print(f'ERROR: {HTML} is missing', file=sys.stderr)
        return 2

    cert, key = Path(args.cert), Path(args.key)
    have_tls = args.tls_port and cert.is_file() and key.is_file()
    if args.tls_port and not have_tls:
        print(f'WARNING: --tls-port given but {cert} / {key} missing; '
              f'run scripts/make_cert.sh to create them', file=sys.stderr)

    flash.load_jobs()          # re-attach to flashes that outlived the last run
    server = _run_server(args.host, args.port, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f'Rack Manager on http://{args.host}:{args.port}  '
          f'({len(list(inv.nodes()))} nodes, {len(inv.POWER_SHELVES)} power shelves)',
          flush=True)
    if have_tls:
        tls = _run_server(args.host, args.tls_port, Handler, cert, key)
        threading.Thread(target=tls.serve_forever, daemon=True).start()
        print(f'Rack Manager on https://{args.host}:{args.tls_port}  '
              f'(TLS, clipboard API available)', flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print('\nstopped', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
