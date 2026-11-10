# ReShow — Agent Working Instructions

> Installation: this file is delivered as `AGENT.md` as requested. For standard Codex project instruction discovery, rename it to `AGENTS.md` and place it at the repository root. If an `AGENTS.md` already exists, merge these instructions without deleting unrelated guidance.

## Objective and scope

Implement the ReShow desktop frontend using HTML, CSS, and plain JavaScript. Follow the user's detailed implementation prompt and six approved visual references. Build Overview, Exhibits, Labels, Activity, Reports, and Staff access. Settings and mobile-specific layouts are out of scope.

The user's teammate owns the AWS database and backend. Use realistic mock data and asynchronous data-access functions so the backend can be connected later. Do not implement AWS services, a backend, real authentication, or hardware communication. Do not introduce TypeScript, React, Next.js, or a Node.js backend. A static development server is acceptable.

## Lead and implementation models

The requested team is:

| Responsibility | Model | Reasoning effort |
| --- | --- | --- |
| Lead, architecture, task allocation, integration decisions, final review | GPT-6 Astra | High |
| Routine implementation and focused fixes | GPT-6.1 Sol | Medium |
| Complex implementation, state management, difficult debugging | GPT-6.1 Sol | High |

Treat these as explicit model-selection preferences, not role-play instructions. Use the environment's supported model and effort controls when spawning workers. Resolve the exact available identifiers rather than inventing API or configuration syntax.

This Markdown file does not itself switch the running lead model or enable unavailable subagent capabilities. Check exposed capabilities once. If the requested routing is unavailable, report the limitation clearly. Continue useful work with available capabilities without claiming that the requested model is active, silently substituting a different model, or changing global configuration. Do not buy API access or create external agent infrastructure to satisfy this preference.

### Astra High: lead responsibilities

- Read the existing project, user requirements, screenshots, and checkpoint before assigning work.
- Decide the minimum useful architecture, shared data contract, page interfaces, design tokens, and ownership boundaries.
- Delegate substantial implementation to Sol workers. Keep architecture decisions, cross-tab consistency, integration decisions, and final acceptance with the lead.
- Make small integration fixes directly when delegating would create unnecessary delay.
- Review actual diffs and relevant browser evidence. A worker's completion message is not sufficient verification.
- Resolve conflicting assumptions promptly and keep the project moving to completion.

### Sol Medium: default implementation worker

Use Medium for well-specified page markup and styling, forms, tables, filters, navigation, simple dialogs, straightforward validation, documentation, and isolated fixes with a known cause.

### Sol High: complex implementation worker

Use High for shared mock-service/state logic, requested-versus-confirmed device updates, cross-tab synchronisation, date-range aggregation, heatmap/report consistency, persistence bugs, and difficult integration failures. Escalate a Medium task when concrete complexity or an unresolved failure warrants it. Do not use High automatically for every task.

## Delegation and file ownership

- Use subagents for independent implementation tasks. Start with two or three workers when useful and stay within the environment's available limit.
- Establish shared interfaces before parallel page implementation. Do not send every worker to independently design the data layer.
- Assign one writer per file at a time. Shared styles, routing, service interfaces, fixtures, and dependency files must each have an explicit owner.
- Workers may read other modules but must request shared changes from their owner instead of editing those files concurrently.
- If all pages currently share one file, split ownership safely or work sequentially. Do not parallelise overlapping edits.
- Use existing project conventions. Use separate Git worktrees only when supported and helpful. Do not assume agents have isolated filesystems.
- Workers must not spawn additional agents unless the lead explicitly delegates that authority.
- Do not duplicate the same implementation across workers or repeatedly restart completed work.

Give each worker a concise brief containing:

1. Goal and expected user-visible behaviour.
2. Assigned files and files they must not edit.
3. Relevant screenshot, requirements, and agreed service/data interfaces.
4. Required model/effort, when the environment supports selecting them.
5. Acceptance checks and dependencies.
6. Required return summary: changed files, verification performed, limitations, and integration notes.

## Implementation sequence

1. **Inspect and establish the foundation.** Review existing files and Git status. Define the app shell, shared design tokens, routing, data structures, mock clock, and asynchronous service interfaces. Implement or delegate the foundation before dependent pages.
2. **Implement independent page groups.** A useful split is Overview + Activity, Exhibits + Labels, and Reports + Staff access. Adjust this grouping to the real file boundaries. Use separate page styles scoped by page or component.
3. **Integrate.** Verify every page uses the same store/services, stable IDs, units, timestamps, and update semantics. Remove disconnected hard-coded copies of shared data.
4. **Review in the browser.** Compare each page with its approved reference at desktop widths. Fix visual and functional issues before final delivery.

These are implementation milestones, not approval gates. Continue authorised work without requesting confirmation at every milestone. Ask only when a material ambiguity cannot be resolved from the requirements or project.

## Frontend invariants

- Match the approved white-card, pale-grey, navy-and-teal visual style. Use real HTML controls, not screenshots as page content.
- Keep all six tabs functional and consistent. No dead buttons or misleading success notifications.
- Keep UI rendering separate from asynchronous data-access functions. UI modules must not manipulate storage independently of the shared data layer.
- Persist appropriate demo records through a versioned browser-storage format. Handle unavailable or malformed storage gracefully.
- Clearly label mock data, scans, commands, and acknowledgements. Frontend roles are demonstrations, not security enforcement.
- Distinguish saved drafts, requested changes, pending delivery, acknowledged state, and failures. Do not replace confirmed device state before acknowledgement.
- Exhibit assignment must update related screens consistently. Editing a label draft must not alter the acknowledged label.
- Keep staff access cards separate from exhibit-identification tags. Prevent duplicate active card assignments. Do not add a remote unlock action.
- Show stale readings when the device is offline. Missing data must not become zero. Proximity events must not be described as unique visitors.
- Derive activity summaries, heatmaps, reports, and exports from the same filtered records. Use a coherent demo clock and Hong Kong display time.
- Make CSV export work. Use a print stylesheet and an accurately labelled Print / Save as PDF action for browser PDF output.
- Include loading, empty, pending, failed, and offline demo states without adding a Settings tab.
- Validate forms, render user content safely as text, and provide keyboard access and visible focus indicators.
- Keep dependencies minimal and code understandable for a university group project. Do not broaden scope with unsolicited features.

## Verification and acceptance

Workers verify their assigned behaviour. Astra reviews integration and final acceptance.

- Exercise navigation, profile editing and assignment, label draft/send behaviour, lighting commands, staff-card registration/revocation, filters, report generation, and exports.
- Check persisted demo edits after refresh and coherent shared state across tabs.
- Check missing-data aggregation, duplicate identifiers, invalid limits, and pending/failed commands with focused tests where useful.
- Inspect all tabs at representative desktop widths such as 1280, 1440, and 1920 pixels. Resolve clipped controls, overlap, unreadable tables, and unnecessary horizontal scrolling.
- Check browser-console errors. Report any unavailable verification tools and do not claim checks that were not performed.
- Use focused verification for identified risks. Do not add a large test framework or repeatedly rerun unrelated tests just to increase coverage.

Completion means all six tabs work with consistent mock data, match the references reasonably, have no known blocking integration errors, and include a clear backend handoff. No backend connection is required for this milestone.

## Git, resumability, and delivery

- Preserve user changes and unrelated files. Do not reset, clean, overwrite, or revert work owned by another contributor.
- Use local Git when available. Let the lead coordinate commits and integration so workers do not stage each other's changes. Do not push or deploy unless requested.
- Maintain a concise `PROJECT_CHECKPOINT.md` at milestone boundaries and before handoff: objective, completed/in-progress/remaining work, decisions, active ownership, branch/worktree, uncommitted changes, verification results, blockers, and exact next action.
- On resume, inspect the checkpoint and actual files before doing work. Do not assume previously running workers are still active or restart finished milestones.
- Deliver source files, local running instructions, and a short frontend/backend handoff documenting service functions, data fields, units, timestamp conventions, and pending/acknowledgement semantics.
- Give concise progress updates with completed work, blockers, and next steps. Finish with what changed, what was verified, and remaining backend-dependent work.

## Setup references

- Codex instruction discovery: https://learn.chatgpt.com/docs/agent-configuration/agents-md
- Subagent configuration: https://learn.chatgpt.com/docs/agent-configuration/subagents

These references explain configuration mechanisms. The model and effort choices above are the user's requested project policy.
