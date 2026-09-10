# Agent Guidelines

## Architecture maps

- Every project folder must contain an `architecture.md` file with a short, high-level description of that folder's purpose and structure.
- Before reading source files in a folder, read its `architecture.md` first to locate the relevant files with minimal context usage.
- Keep each `architecture.md` concise. List the important files and subfolders, their responsibilities, and any non-obvious relationships.
- Update the relevant `architecture.md` whenever files, responsibilities, or folder structure change.
- When creating a new folder, create its `architecture.md` at the same time.
- Do not add `architecture.md` files to generated, dependency, cache, build-output, or version-control directories.

## Code organization

- Prefer small, focused files with one clear responsibility.
- Keep code DRY, but introduce abstractions only when they make ownership and behavior clearer.
- Follow established architectural patterns and keep dependencies flowing through clear boundaries.
- Place shared behavior in an appropriate common module rather than duplicating it.
- Keep public interfaces small and implementation details private where practical.
- Update documentation and tests alongside behavior changes.

