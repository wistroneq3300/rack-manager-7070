"""Hardware / firmware inventory for one node.

The vendor script `Config-Neutrino.sh` already knows how to walk this platform
(mst for the Vera SuperNICs, dmidecode for SOCAMMs, the 2f95-2f98 fabric
bridges, the BF4 that should be there but is not). Re-implementing that is how
you get quietly wrong answers, so this module runs the script over SSH and
parses its output instead.

Two sections come back from the script:
  [Pass]       - aggregated component counts, e.g. "CPU: Vera A1Z x2"
  [FW Version] - a fixed-width table of every firmware component

[Fail] is skipped on purpose: it is the script's checklist of what the
platform is expected to carry, not a measurement. This page shows what is
actually installed.

The parser is deliberately tolerant: the script's formatting varies slightly
between releases, and a parse failure should leave one line blank rather than
fail the whole page.
"""

import base64
import json
import re
import ssl
import urllib.request
from pathlib import Path

import inventory as inv
import rack_probe as probe

SCRIPT = '/root/Config-Neutrino.sh'
SSH_TIMEOUT = 180

# Local copy pushed to a node that does not carry the script yet (fresh image,
# reinstall, a tray that was never provisioned).
VENDOR = Path(__file__).resolve().parent / 'vendor' / 'Config-Neutrino.sh'

_SECTION_RE = re.compile(r'^\[(?P<name>[^\]]+)\]\s*$')


def _strip_ansi(s):
    return re.sub(r'\x1b\[[0-9;]*m', '', s)


def push_script(host, user, pw):
    """Ensure SCRIPT exists on the node, copying VENDOR over when it does not.

    Returns a short note when something was written, None when the node
    already had the script or no local copy is available. Best effort: a
    failure here falls through to the normal "not found" message.
    """
    if not VENDOR.is_file():
        return None
    rc, out = probe._ssh(host, user, pw, f'test -s {SCRIPT} && echo have',
                         timeout=30)
    if 'have' in (out or ''):
        return None
    payload = base64.b64encode(VENDOR.read_bytes()).decode()
    cmd = (f'echo {payload} | base64 -d > {SCRIPT} && '
           f'chmod 755 {SCRIPT} && echo pushed')
    rc, out = probe._ssh(host, user, pw, cmd, timeout=120)
    if 'pushed' in (out or ''):
        return f'pushed {SCRIPT}'
    return None


def run_script(node):
    """Run Config-Neutrino.sh on the node.

    Returns (ok, raw_text_or_error, pushed_note). The third element is set when
    the script had to be copied to the node first, so the caller can say so.
    """
    rec = inv.lookup_node(node)
    if not rec:
        return False, f'unknown node {node}', None
    host = rec.get('os_ip')
    if not host:
        return False, 'no OS IP recorded for this node', None
    user = rec.get('os_user') or inv.OS_USER
    pw = rec.get('os_pass') or inv.OS_PASS
    pushed = push_script(host, user, pw)
    # 'bash <path>' rather than executing directly: the script may not carry
    # the exec bit on a freshly imaged node.
    rc, out = probe._ssh(host, user, pw, f'bash {SCRIPT}', timeout=SSH_TIMEOUT)
    text = _strip_ansi(out or '')
    if rc != 0:
        hint = text.strip()[-300:] or f'ssh exit {rc}'
        if 'No such file' in text:
            hint = f'{SCRIPT} not found on {host}'
        return False, hint, pushed
    if '[Info]' not in text and '[Pass]' not in text:
        return False, 'script produced no inventory output', pushed
    return True, text, pushed


def _split_sections(text):
    """{section_name: [lines]} preserving order of first appearance."""
    sections = {}
    current = None
    for line in text.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            current = m.group('name').strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def parse_components(text):
    """The [Pass]/[Fail] blocks: 'CPU: Vera A1Z x2' -> {cpu: [{label, qty}]}."""
    out = {}
    sections = _split_sections(text)
    # Only [Pass] is a hardware inventory. [Fail] is the script's own opinion
    # about what the platform ought to have ('BF4(DPU)'); this view reports
    # what IS present, so it is deliberately ignored - a component that is not
    # installed yet is not a finding.
    for line in sections.get('Pass', []):
        line = line.strip()
        if not line or ':' not in line:
            continue
        tag, rest = line.split(':', 1)
        # CPU/NVMe/PCIeFAB must survive untouched; only an all-lowercase tag
        # gets title-cased. isupper() is not usable: 'NVMe' is not uppercase.
        tag = tag.strip()
        if tag and tag == tag.lower():
            tag = tag.title()
        rest = rest.strip()
        m = re.match(r'^(?P<label>.*?)\s+x(?P<qty>\d+)$', rest)
        if m:
            label, qty = m.group('label').strip(), int(m.group('qty'))
        else:
            label, qty = rest, 1
        out.setdefault(tag, []).append({'label': label, 'qty': qty})
    return out


def parse_firmware(text):
    """The [FW Version] block -> [{component, version}], plus the BMC IP line.

    The block has two shapes. When the script reaches Redfish it prints
    "BMC IP : x" and a fixed-width table; when Redfish is unreachable it
    prints a status line instead and only "BIOS: ..." as a single line
    (there is no BMC/IPMI firmware to report in-band). Both are parsed here:
    without the single-line form a node whose BMC is down showed a firmware
    table with nothing in it, which reads like "no firmware" rather than
    "could not read it".
    """
    bmc_ip = None
    bmc_note = None
    rows = []
    in_fw = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('BMC IP'):
            _, _, val = stripped.partition(':')
            bmc_ip = val.strip() or None
            continue
        if stripped.startswith('BMC :'):
            bmc_note = stripped.partition(':')[2].strip() or None
            continue
        if stripped.startswith('BIOS:'):
            _, _, val = stripped.partition(':')
            if val.strip():
                rows.append({'component': 'BIOS', 'version': val.strip()})
            continue
        if _SECTION_RE.match(stripped):
            in_fw = stripped.lower().startswith('[fw')
            continue
        if not in_fw or not stripped:
            continue
        if set(stripped) <= {'-'}:           # the ---- ruler
            continue
        if stripped.lower().startswith('component'):
            continue
        # Fixed width: component name, two or more spaces, then the version.
        m = re.match(r'^(?P<name>[A-Za-z0-9_.\-]+)\s{2,}(?P<ver>.+)$', line)
        if m:
            rows.append({'component': m.group('name'),
                         'version': m.group('ver').strip()})
    return rows, bmc_ip, bmc_note


# --- firmware read out-of-band from the BMC ----------------------------------
# Config-Neutrino.sh only reaches Redfish after asking the HOST for the BMC IP
# with `ipmitool lan print`, which needs /dev/ipmi0. On a node whose in-band
# IPMI interface failed to come up (ipmi_ssif probe -5, common after a warm
# boot) that device does not exist, BMC_IP comes back empty, the Redfish branch
# is skipped entirely and the script falls back to a single "BIOS:" line - so
# the firmware table shows up empty. Nothing is wrong with the BMC: it answers
# Redfish normally. The BMC address is already known (it is pinned in
# inventory), so read the firmware straight out-of-band and skip the host.

FW_KEYS = [
    'FW_BMC_0', 'FW_ERoT_BMC_0', 'FW_CPLD_0',
    'FW_ProcessorModule_SMA_0', 'FW_StorageBackplane_SMA_0',
    'SBIOS_FMC_0', 'SBIOS_FMC_1', 'SBIOS_FW_0', 'SBIOS_FW_1',
]
BMC_TIMEOUT = 20


def _redfish_get(url, user, pw):
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    auth = base64.b64encode(f'{user}:{pw}'.encode()).decode()
    req.add_header('Authorization', f'Basic {auth}')
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=BMC_TIMEOUT, context=ctx) as r:
        return json.loads(r.read().decode())


def read_bmc_firmware(rec):
    """Firmware table from the BMC's own Redfish. Returns (rows, error).

    Components are discovered from the FirmwareInventory collection instead of
    a hardcoded list, so parts added later (the BlueField SoC, the E1S NVMe
    drives) appear without touching this file. FW_KEYS only decides ordering.
    """
    host = rec.get('bmc_ip')
    user = rec.get('bmc_user') or inv.BMC_USER
    pw = rec.get('bmc_pass') or inv.BMC_PASS
    if not host:
        return None, 'no BMC IP recorded for this node'
    base = f'https://{host}/redfish/v1'

    rows = []
    try:
        sysinfo = _redfish_get(f'{base}/Systems/System_0', user, pw)
        bios = (sysinfo.get('BiosVersion') or '').strip()
        if bios:
            rows.append({'component': 'BIOS', 'version': bios})
        inv_json = _redfish_get(f'{base}/UpdateService/FirmwareInventory',
                                user, pw)
    except Exception as e:                                   # noqa: BLE001
        return None, f'BMC {host} Redfish unreachable: {e}'

    for member in inv_json.get('Members', []):
        key = (member.get('@odata.id') or '').rstrip('/').rsplit('/', 1)[-1]
        if not key:
            continue
        try:
            item = _redfish_get(f'{base}/UpdateService/FirmwareInventory/{key}',
                                user, pw)
        except Exception:                                    # noqa: BLE001
            continue
        ver = (item.get('Version') or '').strip()
        if ver:
            rows.append({'component': key, 'version': ver})

    if not rows:
        return None, f'BMC {host} returned no firmware versions'

    def order(r):
        try:
            return (0, FW_KEYS.index(r['component']))
        except ValueError:
            return (1, 0)
    rest = sorted([r for r in rows if r['component'] != 'BIOS'], key=order)
    return ([rows[0]] + rest if rows and rows[0]['component'] == 'BIOS'
            else rest), None


def read_node(node):
    """Everything the HW/FW popup needs for one node."""
    rec = inv.lookup_node(node)
    if not rec:
        return {'ok': False, 'node': node, 'error': f'unknown node {node}'}

    ok, text, pushed = run_script(node)
    if not ok:
        # The host is unreadable, but the BMC's firmware is still out-of-band
        # reachable, so show it rather than an empty page.
        firmware, fw_err = read_bmc_firmware(rec)
        return {'ok': False, 'node': node, 'error': text, 'pushed': pushed,
                'os_ip': rec.get('os_ip'), 'bmc_ip': rec.get('bmc_ip'),
                'firmware': firmware or [],
                'fw_source': 'bmc' if firmware else None,
                'fw_note': None if firmware else fw_err}

    components = parse_components(text)
    # Firmware comes from the BMC itself; the script's [FW Version] table is
    # only a fallback for the case where the BMC cannot be reached at all.
    firmware, fw_err = read_bmc_firmware(rec)
    fw_bmc_ip = None
    bmc_note = None
    fw_source = 'bmc'
    if not firmware:
        firmware, fw_bmc_ip, bmc_note = parse_firmware(text)
        fw_source = 'script'
        if not firmware:
            bmc_note = bmc_note or fw_err
    return {
        'ok': True,
        'node': node,
        'os_ip': rec.get('os_ip'),
        'bmc_ip': fw_bmc_ip or rec.get('bmc_ip'),
        'components': components,
        'firmware': firmware,
        'fw_source': fw_source,
        'fw_note': bmc_note,
        'pushed': pushed,
        'raw': text,
    }