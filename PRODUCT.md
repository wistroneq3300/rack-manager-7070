# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Data center and validation operators monitoring a Neutrino rack over long sessions and issuing safety-sensitive power, shelf, inventory, and firmware operations.

## Product Purpose

Provide one operational surface for monitoring 14 trays and 56 nodes, inspecting hardware and firmware, managing inventory, controlling node/tray/shelf power, and running explicit firmware workflows. Success means operators can understand rack state quickly and execute the intended action without confusing host, BMC, tray, or shelf scope.

## Positioning

The product combines live OS/BMC connectivity, BMC-reported power state, the rack's exact seven-action power vocabulary, hardware/firmware inspection, and confirmation-token-gated execution in one rack-specific interface.

## Operating Context

Operators use the interface as a long-running control console. Rack status refreshes every 20 seconds. Power and firmware actions are deliberate, guarded workflows; firmware follow-up actions remain manual and visible rather than running automatically.

## Capabilities and Constraints

- Preserve every existing API URL, HTTP method, JSON schema, action key, callback meaning, confirmation token, guard delay, and refresh interval.
- Preserve all seven node and tray actions: Power On, Power Off, Graceful Off, OS Reboot, Power Cycle, Aux Cycle, and BMC Reset.
- Preserve complete OS and BMC IP addresses, Power Shelf control, Add Tray/Node, HW/FW inspection, package upload, target selection, preview, flash execution, job polling, and version results.
- Backend Python modules are read-only for this redesign.
- No large UI framework, external CDN, or new runtime dependency.
- Off is a normal power state. Unknown, Offline, Partial, and true failures require explicit text and cannot rely on color alone.

## Brand Commitments

Keep the Neutrino Rack Manager name and the unmodified official Wistron logo asset. The requested visual direction is a premium enterprise control surface with deep graphite and blue-black neutrals, one low-saturation cyan/teal accent, and semantic green, amber, and red used only for status and risk.

## Evidence on Hand

- Existing application and workflows in `index.html`.
- Backend route and safety behavior in `rackd.py`, `rack_probe.py`, `rack_hw.py`, `rack_flash.py`, and `inventory.py`.
- Operational documentation in `README.md`.
- Official Wistron logo at `static/img/wistron-official.svg`.

## Product Principles

- State before action: name and status dominate; controls remain available but visually subordinate.
- Safety is visible: scope, consequence, and confirmation remain explicit at every destructive step.
- Dense without being cramped: show the full rack while keeping IP addresses and state text readable.
- Refresh without surprise: operator selections and expanded controls survive periodic DOM rebuilds where relevant.
- Semantic color only: meaning must be carried by text first and color second.

## Accessibility & Inclusion

Normal text targets WCAG AA contrast of at least 4.5:1; meaningful controls, boundaries, and states target at least 3:1. Keyboard focus, reduced motion, non-color state labels, and usable layouts across the requested desktop sizes and zoom levels are required.
