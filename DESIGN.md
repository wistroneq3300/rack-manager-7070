---
name: Neutrino Rack Manager
description: A calm, safety-first fabrication control surface for dense rack operations.
colors:
  dark-canvas: "#0b1015"
  dark-panel: "#12191f"
  dark-panel-raised: "#182129"
  dark-hover: "#1c272f"
  dark-border: "#5b6b73"
  dark-border-soft: "#243139"
  dark-divider: "#33434d"
  dark-text: "#edf3f3"
  dark-text-secondary: "#b4c0c4"
  dark-text-muted: "#8c9ca3"
  dark-sidebar: "#0d151b"
  dark-accent: "#62b9b3"
  dark-accent-strong: "#85cbc6"
  dark-accent-wash: "rgba(98,185,179,.12)"
  dark-accent-ink: "#071315"
  dark-focus: "#85cbc6"
  dark-success: "#70c697"
  dark-success-wash: "rgba(112,198,151,.12)"
  dark-warning: "#d7b36d"
  dark-warning-wash: "rgba(215,179,109,.13)"
  dark-danger: "#e18787"
  dark-danger-wash: "rgba(225,135,135,.12)"
  dark-danger-ink: "#161010"
  light-canvas: "#e9eef0"
  light-panel: "#fbfcfc"
  light-panel-raised: "#f0f4f4"
  light-hover: "#e6eeee"
  light-border: "#798d94"
  light-border-soft: "#d2dde0"
  light-text: "#17282e"
  light-text-secondary: "#405860"
  light-text-muted: "#526970"
  light-sidebar: "#e3eaec"
  light-accent: "#176f75"
  light-accent-strong: "#0f5c62"
  light-accent-wash: "rgba(23,111,117,.10)"
  light-accent-ink: "#f7ffff"
  light-focus: "#0f5c62"
  light-success: "#287249"
  light-success-wash: "rgba(40,114,73,.11)"
  light-warning: "#855d16"
  light-warning-wash: "rgba(133,93,22,.12)"
  light-danger: "#a13e3e"
  light-danger-wash: "rgba(161,62,62,.10)"
  light-danger-ink: "#fff7f7"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans TC', Roboto, sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "normal"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans TC', Roboto, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: ".2px"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans TC', Roboto, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: ".05px"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans TC', Roboto, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  control:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans TC', Roboto, sans-serif"
    fontSize: "13px"
    fontWeight: 560
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans TC', Roboto, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: ".65px"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
rounded:
  xs: "4px"
  sm: "6px"
  control: "8px"
  nav: "9px"
  compact-panel: "10px"
  surface: "12px"
  dialog: "14px"
  pill: "999px"
spacing:
  2xs: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "28px"
components:
  button-default:
    backgroundColor: "{colors.dark-panel-raised}"
    textColor: "{colors.dark-text-secondary}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "7px 13px"
    height: "34px"
  button-primary:
    backgroundColor: "{colors.dark-accent}"
    textColor: "{colors.dark-accent-ink}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "7px 13px"
    height: "34px"
  button-danger:
    backgroundColor: "{colors.dark-danger-wash}"
    textColor: "{colors.dark-danger}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "7px 13px"
    height: "34px"
  input:
    backgroundColor: "{colors.dark-panel-raised}"
    textColor: "{colors.dark-text}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "8px 11px"
    height: "40px"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.dark-text-muted}"
    typography: "{typography.control}"
    rounded: "{rounded.nav}"
    padding: "10px 11px"
    height: "42px"
  status-chip:
    backgroundColor: "{colors.dark-panel-raised}"
    textColor: "{colors.dark-text}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
    height: "26px"
  tray-container:
    backgroundColor: "{colors.dark-panel}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.surface}"
    padding: "0"
---

# Design System: Neutrino Rack Manager

## Overview

**Creative North Star: "The Fabrication Control Surface"**

Neutrino Rack Manager should feel like the quiet control layer of a semiconductor fabrication line: engineered, continuously observable, and deliberately uneventful until attention is required. Deep graphite and blue-black planes keep long sessions comfortable, while cool dividers and tabular facts turn a dense rack into an orderly instrument rather than a collection of dashboard cards.

The system is state-first and safety-first. Names, addresses, connectivity, and explicit text status remain visible before any control is considered; routine controls are restrained, and consequential actions only gain semantic warning color when revealed or confirmed. Light and dark themes share the same hierarchy and interaction logic rather than becoming separate visual identities.

Motion is brief and functional: it acknowledges state changes, loading, or notification dismissal. Surfaces remain grounded at rest, without decorative glow, gradients, or hover lift. The design rejects bright status mosaics, simultaneous rainbow actions, and ambiguous icon-only state.

**Key Characteristics:**

- Graphite and blue-black tonal layering with a cool, low-saturation teal interaction accent.
- Calm operational density: compact controls, legible full addresses, and stable rack geometry.
- Explicit status text supported by semantic color, never replaced by it.
- Flat, hairline-separated surfaces with depth reserved for overlays and transient layers.
- Progressive disclosure for power actions, followed by scope-and-consequence confirmation.
- Tabular numerals and monospace facts where operators compare addresses, counts, commands, or versions.

## Colors

The palette is a cold industrial neutral field with one muted teal interaction voice; green, amber, and red are scarce operational signals, not decoration.

### Primary

- **Instrument Teal** (`dark-accent` / `light-accent`): marks active navigation, focus-adjacent emphasis, selected firmware targets, and primary execution controls.
- **Signal Teal** (`dark-accent-strong` / `light-accent-strong`): strengthens teal text and hover states without introducing a second accent hue.
- **Teal Wash** (`dark-accent-wash` / `light-accent-wash`): supplies selected and disclosed backgrounds while preserving the neutral field.

### Neutral

- **Blue-Black Canvas** (`dark-canvas`) and **Cool Alloy Canvas** (`light-canvas`): the page fields for long-running dark and light sessions.
- **Graphite Panel** (`dark-panel`) and **Instrument White Panel** (`light-panel`): primary rack, form, and modal surfaces.
- **Raised Graphite** (`dark-panel-raised`) and **Pale Equipment Surface** (`light-panel-raised`): headers, controls, chips, and facts nested inside a primary panel.
- **Cold Steel Borders** (`dark-border`, `dark-border-soft`, `dark-divider`) and their light equivalents: structural separators that define density without ornament.
- **High-Contrast Readout** (`dark-text` / `light-text`): names, values, and primary information.
- **Secondary Readout** (`dark-text-secondary` / `light-text-secondary`): controls and supporting copy.
- **Muted Instrument Label** (`dark-text-muted` / `light-text-muted`): captions, labels, and lower-priority metadata.

### Tertiary

- **Operational Green** (`dark-success` / `light-success`): healthy connectivity, powered-on state, successful probes, and success feedback only.
- **Attention Amber** (`dark-warning` / `light-warning`): unknown, partial, offline, or cautionary state only.
- **Interlock Red** (`dark-danger` / `light-danger`): destructive power actions, true failures, and irreversible-risk messaging only.

### Named Rules

**The One Instrument Voice Rule.** Teal is the only non-semantic interaction accent; do not add another brand or navigation hue.

**The Text Before Hue Rule.** Every operational state is named in text first. Color may reinforce `Online`, `Offline`, `Partial`, `On`, `Off`, `Unknown`, `WARN`, or `ERR`, but may never carry that meaning alone.

**The Earned Alarm Rule.** Green, amber, and red appear only when live state, feedback, or action risk earns them; they are never used to decorate navigation, charts, or generic hierarchy.

## Typography

**Display Font:** System UI (`-apple-system`, BlinkMacSystemFont, Segoe UI, Noto Sans TC, Roboto, sans-serif)

**Body Font:** System UI (`-apple-system`, BlinkMacSystemFont, Segoe UI, Noto Sans TC, Roboto, sans-serif)

**Label/Mono Font:** Native UI monospace (`ui-monospace`, SFMono-Regular, Menlo, monospace)

**Character:** The typography is compact, neutral, and bilingual-ready. Weight and spacing distinguish operational roles; a separate monospace register makes addresses, commands, versions, and numeric readouts easy to compare without making the entire interface feel like a terminal.

### Hierarchy

- **Display** (700, 20px, 1.25): hardware dialogs and the highest local modal title.
- **Headline** (700, 18px, 1.35): major workflow cards such as firmware and inventory management.
- **Title** (700, 15px, 1.4): tray, node, shelf, and section identity.
- **Body** (400, 15px, 1.5): application copy; explanatory workflow paragraphs tighten to 13–13.5px and stay near 82 characters when the surface permits.
- **Control** (560, 13px, 1.5): buttons, navigation, and interactive labels.
- **Label** (600, 11px, .65px tracking): uppercase scope keys, table headings, and field labels.
- **Mono** (400, 13px, 1.45): complete IP addresses, commands, firmware versions, and machine results; enable tabular numerals in numeric summaries.

### Named Rules

**The Readout Integrity Rule.** Never truncate operational addresses, node names, versions, or confirmation scope merely to preserve a decorative composition; wrap safely or widen the fact column.

**The Quiet Hierarchy Rule.** Build hierarchy through compact steps in weight, spacing, and tone. Avoid oversized headings that displace rack state from the first viewport.

## Layout

The desktop shell uses a fixed 232px operational rail and a sticky command bar with a 66px minimum height. Main content spans the available workspace up to 2000px and uses 28px horizontal padding; the rack itself is a vertical sequence of trays separated by 16px.

Each tray is a stable operating row: a 62px header over four equal node columns. Node cells use 16px internal padding, a 1px divider grid, and a 220px minimum height so facts and actions line up across a tray. Summary and shelf grids use a 16px gap; workflow containers generally use 22–24px internal padding. The spacing rhythm is compact and mostly advances through 4, 6, 8, 12, 16, 24, and 28px steps.

At 1320px, the rail narrows to 214px and content padding drops to 22px. At 1120px, trays become two columns and aggregate top-bar chips recede. At 760px, the rail becomes a horizontal four-item navigation strip, tray nodes stack to one column, action panels return to document flow, and content padding becomes 12px. Full addresses and explicit state labels remain intact at every breakpoint.

**The Stable Rack Rule.** Responsive behavior may reflow node columns, but it must not reorder scope, hide operational identity, or collapse distinct OS, BMC, and Power state into one ambiguous summary.

## Elevation & Depth

The system is flat by default. Depth comes primarily from tonal layering—canvas, panel, nested equipment surface—and cool 1px separators. Rack trays, shelf cards, and workflow cards carry no resting shadow or hover lift. The only strong shadow belongs to overlays, action popovers, modals, and toasts that must sit above operational state.

### Shadow Vocabulary

- **Dark overlay** (`0 18px 36px rgba(0,0,0,.46)`): modals, tray action popovers, and toasts.
- **Light overlay** (`0 16px 34px rgba(25,48,56,.16)`): modal, popover, and toast separation in light mode.

### Named Rules

**The Grounded Equipment Rule.** Persistent operational surfaces stay flat at rest. Do not add card lift, glow, glass blur, or decorative shadow to trays and nodes.

**The Overlay Earns Depth Rule.** Strong shadow is reserved for content that temporarily interrupts or floats above the operating plane.

## Shapes

The form language is square-shouldered with controlled softening. Major equipment surfaces use 12px corners; dialogs use 14px; compact panels use 9–10px; controls use 8px; state chips use 6px; micro-labels and hardware tags use 4px. Full pills are exceptional and limited to intrinsically compact, non-block controls.

Borders are visible, cool, and structural. A 1px line groups rows and separates adjacent node cells; selection changes border tone and background wash rather than changing silhouette. Circular geometry belongs to tiny status dots, spinners, and the theme mark—not primary containers.

**The Square Shoulder Rule.** Reuse the observed 4–14px corner ladder. Do not turn trays, node cells, workflow cards, or ordinary buttons into capsules.

## Components

### Buttons

- **Shape:** Compact rectangular controls with gently softened corners (8px); mini controls use 7px and a 30px minimum height.
- **Primary:** Instrument Teal with theme-appropriate high-contrast ink, 34px minimum height, and 7px × 13px padding. Reserve it for the safe next step or closure, not every positive-looking action.
- **Hover / Focus:** Hover changes background, border, or text tone over 140ms; focus uses a visible 3px theme-aware solid ring with a 2px offset. Active state uses the teal wash. Disabled controls use a neutral surface and muted text without reducing the whole control's opacity.
- **Secondary:** Raised neutral surface, secondary text, and a visible cool border. Hover introduces the teal border without lifting the control.
- **Danger:** Red text over a red wash with a restrained mixed border. Strong red fill appears only on direct hover or at the final destructive confirmation.

### Chips

- **Style:** Summary chips use 7px corners, a nested neutral surface, cool border, and tabular values. Status chips use 6px corners and always pair a small uppercase label with an explicit value.
- **State:** Healthy or powered-on values become green; offline or unknown values become amber; normal Power Off remains neutral rather than reading as failure.

### Cards / Containers

- **Corner Style:** Major tray and workflow surfaces use gently squared 12px corners.
- **Background:** Primary panel over the page canvas, with the nested equipment tone for tray headers, plans, defaults, and control groups.
- **Shadow Strategy:** Flat for persistent surfaces; see the overlay-only depth vocabulary above.
- **Border:** One cool structural line, softened where adjoining surfaces already create separation.
- **Internal Padding:** 16px for node cells, 17–18px for shelf cards, and 22–24px for multi-step workflow containers.

### Inputs / Fields

- **Style:** Neutral nested background, visible 1px border, 8px radius, 40px target height, and 8px × 11px padding. Technical cells use the monospace role.
- **Focus:** Remove the browser outline only where a teal border and global 3px focus-visible ring remain present.
- **Error / Disabled:** Error text uses Interlock Red and explicit language. Disabled fields remain present on a neutral surface with muted text and no whole-control opacity reduction.

### Navigation

The rail uses 42px-high, 9px-corner rows with secondary text and a small outlined letter marker. Hover adds a neutral equipment surface and soft border; active state uses the teal wash and strong teal text, plus `aria-current`. On narrow screens the same four destinations become an equal-width horizontal strip rather than a hidden drawer.

### Progressive Action Disclosure

Power operations live inside native `details` disclosures. The summary names the scope and shows the exact action count; the open state uses the teal border and wash. Node actions expand inline in a two-column grid, while tray actions use a right-aligned floating panel on desktop and return inline on narrow screens. Disclosure state persists across the 20-second rack rebuild.

### Confirmation Dialog

The dialog repeats the action, target, scope, transport or command, consequence, and any required confirmation token before execution. Destructive confirmation uses red only at the risk block and final action. The overlay is opaque enough to separate attention from the live rack while leaving the underlying operating context recognizable.

### Toasts

Toasts are compact neutral panels with explicit `OK`, `ERR`, `WARN`, or `INFO` text, a matching semantic border, an authored close control, and a brief exit transition. They report both dispatch and outcome so an action is never silently lost.

## Do's and Don'ts

### V3 operational refinements

- Use authored local 24×24 line SVGs at 16px (14px in compact actions), with 1.8px strokes, `currentColor`, 6–8px label spacing and `aria-hidden="true"`. Keep text on every action. The shared helper owns the paths; no icon CDN or build step is introduced.
- Navigation uses server, plug, CPU and server-management shapes. Power actions keep all seven original labels and keys. Refresh and theme controls use refresh, moon and sun shapes. Firmware controls use upload, trash, scan, CPU, wrench, history, chevron and information shapes.
- Firmware flash is parallel. A completed flash, requested activation, returned device and verified version comparison are separate facts. `activated` alone never proves the version changed. Without a readable before/after pair, show verification as incomplete. With a changed pair, say “版本變更已驗證” and disclose that no package-expected version was compared; the existing API supplies no expected-version field.
- Each Job keeps its ID, firmware type, package, creation time, summary, original node table and all three manual activation controls. Recommended activation is CPLD → Aux Cycle, BMC → BMC Reset, BIOS → Power Cycle. Recommendation never changes eligibility or removes another action.
- Summary counts deduplicate by node identity. Total counts all rows; flashed counts `state === 'ok'`; failed counts `state === 'failed'`; pending activation counts successful rows without activated/restarted/stuck/local-waiting flags; verified counts activated successful rows with both readable versions and a changed value. Pending and verified are subsets of flashed, not additional mutually exclusive totals.
- The activation confirmation displays the exact target array computed by the existing executor. No power or token request occurs until confirmation. The captured array is used for the subsequent original execution, even while Job polling updates the visible card.
- Job cards collapse without dismissal or data deletion. Server-retained jobs are available through existing job-list/read endpoints. Original persistence, resume, polling and dismissed-record storage remain in place. Raw backend messages stay escaped and inspectable; their text is not rewritten as a UI verdict.
- PA reference inspected locally: `pa-cycle-platform-premium-ui-v1/DESIGN.md`, its `app/static/css/cycle-workspace.css`, and `pa-server-manager-review/static/css/premium.css`. Adopt the shared engineering hierarchy, written status, system Chinese/Latin typography and local table overflow. Preserve Neutrino's existing flat teal palette; do not import PA's gradients, 3D, green brand actions or business logic.

### Do:

- **Do** keep names, complete addresses, OS/BMC connectivity, and Power state ahead of controls in the reading order.
- **Do** use nested neutral tones and 1px dividers to organize dense rack information before reaching for shadow.
- **Do** preserve explicit `Healthy`, `Partial`, `Offline`, `Online`, `Off`, and `Unknown` language beside any semantic color.
- **Do** reveal the seven power actions progressively and repeat scope plus consequence at confirmation.
- **Do** preserve keyboard focus, reduced-motion behavior, tabular numerals, and the dark/light theme role mapping.
- **Do** reserve monospace typography for facts operators compare exactly: IPs, commands, versions, and raw results.

### Don't:

- **Don't** decorate navigation, generic cards, or page chrome with green, amber, or red.
- **Don't** present all power operations as a simultaneous rainbow button row.
- **Don't** add hover lift, neon glow, glass blur, or ornamental gradients to persistent equipment surfaces.
- **Don't** encode state with dots or color alone, and don't style normal Power Off as an error.
- **Don't** use oversized marketing typography, pill-shaped cards, or airy spacing that pushes a complete tray out of the operating viewport.
- **Don't** hide destructive scope, transport, confirmation requirements, or follow-up steps behind icon-only controls.
