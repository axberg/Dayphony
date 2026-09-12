# Privacy model

Dayphony is local-first. It does not currently contain analytics, accounts, remote
APIs, telemetry, or an LLM integration.

## Data used

| Signal | Collected value | Deliberately excluded |
| --- | --- | --- |
| Active app | Application name | Window title, document text, keystrokes |
| Git | Number of changed entries | File contents, diffs, remotes, commit messages |
| Calendar | Counts and relative timing | Titles, notes, attendees, locations, calendar names |
| Manual control | Scene and numeric intensity | Identity or account information |

Signals are held in memory and are not persisted. They are reduced to a `DayState`
before reaching the music engine.

## Permissions

The frontmost-app adapter uses macOS `NSWorkspace` and does not request Accessibility
permission. Calendar access is disabled unless `--calendar` is supplied; macOS may
then request permission for the terminal or packaged application running Dayphony.

## Rules for future integrations

New adapters must be opt-in when they require permissions, document precisely which
fields they access, reduce data locally, avoid content collection by default, and
continue gracefully when access is unavailable.
