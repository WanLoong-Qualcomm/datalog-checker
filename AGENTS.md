# AGENTS.md

## Python engineering practices

- Keep changes small, focused, and consistent with the existing project structure.
- Prefer clear, idiomatic Python with meaningful names, type hints where useful, and short functions with single responsibilities.
- Validate inputs at boundaries and handle errors explicitly; do not silently discard failures.
- Update or add tests for behavior changes. Run the relevant test suite, linting, formatting, and type checks when available.
- Preserve backwards compatibility unless a breaking change is explicitly requested. Update documentation and examples when behavior or interfaces change.
- Do not commit generated artifacts, secrets, local environment files, or unrelated formatting changes.

## Configuration and UI parity

- Every functional change must remain consistent in both configuration paths: the GUI configurator and the JSON-based configurator.
- Keep validation, defaults, field names, supported values, and resulting behavior synchronized between both paths.
- The JSON configurator uses `JUI.json` as its JSON user interface; it is an alternative interface to the GUI, not a GUI runtime dependency. Keep its validation and behavior consistent with the GUI.
- Coverage TESTS-to-TNAME mappings live in `config.json`; the GUI and JUI-driven CLI must use the same mapping.
- When changing configuration behavior, test both GUI-driven and JSON-driven flows.
- After any GUI change, recompile the application and verify the resulting build.
