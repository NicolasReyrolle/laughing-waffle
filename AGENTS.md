# Agent Guidelines & Project Rules

## Module Size & Architecture

- **Maximum Module Size**: Keep every source code module strictly under 1,000 lines of code.
- **Refactoring Strategy**: Whenever a file approaches or exceeds 1,000 lines, refactor it into cohesive, single-responsibility modules within a package (e.g. `airspace/`).
- **Backward Compatibility**: Ensure top-level entrypoints and existing module imports remain fully backwards-compatible (via re-exports in `__all__`).

## Documentation & Code Style

- **Docstrings**: Maintain comprehensive Google-style docstrings on all functions and methods (including summary, `Args`, `Returns`, and `Raises` when applicable).
- **Type Checking**: Maintain full typing annotations passing `mypy`.
- **Testing**: Ensure all unit tests in `tests/` pass with `pytest`.
