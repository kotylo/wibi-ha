# Repository Architecture

This repository contains a Home Assistant integration for reading and acknowledging WiBi app messages.

## Current structure

- `AGENTS.md` — development and documentation guidance for coding agents.
- `README.md` — project purpose and current development status.
- `.gitignore` — files excluded from version control.
- `.env.example` — anonymized deployment configuration template; the real ignored values live in `.env`.
- `copy-to-server.ps1` — validated, staged SSH deployment of the WiBi component with backup/rollback behavior.
- `custom_components/` — Home Assistant custom integrations; contains WiBi authentication, polling, notifications, entities, and actions.

Implementation folders will be documented here as they are introduced. Each new project folder must also contain its own concise `architecture.md`.
