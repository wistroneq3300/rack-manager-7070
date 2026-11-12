"""Firmware flashing over BMC Redfish multipart push.

Three flows from the vendor runbook. They share the same endpoint, method
and fields, but differ in `Targets` and in what must happen afterwards —
they are NOT interchangeable:

  CPLD  Targets=[]        (BMC decides which component)
        no manager reset; a later aux cycle makes the new version
        visible in FirmwareInventory.

  BIOS  Targets=[]        (BMC decides which component)
        requires a chassis power off/on after the task completes, so the
        new BIOS is actually loaded. No Manager.Reset.

  BMC   Targets=[FW_BMC_0]  (explicit component)
        requires Manager.Reset (GracefulRestart) before the new
        version takes effect.

Every call returns a job id so the UI can poll progress; nothing here
blocks for the length of a flash.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import inventory as inv
import rack_probe as probe

FW_DIR = Path(__file__).resolve().parent / 'fw'
MAX_UPLOAD = 512 * 1024 * 1024        # 512 MB cap on a firmware package
ALLOWED_SUFFIX = {'.fwpkg', '.bin', '.img'}

PUSH_URI = '/redfish/v1/UpdateService/update-multipart'
BMC_TARGET = '/redfish/v1/UpdateService/FirmwareInventory/FW_BMC_0'
BF4_TARGET = '/redfish/v1/UpdateService/FirmwareInventory/FW_BlueField_SMA_0'

# Inventory ids we know how to report a version for.
VERSION_ID = {
    'cpld': 'FW_CPLD_0',
    'bmc': 'FW_BMC_0',
    'bios': 'SBIOS_FW_0',
    'bf4': 'FW_BlueField_SMA_0',
}

# ------------------------------------------------------------- classify ---
# Filenames under \\10.33.34.22\eq3300\...\7.Neutrino\2. Firmware follow the
# vendor's build naming, which is regular enough to catch a wrong upload
# before it reaches a BMC:
#
#   VC_BMC_256_26100400_dev_prod_signed.fwpkg          ~64 MB   BMC
#   CPLD_neutrino_0v41_prod_signed.fwpkg              ~94 KB   CPLD
#   mcxn556-mcufw-p5032-0059.02.0115.0009-rel-prod.fwpkg  ~369 KB  HPM MCU
#   mcxn556-mcufw-pd4c8-0052.02.0090.0000-rel-prod.fwpkg  ~369 KB  BF4 MCU
#
# HPM MCU and BF4 MCU share the mcxn556-mcufw prefix and the same size, so the
# board token in the middle (p5032 vs pd4c8) is the ONLY thing that separates
# them. That is precisely the pair this check exists to catch.
#
# classify() returns (kind, reasons). kind is None when nothing matched. The
# caller shows it to the operator; it never blocks an upload on its own.
_CLASSIFY = [
    # (kind, regex on lowercase name)
    ('bmc', re.compile(r'(^|[_\-])vc_bmc|vc_bmc_')),
    ('cpld', re.compile(r'cpld')),
    ('bf4', re.compile(r'mcufw.*pd4c8|pd4c8.*mcufw')),
    ('hpm', re.compile(r'mcufw.*p5032|p5032.*mcufw')),
]

# Flags appended to the classification. A '_sb4' package is the same firmware
# carried in a different container; 'transition_package' is a stub used while
# climbing BMC versions. Both are valid to upload but are almost never what the
# operator means when they pick a package in a hurry.
def _flags(name, size):
    low = name.lower()
    out = []
    if '_sb4' in low or low.endswith('.sb4'):
        out.append('sb4')
    if 'transition' in low:
        out.append('transition')
    if 'debug' in low:
        out.append('debug')
    return out


def classify(name, size=None):
    """Best-effort (kind, confidence, reasons) for a package filename.

    confidence is 'high' only when the name matched a known build pattern and
    the size is consistent with it. 'low' means a name matched but the size is
    off by an order of magnitude - a truncated or mislabelled file. None means
    no rule matched, which is normal for BIOS and E1.S MCU builds, whose naming
    we have no samples for yet.
    """
    low = (name or '').lower()
    kind = None
    for k, rx in _CLASSIFY:
        if rx.search(low):
            kind = k
            break

    flags = _flags(low, size)
    reasons = []

    if kind and size:
        # Order-of-magnitude guard. A 369 KB file named like a BMC package is
        # a wrong upload, not a small BMC.
        expect = {'bmc': 60 * 1024 * 1024, 'cpld': 90 * 1024}
        want = expect.get(kind)
        if want and not (want / 20 < size < want * 20):
            reasons.append(f'檔案大小 {_mb(size)} 與 {kind.upper()} 的典型值不符')
            return kind, 'low', reasons
        if kind in ('bf4', 'hpm') and not (50 * 1024 < size < 8 * 1024 * 1024):
            reasons.append(f'檔案大小 {_mb(size)} 與 MCU 韌體的典型值（約 0.4 MB）不符')
            return kind, 'low', reasons

    if flags:
        reasons.extend(_FLAG_TEXT[f] for f in flags if f in _FLAG_TEXT)
    return kind, ('high' if kind else None), reasons


_FLAG_TEXT = {
    'sb4': '_sb4 是另一種封裝／簽章形式，與同版本的主檔不是同一個包',
    'transition': 'transition_package 是版本升級過程用的過渡包，不是最終韌體',
    'debug': '這是 debug 版，不是正式 release 版',
}


def _mb(n):
    if n >= 1024 * 1024:
        return f'{n / 1048576:.1f} MB'
    return f'{n / 1024:.0f} KB'

# Per-kind recipe.
#
# Flashing only ever pushes the package and reads the version back. Nothing
# reboots, power-cycles or resets: those are separate operator actions in the
# UI (Aux Cycle / Power Cycle / Reboot), and doing them automatically would
# hide a destructive step behind a progress bar.
#   targets  -> [] means "let the BMC pick"; a list pins the component
#   after    -> what the operator must run themselves for it to take effect
RECIPES = {
    'cpld': {
        'label': 'CPLD',
        'targets': [],               # empty deliberately: BMC selects
        'after': 'aux_cycle',
        'post_note': '刷寫完成後須執行 Aux Cycle 以載入新映像',
    },
    'bmc': {
        'label': 'BMC',
        'targets': [BMC_TARGET],
        'after': 'manager_reset',
        'post_note': '刷寫完成後須重啟 BMC 以啟用新映像',
    },
    'bios': {
        'label': 'BIOS',
        'targets': [],               # empty deliberately: BMC selects
        'after': 'power_cycle',
        'post_note': '刷寫完成後須執行 Power Cycle 以載入新映像',
    },
    'bf4': {
        'label': 'BF4 FW',
        'targets': [BF4_TARGET],
        'after': 'aux_cycle',
        # The vendor's BlueField MCU SOP differs from the tray flows above:
        # it asks for OperationApplyTime=Immediate and does NOT send
        # ForceUpdate. Whole-rack flashing of a wrong .fwpkg here bricks the
        # DPU MCU, so follow the SOP field for field rather than reusing the
        # generic params.
        'params': {'Targets': [BF4_TARGET],
                   '@Redfish.OperationApplyTime': 'Immediate'},
        'post_note': '刷寫完成後須執行 Aux Cycle 以載入新 MCU 韌體',
    },
}

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL = 7 * 24 * 60 * 60   # a week of flash history

# Jobs live in memory for speed but are mirrored here so a service restart does
# not orphan a running flash. The BMC keeps flashing whether or not we are
# watching, so losing the record made a 10-minute job invisible and, worse,
# impossible to activate from the UI afterwards.
STATE_DIR = Path(os.environ.get('RACK_STATE_DIR', '/var/lib/rack-manager'))
JOBS_FILE = STATE_DIR / 'jobs.json'
_PERSIST_MIN_INTERVAL = 1.0

# How long a node gets to come back before its post-cycle readback is written
# off. Generous on purpose: a late answer is only a display delay, but calling
# a machine dead while it is still booting invites an operator to power-cycle
# something that was about to be fine.
ACTIVATION_TIMEOUT = {
    'bmc_reset': 600,        # 10 min
    'aux_cycle': 900,        # 15 min
    'power_cycle': 1800,     # 30 min - BIOS POST plus OS boot
    'default': 900,
}
_last_persist = 0.0

# None = flash as many as were selected, all at once. Set an int to cap it.
# Each node is an independent BMC and the push is I/O bound on very slow
# embedded flash, so concurrency is the only way to shrink wall-clock time.
PARALLEL_NODES = None

AFTER_LABEL = {
    'aux_cycle': 'Aux Cycle',
    'manager_reset': '重啟 BMC',
    'power_cycle': 'Power Cycle（off → on）',
}


# ------------------------------------------------------------------ store ---

def list_packages():
    FW_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(FW_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in ALLOWED_SUFFIX:
            continue
        st = p.stat()
        kind, conf, reasons = classify(p.name, st.st_size)
        out.append({'name': p.name, 'size': st.st_size,
                    'kind': kind, 'confidence': conf, 'reasons': reasons,
                    'mtime': time.strftime('%Y-%m-%d %H:%M:%S',
                                           time.localtime(st.st_mtime))})
    return out


def safe_package_path(name):
    """Resolve a package name inside FW_DIR, rejecting traversal."""
    if not name or not re.match(r'^[A-Za-z0-9._\-]{1,128}$', name):
        return None
    p = (FW_DIR / name).resolve()
    try:
        p.relative_to(FW_DIR.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def save_package(name, data):
    """Store an uploaded package. Returns (path, error)."""
    name = Path(name).name                  # strip any directory part
    if not re.match(r'^[A-Za-z0-9._\- ]{1,128}$', name):
        return None, 'invalid file name'
    if Path(name).suffix.lower() not in ALLOWED_SUFFIX:
        return None, f'unsupported type (allowed: {", ".join(sorted(ALLOWED_SUFFIX))})'
    if not data:
        return None, 'empty file'
    if len(data) > MAX_UPLOAD:
        return None, f'file too large (max {MAX_UPLOAD // (1024*1024)} MB)'
    FW_DIR.mkdir(parents=True, exist_ok=True)
    p = FW_DIR / name
    tmp = p.with_suffix(p.suffix + '.part')
    tmp.write_bytes(data)
    tmp.replace(p)
    return p, None


def delete_package(name):
    p = safe_package_path(name)
    if not p:
        return False
    p.unlink()
    return True


# --------------------------------------------------------------- version ---

def read_version(bmc_ip, kind, user=None, password=None):
    fid = VERSION_ID.get(kind)
    if not fid:
        return None
    code, data = probe._redfish(
        bmc_ip, f'/redfish/v1/UpdateService/FirmwareInventory/{fid}',
        user=user, password=password)
    if code == 200 and isinstance(data, dict):
        return data.get('Version') or 'Unknown'
    return None


def read_all_versions(bmc_ip, kind, user=None, pw=None):
    """Every inventory member whose Id belongs to this kind, with versions.

    The SOP warns that one component can expose several members (e.g. more than
    one CPLD on bigger trays) and that all of them must agree. Returns a list of
    {id, name, version}; empty if the inventory could not be read.
    """
    fid = VERSION_ID.get(kind)
    if not fid:
        return []
    prefix = fid.rsplit('_', 1)[0]          # FW_CPLD_0 -> FW_CPLD
    code, data = probe._redfish(
        bmc_ip, '/redfish/v1/UpdateService/FirmwareInventory',
        user=user, password=pw)
    if code != 200 or not isinstance(data, dict):
        return []
    out = []
    for member in data.get('Members', []):
        mid = str(member.get('@odata.id', '')).rsplit('/', 1)[-1]
        if not mid or not mid.startswith(prefix):
            continue
        c, d = probe._redfish(
            bmc_ip, f'/redfish/v1/UpdateService/FirmwareInventory/{mid}',
            user=user, password=pw)
        if c == 200 and isinstance(d, dict):
            out.append({'id': mid, 'name': d.get('Name'),
                        'version': d.get('Version')})
    return out


# ---------------------------------------------------------------- upload ---

def push_package(bmc_ip, kind, package, user=None, password=None,
                 timeout=600):
    """POST the fwpkg to update-multipart. Returns (ok, detail)."""
    recipe = RECIPES.get(kind)
    if not recipe:
        return False, f'unknown kind {kind}'
    params = json.dumps(recipe.get('params')
                        or {'Targets': recipe['targets'], 'ForceUpdate': True})
    cred = f'{user or inv.BMC_USER}:{password or inv.BMC_PASS}'
    argv = [
        'curl', '-sk', '--max-time', str(timeout),
        '-u', cred,
        # The BMC rejects the default 100-continue probe on large bodies.
        '-H', 'Expect:',
        '--location', '--request', 'POST',
        f'https://{bmc_ip}{PUSH_URI}',
        '-F', f'UpdateParameters={params} ;type=application/json',
        '-F', f'UpdateFile=@{package}',
        '-w', '\n__HTTP__:%{http_code}',
    ]
    rc, out = probe._run(argv, timeout=timeout + 30)
    if rc != 0:
        return False, (out or 'curl failed').strip()[:400]
    body, _, code = (out or '').rpartition('\n__HTTP__:')
    try:
        http = int(code.strip())
    except ValueError:
        http = 0
    if http not in (200, 201, 202, 204):
        return False, f'HTTP {http}: {body.strip()[:300]}'
    return True, body.strip()[:300]


def read_task(bmc_ip, task_id, user=None, password=None):
    code, data = probe._redfish(
        bmc_ip, f'/redfish/v1/TaskService/Tasks/{task_id}',
        user=user, password=password)
    if code != 200 or not isinstance(data, dict):
        return {'state': 'Unknown', 'status': None, 'percent': None}
    return {
        'state': data.get('TaskState') or 'Unknown',
        'status': data.get('TaskStatus'),
        'percent': data.get('PercentComplete'),
    }


# ------------------------------------------------------------------- jobs ---

def _persist(force=False):
    """Mirror _JOBS to disk. Throttled: _set runs once per poll tick."""
    global _last_persist
    now = time.time()
    if not force and now - _last_persist < _PERSIST_MIN_INTERVAL:
        return
    _last_persist = now
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_FILE.with_suffix('.tmp')
        with _JOBS_LOCK:
            payload = json.dumps(_JOBS, ensure_ascii=False)
        tmp.write_text(payload)
        tmp.replace(JOBS_FILE)
    except OSError:
        pass          # never let bookkeeping break a flash


def load_jobs():
    """Restore jobs saved by a previous process, then restart their workers."""
    try:
        raw = JOBS_FILE.read_text()
    except (OSError, ValueError):
        return
    try:
        stored = json.loads(raw)
    except ValueError:
        return
    if not isinstance(stored, dict):
        return
    resumed = []
    with _JOBS_LOCK:
        for jid, job in stored.items():
            if not isinstance(job, dict) or 'nodes' not in job:
                continue
            _JOBS[jid] = job
            if not all(n['state'] in ('ok', 'failed', 'skipped')
                       for n in job['nodes']):
                resumed.append(job)
        _prune_jobs()
    # A worker cannot be revived, but the BMC may still be flashing; re-attach
    # by polling the task the node already reported.
    for job in resumed:
        threading.Thread(target=_resume_job, args=(job,), daemon=True).start()


def _resume_job(job):
    for n in list(job['nodes']):
        if n['state'] in ('ok', 'failed', 'skipped'):
            continue
        task = n.get('task')
        if task is None:
            _set(job, n['node'], state='failed',
                 detail='service restarted before the push was accepted')
            continue
        try:
            _flash_node(job, job['kind'], safe_package_path(job['package']),
                        n['node'], resume_task=task)
        except Exception as e:
            _set(job, n['node'], state='failed',
                 detail=f'{type(e).__name__}: {e}')


def start_job(kind, package_name, nodes, dry_run=True):
    """Kick off a flash for the given node names. Returns the job record."""
    recipe = RECIPES[kind]
    job = {
        'id': uuid.uuid4().hex[:12],
        'kind': kind,
        'package': package_name,
        'dry_run': dry_run,
        'after': recipe['after'],
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'nodes': [
            {'node': n, 'state': 'queued', 'detail': '',
             'task': None, 'version_before': None, 'version_after': None}
            for n in nodes
        ],
    }
    with _JOBS_LOCK:
        _JOBS[job['id']] = job
        _prune_jobs()
    _persist(force=True)
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


# A job whose worker is still pushing firmware must not disappear from under
# the thread, so removal is refused while any node is mid-write.
_ACTIVE_STATES = ('queued', 'reading', 'uploading', 'flashing')


def _job_active(job):
    return any(n['state'] in _ACTIVE_STATES for n in job.get('nodes', []))


def delete_job(job_id):
    """Drop one finished job from the store. Returns (ok, error)."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return False, 'job not found'
        if _job_active(job):
            return False, 'job still running'
        del _JOBS[job_id]
    _persist(force=True)
    return True, None


def clear_jobs():
    """Drop every finished job, keeping any that are still writing."""
    with _JOBS_LOCK:
        keep = {jid: j for jid, j in _JOBS.items() if _job_active(j)}
        removed = len(_JOBS) - len(keep)
        _JOBS.clear()
        _JOBS.update(keep)
    _persist(force=True)
    return removed, len(keep)


def _expire_stale(job):
    """Mark nodes that never came back after a restart as stuck.

    The browser polls the BMC for a while after a cycle, but that timer dies
    with the tab: a node that never returned stayed 'awaiting recovery' for
    ever, in the stored record too. Ageing it out here means the verdict does
    not depend on anyone keeping the page open.
    """
    budget = ACTIVATION_TIMEOUT.get(job.get('last_action') or '',
                                    ACTIVATION_TIMEOUT['default'])
    now = time.time()
    changed = False
    for n in job.get('nodes', []):
        at = n.get('restarted_at')
        if not at or n.get('activated') or n.get('stuck'):
            continue
        if now - at > budget:
            n['stuck'] = True
            n['restarted'] = False
            n['detail'] = '無法取得韌體版本，請檢查節點狀態'
            changed = True
    return changed


def get_job(job_id):
    expired = False
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        expired = _expire_stale(job)
        snapshot = json.loads(json.dumps(job))
    # _persist takes _JOBS_LOCK itself; calling it inside the block above
    # would deadlock this plain Lock.
    if expired:
        _persist(force=True)
    return snapshot


def list_jobs():
    with _JOBS_LOCK:
        return [{'id': j['id'], 'kind': j['kind'], 'package': j['package'],
                 'created': j['created'], 'dry_run': j['dry_run'],
                 'done': all(n['state'] in ('ok', 'failed', 'skipped')
                             for n in j['nodes'])}
                for j in sorted(_JOBS.values(), key=lambda x: x['created'],
                                reverse=True)]


def _prune_jobs():
    cutoff = time.time() - _JOB_TTL
    for jid, j in list(_JOBS.items()):
        try:
            t = time.mktime(time.strptime(j['created'], '%Y-%m-%d %H:%M:%S'))
        except ValueError:
            continue
        if t < cutoff:
            _JOBS.pop(jid, None)


def _set(job, name, **kw):
    for n in job['nodes']:
        if n['node'] == name:
            n.update(kw)
            _persist()
            return


def _run_job(job):
    """Flash every selected node, all at once unless PARALLEL_NODES caps it.

    Wall-clock is dominated by the BMC's slow embedded flash (~480 s per node
    for a 64 MB BMC package), so a serial run makes four nodes take half an
    hour. Nodes are independent BMCs, so they are pushed concurrently.
    """
    kind = job['kind']
    pkg = safe_package_path(job['package'])
    if not pkg:
        for n in job['nodes']:
            _set(job, n['node'], state='failed', detail='package not found')
        return

    names = [e['node'] for e in job['nodes']]
    if job['dry_run'] or len(names) == 1:
        _safe_flash(job, kind, pkg, names[0])
        return
    workers = len(names) if PARALLEL_NODES is None else min(PARALLEL_NODES, len(names))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(lambda n: _safe_flash(job, kind, pkg, n), names))


def _safe_flash(job, kind, pkg, name):
    """Run one node's flash, converting any escape into a per-node failure.

    Without this a single unexpected exception kills the worker thread and the
    remaining nodes stay 'queued' forever with no visible reason — which is
    exactly how the first real run of this feature looked.
    """
    try:
        _flash_node(job, kind, pkg, name)
    except Exception as e:
        _set(job, name, state='failed',
             detail=f'{type(e).__name__}: {e}')


def _mgmt(rec, kind):
    """(ip, user, pass) of the BMC that owns this kind's component.

    Everything except BF4 is served by the tray BMC. The BlueField MCU
    (FW_BlueField_SMA_0) is flashed through the *platform* BMC — the vendor
    SOP posts to <PLATFORM_BMC_IP> — so BF4 deliberately uses the same
    address as the other kinds. BF4_DPU addresses in inventory are the DPU's
    own OS/BMC, which speak a different Redfish and are not the update path.
    """
    return (rec['bmc_ip'],
            rec.get('bmc_user') or inv.BMC_USER,
            rec.get('bmc_pass') or inv.BMC_PASS)


def _flash_node(job, kind, pkg, name, resume_task=None):
    """Push one package to one BMC, poll its task, record the version.

    resume_task is set when the service restarted mid-flash: the BMC is still
    working on that task and the package is already on it, so re-pushing would
    start a second, concurrent update. Pick the poll up where it left off.
    """
    recipe = RECIPES[kind]
    rec = inv.lookup_node(name)
    if not rec:
        _set(job, name, state='failed', detail='unknown node')
        return
    bmc_ip, user, pw = _mgmt(rec, kind)

    if job['dry_run']:
        before = read_version(bmc_ip, kind, user, pw)
        _set(job, name, state='ok', version_before=before,
             detail=f'[DRY RUN] would POST {pkg.name} '
                    f'Targets={recipe["targets"] or "[]"} to {bmc_ip}')
        return

    if resume_task is not None:
        _set(job, name, state='flashing', detail=f'task {resume_task} (resumed)')
        final = _poll_task(bmc_ip, resume_task, user, pw, job, name)
        if final != 'Completed':
            _set(job, name, state='failed', detail=f'task {final}')
            return
        _set(job, name, state='ok',
             detail='刷寫完成，待啟用')
        return

    _set(job, name, state='reading', detail='reading version')
    before = read_version(bmc_ip, kind, user, pw)
    _set(job, name, version_before=before, state='uploading',
         detail=f'POST {pkg.name}')

    ok, detail = push_package(bmc_ip, kind, pkg, user, pw)
    if not ok:
        _set(job, name, state='failed', detail=f'push failed: {detail}')
        return

    # The push replies with a Task monitor; poll until it settles. Task ids on
    # this BMC start at 0, so 0 is a valid id, not "missing".
    task_id = _task_id_from(detail)
    _set(job, name, task=task_id, state='flashing', detail=f'task {task_id}')
    final = _poll_task(bmc_ip, task_id, user, pw, job, name)
    if final != 'Completed':
        _set(job, name, state='failed', detail=f'task {final}')
        return

    # Flash only. Reset / power cycle / aux cycle are the operator's call, made
    # from the rack page once they have seen the result here.
    #
    # Do NOT record this reading as version_after: the running image is still
    # the old one until the node is restarted, so it would be stored as the
    # post-activation value and make a real upgrade look like a same-version
    # reflash. version_after is set by mark_activated() only.
    members = read_all_versions(bmc_ip, kind, user, pw)
    detail = '刷寫完成，待啟用'
    if len(members) > 1:
        uniq = {m['version'] for m in members}
        if len(uniq) > 1:
            detail += f' · {len(members)} 個成員版本不一致: {sorted(uniq)}'
    _set(job, name, state='ok', detail=detail)


def mark_restarted(job_id, nodes, action=None):
    """Record that the operator asked these nodes to restart.

    Kept on the job (and therefore on disk) so a page reload still knows which
    rows are mid-restart instead of quietly reverting them to the pre-restart label. The action
    is stored too: a power cycle is only complete once the OS answers ping,
    while a BMC reset just needs the version to read back.
    """
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        if action:
            job['last_action'] = action
        for n in job['nodes']:
            if n['node'] in nodes:
                n['restarted'] = True
                n['restarted_at'] = time.time()
                n['activated'] = False
    _persist(force=True)
    return get_job(job_id)


def mark_activated(job_id, versions):
    """Store the firmware version read back after the BMC returned."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        for n in job['nodes']:
            ver = versions.get(n['node'])
            if ver:
                n['version_after'] = ver
                n['activated'] = True
                n['restarted'] = False
                n['detail'] = '已完成啟用，韌體版本已確認'
    _persist(force=True)
    return get_job(job_id)


def probe_running(job_id, nodes, kind):
    """Live check used after a power/aux cycle.

    ping the host OS first (a power cycle brings the OS back, not just the BMC)
    and only then read the firmware version over the BMC. Returns a per-node
    dict so the caller can render one row per machine.
    """
    job = get_job(job_id) or {'nodes': []}
    rows = {n['node']: n for n in job['nodes']}
    out = []
    for name in nodes:
        rec = inv.lookup_node(name)
        if not rec:
            out.append({'node': name, 'os_up': False, 'version': None,
                        'error': 'unknown node'})
            continue
        os_up = False
        try:
            os_up = probe.ping(rec['os_ip'])
        except Exception:
            os_up = False
        version = None
        if os_up:
            try:
                version = read_version(rec['bmc_ip'], kind,
                                       rec.get('bmc_user') or inv.BMC_USER,
                                       rec.get('bmc_pass') or inv.BMC_PASS)
            except Exception:
                version = None
        out.append({'node': name, 'os_up': os_up, 'version': version,
                    'restarted_at': rows.get(name, {}).get('restarted_at')})
    return out


def _node_state(job, name):
    for n in job['nodes']:
        if n['node'] == name:
            return n['state']
    return None


def job_before(job, name):
    for n in job['nodes']:
        if n['node'] == name:
            return n.get('version_before')
    return None


def _task_id_from(body):
    """Pull the Task id out of the push response, if the BMC returned one.

    Falls back to '0': the SOP polls TaskService/Tasks/0 directly, and this BMC
    numbers its update tasks from 0, so an id-less reply most likely means 0.
    """
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ('@odata.id', 'Id'):
            val = data.get(key)
            if isinstance(val, str):
                m = re.search(r'Tasks/(\d+)', val)
                if m:
                    return m.group(1)
                if val.isdigit():
                    return val
    m = re.search(r'TaskService/Tasks/(\d+)', body)
    if m:
        return m.group(1)
    return '0'


def _poll_task(bmc_ip, task_id, user, pw, job, name, limit=1800):
    """Block until the task reaches a terminal state or times out."""
    terminal = {'Completed', 'Exception', 'Killed', 'Cancelled',
                'Interrupted', 'Suspended'}
    start = time.time()
    last = 'Unknown'
    while time.time() - start < limit:
        info = read_task(bmc_ip, task_id, user, pw)
        last = info['state']
        pct = info['percent']
        # Never walk a node backwards. A BMC reset mid-poll makes read_task
        # return Unknown, and without this guard that overwrote an already
        # finished node back to 'flashing'.
        if _node_state(job, name) not in terminal:
            _set(job, name, state='flashing',
                 detail=f'{last}' + (f' {pct}%' if pct is not None else ''))
        if last in terminal:
            return last
        time.sleep(5)
    return last


