# Conversational behavior configuration

The runtime source of truth for conversational behavior is `bridge_config.py:BASE_SYSTEM_PROMPT`. The compatibility facade in `bridge.py` re-exports that value for existing imports.

This public document intentionally does **not** reproduce the prompt text, its section ordering, identity strings, internal operator phrasing, tool-routing instructions, or behavioral enforcement grammar. Those details are implementation configuration, not a public integration contract.

## Public behavior contract

At a high level, the bridge is intended to:

- wait for the user to speak before producing first-turn audio;
- answer directly and keep spoken responses structured;
- use available tools only through the runtime's configured tool boundary;
- describe video or screen content only when an authenticated frame was actually received for the current interaction;
- keep vocal-expression markup bounded so it does not dominate speech;
- preserve the configured conversational style without treating personality text as authorization to bypass security, privacy, confirmation, or tool-permission controls.

Prompt wording alone does not prove that a capability works. In particular, the bundled frame paths remain blocked on current `main`; see [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9).

## Editing the configuration

Edit `BASE_SYSTEM_PROMPT` in `bridge_config.py`. Do not edit only the `bridge.py` facade; it contains compatibility imports, not the prompt definition.

After a reviewed change:

1. Compile-check both the source module and facade: `python -m py_compile bridge_config.py bridge.py`.
2. Restart the gateway: `systemctl --user restart hermes-gateway`.
3. Test the affected behavior in a controlled voice session.
4. Re-check that security, privacy, identity, confirmation, and tool-permission boundaries still come from executable policy rather than prompt wording.

For video behavior, do not describe the bundled `/frame` clients as operational until the startup and authentication work in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9) is fixed and tested.

## Dynamic context

The static prompt can be supplemented with per-session Honcho context. The feature is controlled by `VOICE_LIVE_HONCHO_CONTEXT`; `HONCHO_CONTEXT_ENABLED` is the corresponding Python constant, not an environment-variable name. `VOICE_LIVE_HONCHO_MAX_CHARS` limits the appended context size.

Dynamic context may contain user-specific or session-specific material. Treat it as sensitive runtime data: do not copy live context payloads, profile conclusions, identifiers, or private session content into public documentation, issues, logs, or examples.

## Public documentation boundary

Public docs may explain observable behavior, supported configuration names, validation steps, and known limitations. They should not publish:

- verbatim system prompts or internal prompt fragments;
- detailed control-language maps or completion contracts;
- privileged routing, delegation, trust, or mutation instructions;
- embedded user identities or deployment-specific profile data;
- secret-handling, permission-bypass, or tool-authorization grammar.

When documentation needs to describe a regression, state the externally observable behavior and link to the relevant issue or code location without exposing the internal control text used by the model.