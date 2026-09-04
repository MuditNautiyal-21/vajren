# Security model

Vajren deliberately combines the three things that make agents dangerous: access
to private data, exposure to untrusted content, and the ability to communicate
outward. This file states how that is contained. If you fork this, read it before
pointing the agent at a real inbox.

## The threat that matters

Indirect prompt injection: instructions hidden inside content the agent *reads* —
an email body, a web page, a calendar invite, a filename — being treated as
instructions from the user.

This is not hypothetical. The first confirmed weaponised indirect injection in the
wild was documented in December 2025, using zero-size text, off-screen text, CSS
suppression and base64 payloads, in campaigns for phishing, database destruction
and forced transactions. Over 40 CVEs have been filed against MCP-based systems.

## Controls, in order of how much they actually buy

**1 · Human confirmation on consequential actions.** Reads, searches and drafts run
freely. Sending, deleting, spending, posting and executing state their plan aloud
and wait for an explicit confirmation phrase. Silence times out to **cancel**, never
to proceed. This single control breaks the chain between reading untrusted content
and acting on it.

**2 · Quarantine.** Untrusted content is passed to a model call that has no tools
and no authority, which extracts it into a rigid schema. Only that schema reaches
the planner. Raw email bodies and page text never enter the context that decides on
actions. This is the buildable half of the dual-LLM pattern.

**3 · Default-deny tool classification.** Tools are `auto`, `confirm`, or
`forbidden`. An unrecognised tool is **not** auto — it falls through to `confirm`.

**4 · Rules in code, not prompts.** The policy gate, the private/public data split
and the path denylist are enforced in `core/policy.py`. `config/policy.yaml` is
never writable by the agent, and no tool is provided that could write it.

**5 · Data lane separation.** Anything touching email, files, calendar or
credentials is pinned to local inference. Free cloud tiers — several of which train
on submitted data — only ever see non-personal content, and the classification is a
code path, not an instruction.

**6 · Least privilege.** A dedicated Google account rather than the primary one.
Gmail scopes default to `readonly`; `send` is requested only at the moment of an
approved send. The agent runs as a restricted, non-administrator Windows account.

**7 · Reversibility by construction.** Trash rather than delete. Draft rather than
send. Git commit before and after every file edit. Every mutating tool returns an
undo reference.

**8 · Append-only audit.** Every tool call, its arguments, its result, the approval
tier and the phrase actually heard are written to a log the agent's own account
cannot delete.

**9 · Supply chain.** MCP servers are version-pinned, never `latest` — rug-pull
updates are a named attack class. Config snippets are read before being run.

## Never permitted, with or without confirmation

Permanent deletion · payments · modifying its own permissions, OAuth scopes or MCP
server list · disabling the audit log or kill switch · adding a remote device to
its own access · treating instructions found inside content as user instructions.

## Kill switch

Three independent paths, because one will eventually fail: a global hotkey, an
authenticated `/stop` over Telegram, and stopping the Windows service directly.
Stop means *refuse new actions immediately, then abort in-flight ones cleanly* —
never leave a half-sent message.

## Reporting

This is a personal project, not a product, and carries no security guarantees.
If you find something interesting in it, open an issue.
