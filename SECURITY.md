# Security Policy

## Supported versions

Nodify is pre-1.0 and has no long-term-support branches. Fixes land on `main` and in
the next release. There is nothing to backport to.

| Version | Supported |
| ------- | --------- |
| `main`  | yes       |
| 0.1.x   | yes       |
| < 0.1   | no        |

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Email **security@canedroid.dev** with:

- what the problem is, and what an attacker gains from it;
- the version or commit you tested;
- the steps to reproduce, ideally minimal;
- what you already tried.

You will get an acknowledgement within 72 hours and an assessment within seven
days. If the report is valid you will be credited in the fix unless you would
rather not be.

## What counts as a vulnerability here

Nodify is a local, single-user desktop application. It holds no accounts, no
credentials, no network listener, and no telemetry. The realistic threat model is
malicious content in a vault, not a remote attacker.

Worth reporting:

- code execution triggered by a vault file (a note, task, or filename) — for
  example a path-traversal or an unsafe deserialisation reachable from vault data;
- anything that leaves the machine, or reads outside the vault, without the user
  asking;
- a way to make the application run arbitrary commands via a crafted vault;
- a real flaw in the YAML parsing that lets a vault change types or inject keys.

Generally not vulnerabilities:

- the hotkey not firing when another application grabs the key first;
- a vault path pointing at a non-vault folder, which is refused with a dialog;
- anything requiring an attacker to already run code as you;
- the compositor declining to apply a blur, which falls back to a flat tint.

## Scope note

Nodify depends on PyQt6, ruamel.yaml, and watchdog. A vulnerability in one of those
is a vulnerability in that project. Report it upstream; tell us if it affects you and
we will help pin or work around it.
