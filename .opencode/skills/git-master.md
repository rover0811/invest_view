# git-master (project override)

Use the normal git-master workflow for this repository with one critical override:

## Non-negotiable trailer policy

- NEVER add any commit footer or trailer automatically.
- NEVER add `Co-authored-by:` lines.
- NEVER add `Ultraworked with ...` lines.
- NEVER append any agent attribution text to commit bodies.

## Commit message policy

- Follow the repository's existing commit style detected from `git log`.
- Keep commit messages clean, human-authored, and repository-native.
- Use plain commit messages unless the repo clearly uses a different style.

## Git command policy

- Keep using the `GIT_MASTER=1` prefix for git commands if the underlying workflow expects it.
- Preserve the rest of git-master behavior: atomic commits, safe push behavior, branch awareness, and history hygiene.

## Repository-specific preference

- In this repository, clean commit history is preferred over agent attribution.
- If there is any conflict between builtin git-master behavior and this file, this file wins.
