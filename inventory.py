"""Rack inventory: Neutrino trays x 4 nodes, plus 3 LiteOn power shelves.

Source of truth is the "Connection information" sheet of
"NVIDIA Neutrino L8-L10 Validation Plan.xlsm". Rows whose sheet values are
known-bad are corrected here and the reason is recorded next to them.

At runtime the rack is editable: inventory.json (see DATA_FILE) replaces
DEFAULT_TRAYS entirely once it exists.
"""
import json
import os
import re
import threading
from pathlib import Path

# (tray_index, [(os_ip, bmc_ip) x4])
DEFAULT_TRAYS = {
    0:  [('10.36.72.2', '10.36.72.3'), ('10.36.72.4', '10.36.72.5'),
         ('10.36.72.10', '10.36.72.7'), ('10.36.72.8', '10.36.72.9')],
    1:  [('10.36.72.12', '10.36.72.13'), ('10.36.72.14', '10.36.72.15'),
         ('10.36.72.16', '10.36.72.17'), ('10.36.72.18', '10.36.72.19')],
    2:  [('10.36.72.22', '10.36.72.23'), ('10.36.72.24', '10.36.72.25'),
         ('10.36.72.26', '10.36.72.27'), ('10.36.72.28', '10.36.72.29')],
    3:  [('10.36.72.32', '10.36.72.33'), ('10.36.72.34', '10.36.72.35'),
         ('10.36.72.36', '10.36.72.37'), ('10.36.72.38', '10.36.72.39')],
    4:  [('10.36.72.42', '10.36.72.43'), ('10.36.72.44', '10.36.72.45'),
         ('10.36.72.46', '10.36.72.47'), ('10.36.72.48', '10.36.72.49')],
    5:  [('10.36.72.52', '10.36.72.53'), ('10.36.72.54', '10.36.72.55'),
         ('10.36.72.56', '10.36.72.57'), ('10.36.72.58', '10.36.72.59')],
    6:  [('10.36.72.62', '10.36.72.63'), ('10.36.72.64', '10.36.72.65'),
         ('10.36.72.66', '10.36.72.67'), ('10.36.72.68', '10.36.72.69')],
    7:  [('10.36.72.72', '10.36.72.73'), ('10.36.72.74', '10.36.72.75'),
         ('10.36.72.76', '10.36.72.77'), ('10.36.72.78', '10.36.72.79')],
    8:  [('10.36.72.82', '10.36.72.83'), ('10.36.72.84', '10.36.72.85'),
         ('10.36.72.86', '10.36.72.87'), ('10.36.72.88', '10.36.72.89')],
    # n0 OS 修自表格的 10.32.72.92（筆誤）。
    9:  [('10.36.72.92', '10.36.72.93'), ('10.36.72.94', '10.36.72.95'),
         ('10.36.72.96', '10.36.72.97'), ('10.36.72.98', '10.36.72.99')],
    # n2/n3 修自表格的 .226/.228；SSH 實測 .206=Neutrino10-n2、.208=Neutrino10-n3。
    10: [('10.36.72.202', '10.36.72.203'), ('10.36.72.204', '10.36.72.205'),
         ('10.36.72.206', '10.36.72.207'), ('10.36.72.208', '10.36.72.209')],
    11: [('10.36.72.212', '10.36.72.213'), ('10.36.72.214', '10.36.72.215'),
         ('10.36.72.216', '10.36.72.217'), ('10.36.72.218', '10.36.72.219')],
    12: [('10.36.72.222', '10.36.72.223'), ('10.36.72.224', '10.36.72.225'),
         ('10.36.72.226', '10.36.72.227'), ('10.36.72.228', '10.36.72.229')],
    13: [('10.36.72.232', '10.36.72.233'), ('10.36.72.234', '10.36.72.235'),
         ('10.36.72.236', '10.36.72.237'), ('10.36.72.238', '10.36.72.239')],
}

# BF4 DPU management addresses per node. The sheet gives these as two flat,
# non-contiguous blocks rather than per-node columns:
#   Ne 0..9  -> bf4_os = tray_base + 100, bf4_bmc = tray_base + 101
#   Ne 10..13 -> bf4_os = 10.36.72.(20*n' + 30), bf4_bmc = +1   (n' = n-1)
# They must not be derived at runtime: the OS/BMC pairs for trays 10 and 12
# diverge from the sheet (see DEFAULT_TRAYS) while the BF4 pairs do not.
BF4_DPU = {
    0:  [('10.36.72.102', '10.36.72.103'), ('10.36.72.104', '10.36.72.105'),
         ('10.36.72.106', '10.36.72.107'), ('10.36.72.108', '10.36.72.109')],
    1:  [('10.36.72.112', '10.36.72.113'), ('10.36.72.114', '10.36.72.115'),
         ('10.36.72.116', '10.36.72.117'), ('10.36.72.118', '10.36.72.119')],
    2:  [('10.36.72.122', '10.36.72.123'), ('10.36.72.124', '10.36.72.125'),
         ('10.36.72.126', '10.36.72.127'), ('10.36.72.128', '10.36.72.129')],
    3:  [('10.36.72.132', '10.36.72.133'), ('10.36.72.134', '10.36.72.135'),
         ('10.36.72.136', '10.36.72.137'), ('10.36.72.138', '10.36.72.139')],
    4:  [('10.36.72.142', '10.36.72.143'), ('10.36.72.144', '10.36.72.145'),
         ('10.36.72.146', '10.36.72.147'), ('10.36.72.148', '10.36.72.149')],
    5:  [('10.36.72.152', '10.36.72.153'), ('10.36.72.154', '10.36.72.155'),
         ('10.36.72.156', '10.36.72.157'), ('10.36.72.158', '10.36.72.159')],
    6:  [('10.36.72.162', '10.36.72.163'), ('10.36.72.164', '10.36.72.165'),
         ('10.36.72.166', '10.36.72.167'), ('10.36.72.168', '10.36.72.169')],
    7:  [('10.36.72.172', '10.36.72.173'), ('10.36.72.174', '10.36.72.175'),
         ('10.36.72.176', '10.36.72.177'), ('10.36.72.178', '10.36.72.179')],
    8:  [('10.36.72.182', '10.36.72.183'), ('10.36.72.184', '10.36.72.185'),
         ('10.36.72.186', '10.36.72.187'), ('10.36.72.188', '10.36.72.189')],
    9:  [('10.36.72.192', '10.36.72.193'), ('10.36.72.194', '10.36.72.195'),
         ('10.36.72.196', '10.36.72.197'), ('10.36.72.198', '10.36.72.199')],
    10: [('10.36.72.250', '10.36.72.11'), ('10.36.72.20', '10.36.72.21'),
         ('10.36.72.30', '10.36.72.31'), ('10.36.72.40', '10.36.72.41')],
    11: [('10.36.72.50', '10.36.72.51'), ('10.36.72.60', '10.36.72.61'),
         ('10.36.72.70', '10.36.72.71'), ('10.36.72.80', '10.36.72.81')],
    12: [('10.36.72.90', '10.36.72.91'), ('10.36.72.100', '10.36.72.101'),
         ('10.36.72.110', '10.36.72.111'), ('10.36.72.120', '10.36.72.121')],
    13: [('10.36.72.130', '10.36.72.131'), ('10.36.72.140', '10.36.72.141'),
         ('10.36.72.150', '10.36.72.151'), ('10.36.72.160', '10.36.72.161')],
}

# Powershelf-2 (10.36.72.220) is decommissioned and deliberately absent.
POWER_SHELVES = {
    1: '10.36.72.210',
    3: '10.36.72.230',
    4: '10.36.72.240',
}

BMC_USER = 'root'
BMC_PASS = '0penBmc'
OS_USER = 'root'
OS_PASS = 'password'
# BF4/DPU credentials ship with the DPU image and are the same rack-wide.
BF4_OS_USER = 'ubuntu'
BF4_OS_PASS = 'Nvidia_12345!'
BF4_BMC_USER = 'service'
BF4_BMC_PASS = 'Nvidia_12345!'

SHELF_UNITS = 6          # powerdevice0..5 per shelf
INVENTORY_UNITS = 4      # every tray holds n0..n3

# ---------------------------------------------------------------------------
# Mutable overlay. inventory.json — when present — fully replaces
# DEFAULT_TRAYS. Writes go through save() under a lock so two concurrent adds
# cannot interleave.
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
DATA_FILE = Path(os.environ.get('RACK_INVENTORY', HERE / 'inventory.json'))
_LOCK = threading.RLock()

# tray -> {'name': 'Neutrino0', 'nodes':[{os_ip,bmc_ip,os_user,os_pass,
#           bmc_user,bmc_pass,os_hostname,bmc_hostname,slot}, ...]}
TRAYS = {}


def _from_defaults():
    """Turn DEFAULT_TRAYS into the full record shape with shared credentials."""
    out = {}
    for tray, pairs in DEFAULT_TRAYS.items():
        out[tray] = {
            'name': f'Neutrino{tray}',
            'nodes': [
                {'slot': i + 1, 'os_ip': os_ip, 'bmc_ip': bmc_ip,
                 'os_user': OS_USER, 'os_pass': OS_PASS,
                 'bmc_user': BMC_USER, 'bmc_pass': BMC_PASS,
                 'os_hostname': '', 'bmc_hostname': ''}
                for i, (os_ip, bmc_ip) in enumerate(pairs)
            ],
        }
    return out


def reload():
    """Load inventory.json, falling back to the built-in 14-tray layout."""
    global TRAYS
    with _LOCK:
        if DATA_FILE.is_file():
            try:
                with DATA_FILE.open(encoding='utf-8') as fh:
                    raw = json.load(fh)
                TRAYS = {int(k): v for k, v in raw.get('trays', {}).items()}
                return TRAYS
            except (OSError, ValueError, AttributeError):
                # A corrupt file must not take the whole rack offline.
                TRAYS = _from_defaults()
                return TRAYS
        TRAYS = _from_defaults()
        return TRAYS


def save():
    with _LOCK:
        payload = {'trays': {str(k): v for k, v in sorted(TRAYS.items())}}
        tmp = DATA_FILE.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp.replace(DATA_FILE)


def add_tray(tray, name, nodes):
    """Insert or replace a tray. nodes is a list of 4 node dicts."""
    with _LOCK:
        TRAYS[tray] = {'name': name, 'nodes': list(nodes)}
        save()
        return TRAYS[tray]


def remove_tray(tray):
    with _LOCK:
        if tray not in TRAYS:
            return False
        TRAYS.pop(tray)
        save()
        return True


def next_tray_index():
    with _LOCK:
        used = set(TRAYS)
        i = 0
        while i in used:
            i += 1
        return i


def nodes():
    """Flatten to (tray, node_index, node_name, os_ip, bmc_ip)."""
    for tray in sorted(TRAYS):
        for i, n in enumerate(TRAYS[tray]['nodes']):
            yield tray, i, f'{TRAYS[tray]["name"]}-n{i}', n['os_ip'], n['bmc_ip']


def lookup_node(name):
    """Resolve a node name to its record, including credentials."""
    with _LOCK:
        for tray in sorted(TRAYS):
            rec = TRAYS[tray]
            for i, n in enumerate(rec['nodes']):
                if f'{rec["name"]}-n{i}' == name:
                    out = dict(n)
                    out.update(tray=tray, node=i, name=name)
                    return out
    return None


def tray_of(name):
    rec = lookup_node(name)
    return rec['tray'] if rec else None


def tray_name(tray):
    rec = TRAYS.get(tray)
    return rec['name'] if rec else f'tray{tray}'


MAC_FIELDS = ('os_mac', 'bmc_mac', 'bf4_os_mac', 'bf4_bmc_mac')


def node_macs(tray, index):
    """'{os,bmc,bf4_os,bf4_bmc}_mac' for a node; '' where never probed."""
    with _LOCK:
        rec = TRAYS.get(tray)
        nodes = (rec or {}).get('nodes') or []
        if not 0 <= index < len(nodes):
            return {k: '' for k in MAC_FIELDS}
        n = nodes[index]
        return {k: (n.get(k) or '') for k in MAC_FIELDS}


def set_node_macs(tray, index, macs):
    """Merge freshly probed MACs into a node record. Empty values are ignored
    so one failed probe cannot erase a MAC that was read before."""
    with _LOCK:
        rec = TRAYS.get(tray)
        nodes = (rec or {}).get('nodes') or []
        if not 0 <= index < len(nodes):
            return False
        for k, v in (macs or {}).items():
            if k in MAC_FIELDS and v:
                nodes[index][k] = v
    save()
    return True


def node_hostname(tray, index):
    """Stored OS hostname for a node, '' when it was never probed."""
    rec = TRAYS.get(tray)
    if not rec:
        return ''
    nodes = rec.get('nodes') or []
    if not 0 <= index < len(nodes):
        return ''
    return nodes[index].get('os_hostname') or ''


def set_node_hostname(tray, index, hostname):
    """Persist a freshly probed hostname. Caller holds no lock; save() takes it."""
    with _LOCK:
        rec = TRAYS.get(tray)
        if not rec:
            return False
        nodes = rec.get('nodes') or []
        if not 0 <= index < len(nodes):
            return False
        nodes[index]['os_hostname'] = hostname or ''
    save()
    return True


def _tray_number(name):
    """'Neutrino13' -> 13; None when the name does not carry a tray number."""
    m = re.fullmatch(r'Neutrino(\d+)', (name or '').strip())
    return int(m.group(1)) if m else None


def bf4_node(tray, index):
    """(bf4_os_ip, bf4_bmc_ip) for a node, ('', '') when the tray has none.

    inventory.json node records may override the table with explicit bf4_*
    keys; otherwise BF4_DPU is keyed by tray number, so a tray added at runtime
    is matched by its NeutrinoN name rather than by its index.
    """
    with _LOCK:
        rec = TRAYS.get(tray)
        if not rec:
            return '', ''
        nodes = rec.get('nodes') or []
        if 0 <= index < len(nodes):
            n = nodes[index]
            if n.get('bf4_os_ip') or n.get('bf4_bmc_ip'):
                return n.get('bf4_os_ip') or '', n.get('bf4_bmc_ip') or ''
        pairs = BF4_DPU.get(_tray_number(rec.get('name') or ''))
        if pairs and 0 <= index < len(pairs):
            return pairs[index]
    return '', ''


reload()
