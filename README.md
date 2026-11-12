# Rack Manager — Neutrino Rack

Web UI for the Neutrino rack: live connectivity, per-node / per-tray power
actions, LiteOn power shelf control, firmware flashing, and an inventory editor.

Runs on port **7070**, independent of the 6969 PA platform.

---

## 1. Requirements

| Item | Notes |
| --- | --- |
| Python | 3.9+ — **standard library only**, no pip install |
| `ping` | ICMP reachability |
| `curl` | Redfish (BMC + power shelf) |
| `ssh` + `sshpass` | BMC/OS shell actions, hostname probing |
| `ipmitool` | `reboot` (IPMI over LAN) |

Install on Debian/Ubuntu:

```bash
apt-get update
apt-get install -y iputils-ping curl openssh-client sshpass ipmitool
```

> **Use `/usr/bin/python3`.** The `uv`-managed Python often on `PATH` may not
> resolve the system `sshpass`/`curl` paths and probes will silently fail.

Verify:

```bash
/usr/bin/python3 -c "import sys; print(sys.version)"
which ping curl sshpass ipmitool
```

---

## 2. Deploy

```bash
# 1. Put the code here (any path works; examples use /root/rack-manager)
cd /root/rack-manager

# 2. Check the inventory matches your rack
/usr/bin/python3 -c "import inventory as i; print(len(i.TRAYS), 'trays', sum(len(t['nodes']) for t in i.TRAYS.values()), 'nodes')"

# 3. Run once in the foreground to confirm it starts
/usr/bin/python3 rackd.py --port 7070
# -> Rack Manager on http://0.0.0.0:7070  (56 nodes, 3 power shelves)
```

Open `http://<host>:7070/`.

### Install as a systemd service (recommended)

```bash
cat > /etc/systemd/system/rack-manager-7070.service <<'EOF'
[Unit]
Description=Rack Manager (Neutrino 7070) - rack power control UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/rack-manager
Environment=PYTHONUTF8=1
Environment=RACK_INVENTORY=/root/rack-manager/inventory.json
ExecStart=/usr/bin/python3 /root/rack-manager/rackd.py --host 0.0.0.0 --port 7070
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rack-manager-7070
systemctl status rack-manager-7070
```

Adjust `WorkingDirectory`, `RACK_INVENTORY` and `ExecStart` if you deploy
elsewhere. `Restart=always` brings the UI back after a crash or OOM kill.

### Moving to a new server

1. Copy the whole directory, including `inventory.json` and `fw/` if present.
2. Install the packages from §1.
3. Confirm the new host can reach the BMC/OS subnet (`ping` one BMC).
4. Install the unit file, adjusting paths, then `enable --now`.

Nothing is tied to the current host except the paths in the unit file.

---

## 3. Configuration

### Inventory

`inventory.json` is optional:

| State | Result |
| --- | --- |
| absent | built-in `DEFAULT_TRAYS` (14 trays × 4 nodes) is used |
| present | the file **fully replaces** the built-in list |

Create it through **新增 Tray** rather than by hand — that flow SSHs into each
OS to read the real `hostname` and verifies the BMC before writing, so only
nodes that actually answered get stored.

> `inventory.json` holds **plaintext OS and BMC passwords**. It is in
> `.gitignore`; do not commit it, and keep permissions tight (`chmod 600`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `RACK_INVENTORY` | `<dir>/inventory.json` | inventory overlay path |

### Credentials

Defaults live in `inventory.py` (`BMC_USER`/`BMC_PASS`, `OS_USER`/`OS_PASS`).
Per-node overrides are stored in `inventory.json` and can be set in the
**新增 Tray** form.

---

## 4. Using the UI

### Rack 總覽
14 trays × 4 nodes. Per node: OS ping, BMC ping, BMC `PowerState`, and six power
actions. Per tray: the same six across all four nodes **in parallel**.
Auto-refreshes every 20 s. Dark/light toggle top right.

### Power Shelf
Powershelf-1/3/4 with per-unit watts and Power ON/OFF. Powershelf-2 is
decommissioned and absent.

Standby is neutral grey — it is a normal powered state, not a fault. Power Shelf
OFF cuts PSU output, but BMCs run from standby power and can still be woken over
Redfish.

### FW Flash
See §6.

### 新增 Tray
Enter tray number, OS/BMC IPs and credentials → **SSH 探測** → the real
`hostname` is read from each OS and each BMC is checked → **寫入清單**.
Saving is blocked unless every filled row answered over SSH. Entries can also be
deleted here (removes the entry only, never touches a machine).

---

## 5. Power commands

Mirrors `vera-cycle-ts1/vera_cycle/engine/cycle_engine.py:1536-1552`.

| Action | Command | Transport |
| --- | --- | --- |
| power_on | `/usr/bin/powerctrl.sh power_on` | BMC SSH |
| power_off | `/usr/bin/powerctrl.sh power_off` | BMC SSH |
| grace_off | `/usr/bin/powerctrl.sh grace_off` | BMC SSH |
| os_reboot (`reboot`) | `ipmitool power reset` | IPMI LAN |
| power_cycle | `/usr/bin/powerctrl.sh power_cycle` | BMC SSH |
| aux_cycle | `/usr/bin/stbypowerctrl.sh aux_cycle` | BMC SSH |
| **bmc_reset** | `POST .../Managers/BMC_0/Actions/Manager.Reset` `{"ResetType":"GracefulRestart"}` | Redfish |
| shelf on/off | `POST .../Actions/LiteOn.PowerOn\|PowerOff` | Redfish |

`power_off` cuts chassis power **immediately** without notifying the OS. Use
`grace_off` for a clean shutdown.

**OS Reboot vs BMC Reset — do not confuse these.** The UI labels them
**OS Reboot** and **BMC Reset** for exactly this reason.

| | OS Reboot | BMC Reset |
| --- | --- | --- |
| API action | `reboot` | `bmc_reset` |
| Restarts | the **host** | the **BMC** only |
| Host / OS | goes down | keeps running |
| Downtime | OS reboot time | BMC unreachable ~60-90 s |

After a BMC firmware flash you want **BMC Reset**, not **OS Reboot**.

---

## 6. Firmware flashing

Packages live in `fw/` (created on first upload, git-ignored). Upload via
**FW Flash → 選擇檔案**, or copy a `.fwpkg` in directly.

### Three flows — same endpoint, different details

All three POST to `/redfish/v1/UpdateService/update-multipart` with the same
`UpdateFile` / `UpdateParameters` multipart shape. What differs:

| | **CPLD** | **BIOS** | **BMC** |
| --- | --- | --- | --- |
| `Targets` | `[]` | `[]` | `[".../FW_BMC_0"]` |
| You must then run | **Aux Cycle** | **Power Cycle** | **BMC Reset** |
| Version from | `FW_CPLD_0` | `SBIOS_FW_0` | `FW_BMC_0` |

CPLD and BIOS both send an empty `Targets` array, letting the BMC decide which
component the package belongs to. Only BMC pins an explicit target.

> **Flashing never reboots, power-cycles or resets anything.** It pushes the
> package, waits for the update task, then reads the version back. Whatever the
> component needs next (Aux Cycle, Power Cycle, BMC restart) is a separate
> button you press yourself, so a destructive step is never buried in a
> progress bar. After a BMC flash press **BMC Reset**, not **OS Reboot** — the
> latter restarts the host (and your OS with it).

Notes from the SOP that the code follows:

- `-H 'Expect:'` on the upload (skips HTTP 100-continue; required for large bodies).
- The task id starts at **0** on this BMC; `0` is a valid id, not "missing".
- A BMC flash takes roughly 470 s; polling allows 30 min per node.
- A component can expose several inventory members (e.g. more than one CPLD);
  the result line reports whether their versions agree.
- The version stays the same until you run the follow-up action. That is
  expected, not a failed flash.

### UI flow

1. Pick the kind (CPLD / BMC / BIOS).
2. Pick targets — **全選**, a whole **tray**, or individual **nodes**.
3. Choose the uploaded package.
4. **預覽** reads the current version from each BMC (read-only).
5. **執行刷寫** → confirmation modal → job runs.

Nodes are flashed **one at a time** by default. The vendor SOP notes that
pushing to several BMCs at once works; if you want that, raise
`PARALLEL_NODES` at the top of `rack_flash.py`. It is serial by default so a
wide mistake hits one machine, not fifty-six.

Per-node states: `queued → reading → uploading → flashing → ok` / `failed`,
with `version_before → version_after` in the result table.

### Firmware inventory on a live BMC
`/redfish/v1/UpdateService/FirmwareInventory` reports `FW_BMC_0`, `FW_CPLD_0`,
`FW_ERoT_BMC_0`, `SBIOS_FW_0/1`, `SBIOS_FMC_0/1`, `FW_BlueField_SMA_0`,
`FW_ProcessorModule_SMA_0`, `FW_StorageBackplane_SMA_0`, `FW_NVMe_E1S_SSD0/1`.

---

## 7. Safety model

- **Every** power and flash action needs a confirmation token from a
  `?dry_run=1` request for the **same** payload. Tokens are single-use and expire
  after 120 s.
- Only one power/flash action runs at a time (HTTP 429 otherwise).
- Tray power actions require typing the tray name; shelf Power OFF requires
  typing `Neutrino`; the confirm button stays inert for 1.5 s.
- Read-only paths (ping, `PowerState`, shelf `status`, flash preview) need no
  token.
- Uploads: extension allow-list, 512 MB cap, atomic write, path traversal
  rejected.

---

## 8. API

`POST /api/<route>`, JSON body. `?dry_run=1` returns a token instead of
executing; the real call passes `?token=<token>`.

| Route | Purpose |
| --- | --- |
| `GET /api/status` | full rack state |
| `GET /api/inventory` | trays + nodes |
| `POST /api/node/power` | `{node, action}` |
| `POST /api/tray/power` | `{tray, action}` |
| `POST /api/shelf/power` | `{shelf, action}` |
| `POST /api/tray/probe` | preview hostnames (no write) |
| `POST /api/tray/add` | write a tray (`force:1` to replace) |
| `POST /api/tray/remove` | `{tray}` |
| `GET /api/flash/packages` | list uploaded firmware |
| `POST /api/flash/upload` | `{name, data_b64}` |
| `POST /api/flash/delete` | `{name}` |
| `POST /api/flash/dryrun` | preview versions for `{kind, targets}` |
| `POST /api/flash/run` | start a flash job |
| `POST /api/flash/job` | `{id}` — poll job progress |

`targets` accepts `{"mode":"all"}`, `{"mode":"tray","trays":[0,3]}`, or
`{"mode":"node","nodes":["Neutrino1-n2"]}`.

---

## 9. Files

| File | Purpose |
| --- | --- |
| `rackd.py` | HTTP server, routing, confirmation tokens |
| `rack_probe.py` | ping / Redfish / SSH / IPMI actions |
| `rack_flash.py` | firmware recipes, task polling, package store |
| `inventory.py` | default inventory + `inventory.json` overlay |
| `index.html` | single-file UI (dark + light) |
| `fw/` | uploaded firmware packages (git-ignored) |
| `inventory.json` | local inventory overlay (git-ignored) |

---

## 10. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| all BMCs down, pings fail | wrong subnet / no route on the new host |
| probe `auth failed` | wrong OS/BMC credentials |
| probe works in CLI but not in UI | running under `uv` Python — use `/usr/bin/python3` |
| flash `push failed: HTTP 401` | BMC credentials |
| flash task `Exception` | package does not match the component |
| CPLD version unchanged | expected — run **Aux Cycle** first |

Logs: `journalctl -u rack-manager-7070 -f`
