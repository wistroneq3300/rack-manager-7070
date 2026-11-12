# KVM research probes

Not part of the application. Nothing here is imported by rackd or index.html.

- `kvm_probe.py` — logs into a BMC's web endpoint, opens `wss://<bmc>/kvm/0`
  and completes an RFB 3.8 handshake down to a full framebuffer update.

Usage:
```
.venv-test/bin/python scripts/research/kvm_probe.py
```
Set `BMC`, `USER`, `PW` at the top of the file. See `docs/kvm-research.md` for
the protocol details.

- `kvm_capture.py` — grabs one full frame and writes it to `/tmp/kvm_frame.png`.
- `kvm_input.py` — sends a single pointer move and reports whether the drawn
  cursor followed it; the safest way to prove the input path works without
  clicking anything.

Pointer/keyboard wire formats and the CDP evidence are in `docs/kvm-research.md`.
