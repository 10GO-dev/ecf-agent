# ECF Agent

Python service for ECF processing and integration workflows.

## Rules

- Follow the package structure and configuration conventions already present in `src`, `config`, and `tests`.
- Keep configuration environment-driven and do not commit secrets or machine-specific paths.
- Preserve database, XML, ECF, and emitted-file contracts unless the task explicitly changes them.
- Validate external input at boundaries and use the project's existing logging and error-handling patterns.
- Add or update focused tests for behavior changes.

## Validation

Use the configured project environment and run the narrowest applicable pytest, type-check, or build command. Check `pyproject.toml` and `README.md` for project-specific commands before running broad validation.
