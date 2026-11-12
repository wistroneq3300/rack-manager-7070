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
