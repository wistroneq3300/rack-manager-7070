"""Bounded probes and power actions for the Neutrino rack.

Read paths (ping, Redfish PowerState, shelf state) never mutate hardware.
Power paths are only ever reached through rackd.py, which gates them behind an
operator confirmation.
"""
from __future__ import annotations

import json
import re
import subprocess
import time

import inventory as inv

PING_TIMEOUT = 1
CMD_TIMEOUT = 40
REDFISH_TIMEOUT = 8


def _run(argv, timeout=CMD_TIMEOUT):
    """Run a command without a shell; credentials never enter argv."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           check=False)
        return p.returncode, (p.stdout or '') + (p.stderr or '')
    except subprocess.TimeoutExpired:
        return 124, 'timeout'
    except OSError as exc:
        return 127, str(exc)


def ping(ip):
    if not ip:
        return False
    rc, _ = _run(['ping', '-c', '1', '-W', str(PING_TIMEOUT), ip],
                 timeout=PING_TIMEOUT + 3)
    return rc == 0


# ---------------------------------------------------------------- Redfish ---

def _redfish(ip, path, method='GET', timeout=REDFISH_TIMEOUT, user=None, password=None,
             payload=None):
    """LiteOn/AMI BMC Redfish call. Returns (http_code, parsed_or_text)."""
    cred = f'{user or inv.BMC_USER}:{password or inv.BMC_PASS}'
    argv = ['curl', '-sk', '--max-time', str(timeout),
            '-u', cred,
            '-H', 'Content-Type: application/json',
            '-w', '\n__HTTP__:%{http_code}', '-X', method, f'https://{ip}{path}']
    if method == 'POST':
        argv += ['-d', json.dumps(payload if payload is not None else {})]
    rc, out = _run(argv, timeout=timeout + 5)
    if rc != 0:
        return 0, out.strip()
    body, _, code = out.rpartition('\n__HTTP__:')
    try:
        code_i = int(code.strip())
    except ValueError:
        code_i = 0
    try:
        return code_i, json.loads(body)
    except ValueError:
        return code_i, body


def power_state(bmc_ip):
    """BMC-reported chassis power state: On / Off / Unknown."""
    code, data = _redfish(bmc_ip, '/redfish/v1/Systems/System_0')
    if code == 200 and isinstance(data, dict):
        return data.get('PowerState') or 'Unknown'
    return 'Unknown'


def shelf_units(shelf_ip):
    """Per-unit state for a LiteOn power shelf (powerdevice0..5).

    LiteOn reports "Standby" while the unit is actively supplying the rack, so
    the raw state alone reads like a fault. Output watts are included to make
    that unambiguous.
    """
    base = ('/redfish/v1/Chassis/chassis/Power/Oem/LiteOn/PowerUnits'
            '/powerdevice')
    units = []
    for i in range(inv.SHELF_UNITS):
        code, data = _redfish(shelf_ip, f'{base}{i}')
        state, watts, volts = '?', None, None
        if code == 200 and isinstance(data, dict):
            state = data.get('state') or '?'
            pout = data.get('pout')
            if isinstance(pout, dict):
                watts = pout.get('Value')
            vout = data.get('vout')
            if isinstance(vout, dict):
                volts = vout.get('Value')
        units.append({'unit': i, 'state': state, 'watts': watts, 'volts': volts})
    return units


def probe_os_hostname(ip, user, password, timeout=15):
    """Read a node's hostname over SSH. Returns (hostname|None, detail)."""
    if not ip:
        return None, 'no ip'
    rc, out = _ssh(ip, user, password,
                   'hostname 2>/dev/null || cat /proc/sys/kernel/hostname',
                   timeout=timeout)
    text = (out or '').strip()
    if rc != 0:
        low = text.lower()
        if 'timed out' in low or 'timeout' in low:
            return None, 'timeout'
        if 'permission denied' in low or 'authentication' in low:
            return None, 'auth failed'
        if 'connection refused' in low or 'no route' in low:
            return None, 'unreachable'
        return None, text.splitlines()[0][:80] if text else f'exit {rc}'
    name = text.splitlines()[0].strip() if text else ''
    return (name or None), ('ok' if name else 'empty hostname')


def probe_bmc(bmc_ip, user, password, timeout=REDFISH_TIMEOUT):
    """Confirm BMC Redfish answers and report its power state."""
    if not bmc_ip:
        return None, 'no ip'
    code, data = _redfish(bmc_ip, '/redfish/v1/Systems/System_0',
                          timeout=timeout, user=user, password=password)
    if code == 200 and isinstance(data, dict):
        return data.get('PowerState') or 'Unknown', 'ok'
    if code == 401:
        return None, 'auth failed'
    if code == 0:
        return None, 'unreachable'
    return None, f'http {code}'


# ------------------------------------------------------------ power paths ---

# Commands mirror vera_cycle/engine/cycle_engine.py:1536-1552. Outband
# power_cycle deliberately uses the BMC helper rather than IPMI-over-LAN.
SHELF_ON = ('/redfish/v1/Chassis/chassis/Power/Oem/LiteOn/PowerUnits'
            '/Actions/LiteOn.PowerOn')
SHELF_OFF = ('/redfish/v1/Chassis/chassis/Power/Oem/LiteOn/PowerUnits'
             '/Actions/LiteOn.PowerOff')


def _ssh(host, user, password, command, timeout=CMD_TIMEOUT):
    argv = ['sshpass', '-p', password, 'ssh',
            '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR', '-o', 'ConnectTimeout=10',
            '-o', 'PreferredAuthentications=password',
            '-o', 'PubkeyAuthentication=no',
            f'{user}@{host}', command]
    return _run(argv, timeout=timeout)


# The management port is the interface that carries the node's own OS IP.
# Picking by link state would land on a fabric port that happens to be up.
_OS_MAC_CMD = ("a=$(ip -o -4 addr show scope global 2>/dev/null | "
               "awk -v ip=%s '$4 ~ \"^\"ip\"/\" {print $2; exit}'); "
               "if [ -z \"$a\" ]; then a=$(ip -o route get 10.36.72.1 2>/dev/null | "
               "sed -n 's/.* dev \\([^ ]*\\).*/\\1/p'); fi; "
               "[ -n \"$a\" ] && ip -o link show dev \"$a\" 2>/dev/null | "
               "sed -n 's/.*link\\/ether \\([0-9a-f:]\\{17\\}\\).*/\\1/p'")

_MAC_RE = re.compile(r'([0-9a-f]{2}:){5}[0-9a-f]{2}')


def _ssh_why(rc, text):
    """Short reason for a failed ssh, from its own stderr."""
    low = (text or '').lower()
    if 'timed out' in low or 'timeout' in low:
        return 'timeout'
    if 'permission denied' in low or 'authentication' in low:
        return 'auth failed'
    if 'connection refused' in low or 'no route' in low:
        return 'unreachable'
    first = (text or '').strip().splitlines()
    return first[0][:80] if first else f'exit {rc}'


def probe_os_mac(ip, user, def_pw, timeout=15):
    """MAC of the NIC carrying the node's OS IP. Returns (mac|None, detail)."""
    if not ip:
        return None, 'no ip'
    rc, out = _ssh(ip, user, def_pw, _OS_MAC_CMD % ip, timeout=timeout)
    text = (out or '').strip()
    if rc != 0:
        return None, _ssh_why(rc, text)
    mac = next((ln.strip().lower() for ln in text.splitlines()
                if _MAC_RE.fullmatch(ln.strip())), '')
    return (mac or None), ('ok' if mac else 'os ip not on any local nic')


def arp_mac(ip):
    """MAC this host has learned for an IP, or None."""
    if not ip:
        return None
    rc, out = _run(['ip', 'neigh', 'show', ip], timeout=5)
    m = _MAC_RE.search((out or '').lower())
    return m.group(0) if m else None


# A DPU's BMC is its own small Linux: log into it as 'service' and read the
# management NIC directly. /sbin is not on the login PATH there, so `ip` has to
# be spelled out. eth0 is the management port; ecm-plat-bmc is an internal
# platform link and vlan4040 sits on top of eth0.
_BF4_BMC_MAC_CMD = ("/sbin/ip -o -4 addr show 2>/dev/null | "
                    "awk -v ip=%s '$4 ~ \"^\"ip\"/\" {print $2; exit}'")


def probe_bf4_bmc_mac(bmc_ip, user, pw, timeout=20):
    """MAC of a DPU BMC's management NIC, read on the BMC itself."""
    if not bmc_ip:
        return None, 'no ip'
    rc, out = _ssh(bmc_ip, user, pw, _BF4_BMC_MAC_CMD % bmc_ip, timeout=timeout)
    text = (out or '').strip()
    if rc != 0:
        return None, _ssh_why(rc, text)
    dev = text.splitlines()[0].strip() if text else ''
    if not dev:
        return None, 'bmc ip not on any local nic'
    rc, out = _ssh(bmc_ip, user, pw, f'/sbin/ip -o link show dev {dev}',
                   timeout=timeout)
    if rc != 0:
        return None, _ssh_why(rc, out or '')
    m = re.search(r'link/ether ([0-9a-f:]{17})', (out or '').lower())
    return (m.group(1) if m else None), ('ok' if m else f'no mac on {dev}')


def probe_bmc_mac(bmc_ip, user, pw, timeout=REDFISH_TIMEOUT, fallback_mac=None):
    """MAC of the NIC that actually answers at bmc_ip.

    Matched on the management interface's own IPv4 address: these BMCs expose
    several, and the first one listed is not always the one on the rack
    network. Older builds expose no manager entry at all, so the caller may
    pass the ARP MAC it just learned, kept only when the BMC answers at all.
    """
    if not bmc_ip:
        return None, 'no ip'
    for base in ('/redfish/v1/Managers', '/redfish/v1/Managers/Self'):
        code, data = _redfish(bmc_ip, base, timeout=timeout, user=user, password=pw)
        if code != 200 or not isinstance(data, dict):
            continue
        members = [data['@odata.id']] if data.get('@odata.id') else []
        members += [m.get('@odata.id') for m in (data.get('Members') or [])
                    if isinstance(m, dict)]
        for mgr_url in filter(None, members):
            c2, lst = _redfish(bmc_ip, f'{mgr_url}/EthernetInterfaces',
                               timeout=timeout, user=user, password=pw)
            if c2 != 200 or not isinstance(lst, dict):
                continue
            first = None
            for m in (lst.get('Members') or []):
                url = m.get('@odata.id') if isinstance(m, dict) else None
                if not url:
                    continue
                c3, itf = _redfish(bmc_ip, url, timeout=timeout, user=user, password=pw)
                if c3 != 200 or not isinstance(itf, dict) or not itf.get('MACAddress'):
                    continue
                mac = itf['MACAddress'].lower()
                first = first or mac
                if any((a.get('Address') or '') == bmc_ip
                       for a in (itf.get('IPv4Addresses') or [])):
                    return mac, 'ok'
            if first:
                return first, 'ok'
    if fallback_mac:
        return fallback_mac.lower(), 'arp'
    return None, 'no mac'


def node_action(name, action):
    """Issue a power action on one node.

    Returns a dict describing what was sent, never a claim of success beyond
    the transport result.
    """
    rec = inv.lookup_node(name)
    if not rec:
        return {'ok': False, 'error': f'unknown node {name}'}
    os_ip, bmc_ip = rec['os_ip'], rec['bmc_ip']
    os_user = rec.get('os_user') or inv.OS_USER
    os_pass = rec.get('os_pass') or inv.OS_PASS
    bmc_user = rec.get('bmc_user') or inv.BMC_USER
    bmc_pass = rec.get('bmc_pass') or inv.BMC_PASS
    started = time.time()

    if action == 'aux_cycle':
        rc, out = _ssh(bmc_ip, bmc_user, bmc_pass,
                       '/usr/bin/stbypowerctrl.sh aux_cycle')
        sent = '/usr/bin/stbypowerctrl.sh aux_cycle'
        via = 'bmc ssh'
    elif action == 'power_cycle':
        rc, out = _ssh(bmc_ip, bmc_user, bmc_pass,
                       '/usr/bin/powerctrl.sh power_cycle')
        sent = '/usr/bin/powerctrl.sh power_cycle'
        via = 'bmc ssh'
    elif action == 'power_on':
        rc, out = _ssh(bmc_ip, bmc_user, bmc_pass,
                       '/usr/bin/powerctrl.sh power_on')
        sent = '/usr/bin/powerctrl.sh power_on'
        via = 'bmc ssh'
    elif action == 'power_off':
        rc, out = _ssh(bmc_ip, bmc_user, bmc_pass,
                       '/usr/bin/powerctrl.sh power_off')
        sent = '/usr/bin/powerctrl.sh power_off'
        via = 'bmc ssh'
    elif action == 'grace_off':
        rc, out = _ssh(bmc_ip, bmc_user, bmc_pass,
                       '/usr/bin/powerctrl.sh grace_off')
        sent = '/usr/bin/powerctrl.sh grace_off'
        via = 'bmc ssh'
    elif action == 'reboot':
        # SSH to the OS so it runs its own shutdown sequence; IPMI power reset
        # cuts the host mid-flight and risks the filesystem.
        rc, out = _ssh(os_ip, os_user, os_pass, 'reboot')
        sent = 'reboot'
        via = 'os ssh'
    elif action == 'bmc_reset':
        # Restarts the BMC itself, not the host. The host keeps running and the
        # BMC is unreachable for roughly 60-90 s afterwards.
        code, _ = _redfish(
            bmc_ip, '/redfish/v1/Managers/BMC_0/Actions/Manager.Reset',
            method='POST', user=bmc_user, password=bmc_pass,
            payload={'ResetType': 'GracefulRestart'})
        rc = 0 if code in (200, 202, 204) else 1
        out = f'HTTP {code}'
        sent = 'Manager.Reset GracefulRestart'
        via = 'redfish'
    else:
        return {'ok': False, 'error': f'unknown action {action}'}

    return {
        'ok': rc == 0,
        'node': name,
        'action': action,
        'command': sent,
        'via': via,
        'exit_code': rc,
        'output': out.strip()[-500:],
        'elapsed': round(time.time() - started, 1),
    }


def tray_action(tray, action):
    """Apply a node action to every node of a tray, in parallel."""
    from concurrent.futures import ThreadPoolExecutor
    rec = inv.TRAYS.get(tray)
    if not rec:
        return [{'ok': False, 'error': f'unknown tray {tray}'}]
    names = [f'{rec["name"]}-n{i}' for i in range(len(rec['nodes']))]
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as ex:
        return list(ex.map(lambda n: node_action(n, action), names))


def shelf_action(shelf, action):
    """Power ON/OFF every unit of one power shelf."""
    ip = inv.POWER_SHELVES.get(shelf)
    if not ip:
        return {'ok': False, 'error': f'unknown power shelf {shelf}'}
    if action == 'status':
        return {'ok': True, 'shelf': shelf, 'ip': ip, 'units': shelf_units(ip)}
    path = {'on': SHELF_ON, 'off': SHELF_OFF}.get(action)
    if not path:
        return {'ok': False, 'error': f'unknown shelf action {action}'}
    code, data = _redfish(ip, path, method='POST', timeout=20)
    text = json.dumps(data) if not isinstance(data, str) else data
    return {
        'ok': code in (200, 204) and 'Success' in text,
        'shelf': shelf,
        'ip': ip,
        'action': action,
        'http': code,
        'response': text[:400],
    }
