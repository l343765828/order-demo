# Domain Docs

This repo uses a single context: root `GLOSSARY.md` and `docs/adr/`.

## Before exploring

- Read root `GLOSSARY.md` when it exists.
- Read ADRs under `docs/adr/` that concern the area being explored.

When these documents are absent, proceed silently. The `domain-modeling`
skill creates them lazily as terms and decisions are resolved.

## Vocabulary

Use the glossary's terms when naming domain concepts in code, issues,
tests, and discussions.

If a concept is missing, reconsider the terminology or note the gap for
`domain-modeling`.

## Decisions

If a proposal contradicts an existing ADR, identify the ADR and explain
why the decision should be reopened.
