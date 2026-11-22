# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- The spec is `.scratch/<feature-slug>/spec.md`.
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`.
- Triage state is a `Status:` line near the top of each issue file. See `triage-labels.md` for the role strings.
- Append comments and conversation history under a `## Comments` heading.

## Publishing and fetching

When a skill says "publish to the issue tracker", create a file under
`.scratch/<feature-slug>/`, creating directories as needed.

When a skill says "fetch the relevant ticket", read the referenced file.
The user normally supplies its path or issue number.

## Wayfinding operations

Used by `/wayfinder`:

- Map: `.scratch/<effort>/map.md`, containing Notes, Decisions-so-far, and Fog.
- Child ticket: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in its body.
- Type: a `Type:` line records `research`, `prototype`, `grilling`, or `task`.
- State: a `Status:` line records `claimed` or `resolved`.
- Blocking: a `Blocked by: NN, NN` line lists dependencies. A ticket is unblocked when all listed tickets are `resolved`.
- Frontier: scan for open, unblocked, unclaimed tickets; lowest number first.
- Claim: set `Status: claimed` and save before working.
- Resolve: append the answer under `## Answer`, set `Status: resolved`, and add a summary and link to Decisions-so-far in the map.
