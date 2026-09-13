# Privacy model

Dayphony is local-first. It has no analytics, accounts, remote APIs, or LLM calls.
Its local environmental telemetry is used only to shape music and is not sent or
persisted.

## Data used

| Signal | Collected value | Deliberately excluded |
| --- | --- | --- |
| Active app | Application name | Window title, document text, keystrokes |
| System | Aggregate CPU and memory load | Process arguments, process contents, files |
| Open apps | Names and category counts of visible GUI apps | Window titles, documents, interaction history |
| Git | Number of changed entries | File contents, diffs, remotes, commit messages |
| Calendar | Counts and relative timing | Titles, notes, attendees, locations, calendar names |
| AI activity (opt-in) | Timestamps and numeric token counters from recent local Codex/Claude records | Prompts, responses, tool arguments, file content |
| Agent control | Agent name, typed reason, numeric priority | Prompt, response, task text, arbitrary messages |
| Manual control | Scene and numeric intensity | Identity or account information |

Signals are held in memory and are not persisted. They are reduced to a `DayState`
before reaching the music engine.

## Permissions

The frontmost-app adapter uses macOS `NSWorkspace` and does not request Accessibility
permission. Calendar access is disabled unless `--calendar` is supplied; macOS may
then request permission for the terminal or packaged application running Dayphony.
AI token-rate estimation is disabled unless `--ai-telemetry` is supplied. It uses
best-effort parsers for private local client formats, never sends those records over
the network, and degrades to zero if they cannot be read.

The MCP adapter is local stdio and forwards typed events over a permission-restricted
Unix socket. It contains no network transport, credential field, or arbitrary text
parameter. Signals are consumed from memory and are not persisted.

## Rules for future integrations

New adapters must be opt-in when they require permissions, document precisely which
fields they access, reduce data locally, avoid content collection by default, and
continue gracefully when access is unavailable.
