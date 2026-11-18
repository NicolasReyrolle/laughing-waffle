# Agent Guidelines & Project Rules

## Project Files & Rules

- **Rules File**: Only `AGENTS.md` is used for agent guidelines. Do not create or generate `GEMINI.md`.

## Module Size & Architecture

- **Maximum Module Size**: Keep every source code module strictly under 1,000 lines of code.
- **Refactoring Strategy**: Whenever a file approaches or exceeds 1,000 lines, refactor it into cohesive, single-responsibility modules within a package (e.g. `airspace/`).
- **Backward Compatibility**: Ensure top-level entrypoints and existing module imports remain fully backwards-compatible (via re-exports in `__all__`).

## Complexity & Function Design

- **Cognitive Complexity**: Keep the cognitive complexity of each function and method strictly under 15 (ideally <= 10).
- **Decomposition**: Decompose multi-step, deeply nested, or multi-concern routines into focused, single-responsibility helper functions.

## Documentation & Code Style

- **Docstrings**: Maintain comprehensive Google-style docstrings on all functions and methods (including summary, `Args`, `Returns`, and `Raises` when applicable).
- **Type Checking (mypy & Pylance / Pyright)**:
  - Zero Pylance / Pyright errors must be present.
  - Maintain explicit re-exports in `__all__` in every module to prevent `reportPrivateImportUsage`.
  - Maintain project configuration in `pyproject.toml` and `.vscode/settings.json` referencing `.venv` and workspace root in `extraPaths`.
  - Maintain full typing annotations passing `mypy`.
  - Format source code using `ruff`.
- **Testing**: Ensure all unit tests in `tests/` pass with `pytest`.
