# Conversational behavior

The bridge's conversational behavior is configured in `bridge_config.py`; the compatibility facade in `bridge.py` re-exports that configuration for existing imports. This page covers supported behavior and operator validation. Runtime code and tests remain the source of truth.

## Expected behavior

At a high level, the bridge is intended to:

- wait for the user to speak before producing first-turn audio;
- answer directly and keep spoken responses structured;
- use available tools only through the runtime's configured tool boundary;
- describe video or screen content only when an authenticated frame was actually received for the current interaction;
- keep vocal-expression markup bounded so it does not dominate speech;
- preserve the configured conversational style while respecting runtime security, privacy, confirmation, and permission checks.

Configuration wording alone does not prove that a capability works. In particular, the bundled frame paths remain blocked on current `main`; see [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9).

## Updating behavior

Edit the conversational behavior configuration in `bridge_config.py`. Do not edit only the `bridge.py` facade; it contains compatibility imports rather than the active definition.

After a reviewed change:

1. Compile-check both the source module and facade: `python -m py_compile bridge_config.py bridge.py`.
2. Restart the gateway: `systemctl --user restart hermes-gateway`.
3. Test the affected behavior in a controlled voice session.
4. Verify that security, privacy, identity, confirmation, and permission boundaries remain enforced by runtime code.

For video behavior, do not describe the bundled `/frame` clients as operational until the startup and authentication work in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9) is fixed and tested.

## Dynamic context

The static behavior configuration can be supplemented with per-session Honcho context. The feature is controlled by `VOICE_LIVE_HONCHO_CONTEXT`; `HONCHO_CONTEXT_ENABLED` is the corresponding Python constant, not an environment-variable name. `VOICE_LIVE_HONCHO_MAX_CHARS` limits the appended context size.

Dynamic context may contain user-specific or session-specific material. Treat it as sensitive runtime data: do not copy live context payloads, profile conclusions, identifiers, or private session content into public documentation, issues, logs, or examples.

## Documentation guidance

Public documentation should focus on observable behavior, supported configuration, validation steps, and known limitations. Do not include real credentials, account identifiers, private deployment data, live profile or session content, or capability claims that are not supported by the current code and tests.

When documenting a regression, describe the externally observable effect and link to the relevant issue or code location.
