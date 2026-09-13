# Security and privacy policy

## Reporting

Please report vulnerabilities privately through GitHub's **Security** tab instead of
opening a public issue. Include reproduction steps, affected versions, and potential
impact when possible.

## Supported versions

Dayphony is pre-release software. Security and privacy fixes are applied to the latest
version on the `main` branch.

## Sensitive areas

Reports involving these boundaries are especially valuable:

- calendar or application data leaving the local machine;
- collection of event content beyond documented timing aggregates;
- command or path injection through adapters or environment variables;
- OSC listeners exposed beyond loopback;
- unsafe audio levels or failure of pause/panic controls.
- local control sockets that can be accessed by another user or replaced by a
  second process;
- MCP tools that accept or retain prompt, response, task, or credential content.
