---
name: skill-creator
description: Design, create, review, and update reusable filesystem-based Agent Skills. Use when a user asks to create a skill, improve SKILL.md instructions, package scripts or references, or validate a skill bundle.
---

# Skill Creator

Create concise, reusable skills that another agent can discover and follow safely.

## Workflow

1. Identify concrete requests that should trigger the skill.
2. Choose a lowercase hyphenated name of at most 64 characters.
3. Write a focused description that says what the skill does and when to use it.
4. Keep core instructions in `SKILL.md`; place detailed knowledge in `references/`, deterministic utilities in `scripts/`, and output resources in `assets/`.
5. Add `agents/openai.yaml` for UI metadata when useful.
6. Review every bundled file and remove placeholders or unrelated documentation.
7. Validate the YAML frontmatter, file links, and example workflow before publishing.

## Authoring Rules

- Include only `name` and `description` in SKILL.md frontmatter.
- Prefer imperative instructions and concise examples.
- Keep references one level away from SKILL.md.
- Never include secrets, credentials, or unreviewed executable code.
- Treat third-party skills like software: audit all instructions and resources before enabling them.
