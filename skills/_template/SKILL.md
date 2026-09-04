---
name: skill-name-in-kebab-case
description: >
  One line. When should Vajren reach for this? This line is ALWAYS in context,
  so keep it short and make it about the trigger, not the mechanism.
risk: auto | confirm            # inherits from config/policy.yaml if omitted
requires: []                    # tools or MCP servers this needs
author: mudit | vajren          # 'vajren' = self-written, needs the canary gate
created: YYYY-MM-DD
---

# Skill name

## When to use this

Concrete situations, in Mudit's own words where possible.

## Steps

1. …
2. …

## Post-conditions

How to know it actually worked — the thing `core/verify.py` should check.

## Failure modes

What goes wrong, and what to do instead.

---
<!--
SELF-WRITTEN SKILLS (author: vajren) must pass, in order:
  1. dry run in Windows Sandbox
  2. the promptfoo suite in tests/
  3. a spoken approval from Mudit
  4. a git commit
Only then may they be registered as callable. This ordering is not optional.
-->
