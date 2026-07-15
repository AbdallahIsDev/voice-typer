# ADR 0001: Record Architecture Decisions

## Status

Accepted

## Context

We need to record the architectural decisions made on this project so that
current and future contributors understand why the codebase is structured the
way it is. Without explicit records, decisions are lost over time, leading to
repeated discussions and potential reverts of intentional choices.

Voice Typer is a cross-platform desktop application combining a Python backend
(speech recognition, model management, clipboard control) with an
Electron/React frontend. The architecture has evolved through multiple rounds
of forensic review and remediation, making it especially important to document
why certain patterns were chosen.

## Decision

We will use Architecture Decision Records (ADRs) as described by Michael Nygard
in this article: http://thinkrelevance.com/blog/2011/11/15/documenting-architecture-decisions

- Each ADR will be stored as a Markdown file in `docs/adr/`
- ADRs are numbered sequentially starting from 0001
- ADRs use the template in `docs/adr/template.md`
- Once an ADR is accepted, it is not updated; if a decision is reversed or
  modified, a new ADR is created that supersedes the previous one

## Consequences

- Positive: New contributors can understand historical context
- Positive: Reduces "why was it done this way?" questions
- Negative: Requires discipline to write ADRs for significant decisions
- Negative: ADRs add documentation maintenance overhead
