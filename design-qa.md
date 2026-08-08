# StaffDeck SD1 UI QA

Source:
- Figma file `03XlzJQ1dFYdlDWBR4Mlg4`, page node `0:1`
- Covered SD1 frames: `1:2892`, `1:765`, `1:1462`, `1:2165`, `1:68`, `1:6578`, `1:3713`, `1:3425`, `1:5883`, `1:7470`, `1:3975`, `1:4614`, `1:4286`, `1:5013`, `1:5409`

Implementation checkpoints:
- Chat gallery and selected-session states: `/private/tmp/StaffDeck-sd1-qa/01-chat-gallery-switch.png` through `/private/tmp/StaffDeck-sd1-qa/15-chat-stopped-or-idle.png`
- Figma reference exports: `/private/tmp/StaffDeck-figma-sd1/01-figma-1-2892.png` through `/private/tmp/StaffDeck-figma-sd1/15-figma-1-5409.png`
- Visual comparison contact sheet: `/private/tmp/StaffDeck-sd1-visual-diff/overview-15-scenarios.png`
- Visual comparison report: `/private/tmp/StaffDeck-sd1-visual-diff/visual-diff-report.json`
- Enterprise employee roster: `/private/tmp/StaffDeck-sd1-qa/02-enterprise-agents-collapsed.png` (legacy filename; Figma node `1:765` is expanded), `/private/tmp/StaffDeck-sd1-qa/05-enterprise-agents-expanded.png`, `/private/tmp/StaffDeck-sd1-qa/09-enterprise-agents-collapsed-reference.png`
- Enterprise employee profile: `/private/tmp/StaffDeck-sd1-qa/06-enterprise-dashboard-expanded.png`, `/private/tmp/StaffDeck-sd1-qa/10-enterprise-dashboard-collapsed.png`
- Dark and responsive checks: `/private/tmp/StaffDeck-sd1-qa/20-dark-chat-input.png`, `/private/tmp/StaffDeck-sd1-qa/21-dark-enterprise-dashboard.png`, `/private/tmp/StaffDeck-sd1-qa/22-mobile-chat-input.png`, `/private/tmp/StaffDeck-sd1-qa/23-mobile-enterprise-agents.png`
- Machine-readable report: `/private/tmp/StaffDeck-sd1-qa/report.json`

Browser QA summary:
- 23 browser states checked: all 15 SD1 frames, 4 enterprise regression pages, 2 dark-mode pages, and 2 narrow-screen pages.
- Figma metadata was checked for all 15 nodes. Important interaction-state corrections: `1:2165` is the chat gallery employee-filter dropdown state, `1:4286` is an expanded-sidebar model-dropdown input state, and only `1:5883` is the collapsed enterprise employee roster; `1:765` and `1:68` are expanded-sidebar roster states.
- Formal API data path used: `/api/auth/login`, `/api/chat/agents`, `/api/chat/sessions`, `/api/enterprise/agents`, and page-owned enterprise/chat API calls.
- Layout checks passed: no horizontal overflow, no visible error toast, no pageerror, key 1440x900 chat dimensions matched SD1 (`72/220` sidebar, `56` header, `570` empty state, `1078/960 x 100` composer).
- In-app Browser checks verified `/chat/gallery` at expanded `220px` sidebar with `所有员工` active, visible employee-filter dropdown, top-right `切换主题`/`刷新页面` only, `/enterprise/agents` summary labels, model-dropdown input state, and dark enterprise dashboard inversion.
- Chat polling was constrained to current/running sessions to prevent request storms and stacked `Failed to fetch` errors.
- Enterprise knowledge page now suppresses non-visible OKF version probes during automatic page load.
- Follow-up in-app browser checks matched SD1 top-right actions (`sun`/`refresh`), collapsed chat bottom icon, and composer shallow border/shadow.
- Dark-mode checks verified enterprise dashboard/agents and chat surfaces all invert their main content areas, not only the sidebars.

Automated checks:
- `npm --prefix frontend-enterprise run build` -> passed
- `node /private/tmp/codex-playwright/sd1-qa.mjs` -> 23 total, 0 failures

Final result: passed

---

# Design QA — Operations Dashboards

## Evidence

- Reference overview: `C:/Users/Administrator/.codex/visualizations/2026/08/07/staffdeck-ui-audit/06-business-detail.png`
- Implementation overview: `C:/Users/Administrator/.codex/visualizations/2026/08/07/staffdeck-ui-audit/08-operations-implementation.png`
- Reference today view: `C:/Users/Administrator/.codex/visualizations/2026/08/07/staffdeck-ui-audit/07-daily-detail.png`
- Implementation today view: `C:/Users/Administrator/.codex/visualizations/2026/08/07/staffdeck-ui-audit/09-today-implementation.png`
- Responsive checks: `10-today-1024.png` and `11-operations-1024.png`

## Test State

- Browser: Codex in-app browser
- Viewports: 1280 × 720 and 1024 × 768 CSS pixels
- Account: authenticated local administrator
- Data: live local API data for the selected employee
- Known shell state: the existing missing-model configuration notice is visible

## Comparison

The implementation preserves the selected design's information hierarchy: a compact title area, four operating metrics, an attention queue, a completion trend, and a reusable capability-capture summary. The daily view keeps the three-part structure of active work, confirmations, and weekly capture. Both screens use StaffDeck's existing sidebar, header controls, typography, borders, neutral surfaces, and restrained status colors.

Visible differences are intentional. The reference contains illustrative QQQ data, while the implementation renders actual tenant data and truthful empty states. The global model-configuration notice belongs to the existing application shell and was not hidden for these routes.

## Findings

- P0 blockers: none
- P1 functional or layout defects: none
- P2 visual mismatches: none
- Navigation, period selection, employee switching, empty states, and responsive layout verified
- Browser console errors: 0

final result: passed

---

# Design QA — Capability Evolution

- Added an evidence-driven `learning → proposal → review → apply → reuse` loop for agent-bound General Skills.
- Negative feedback produces a proposal only when the same session contains a real `claude_skill_loaded` event for an active bound Skill.
- Generating a proposal never edits the Skill. Applying requires employee-owner/admin permission and an exact base-content match, preventing stale proposals from overwriting newer edits.
- The operations dashboard counts only approved, applied learning as “沉淀”; assigning a resource is no longer presented as learning.
- Browser E2E verified proposal generation, Diff preview, approval, Skill write, later reuse counting, exact test-fixture cleanup, truthful empty states, and zero console warnings/errors.
