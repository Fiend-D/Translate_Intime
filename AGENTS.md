# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/`. Keep orchestration and audio lifecycle logic in `src/core/`, engine adapters in `src/engines/`, platform audio integrations in `src/audio/`, Qt widgets in `src/gui/`, configuration/data objects in `src/models/`, and shared helpers in `src/utils/`. Generated Volcengine protobuf modules are under `python_protogen/`; do not edit them manually. Tests are split into `tests/unit/` and `tests/integration/`. Runtime defaults, icons, terminology lists, and optional model placeholders live in `config/`, `assets/`, `hotwords/`, and `resource/`. Design documents and contracts are in `specs/` and `docs/`.

## Build, Test, and Development Commands

- `python3.12 -m venv .venv && source .venv/bin/activate`: create the development environment.
- `pip install -r requirements.txt -r requirements-dev.txt`: install runtime and development dependencies.
- `python run.py` or `python -m src.main`: launch the desktop application.
- `pytest`: run all unit and integration tests.
- `QT_QPA_PLATFORM=offscreen pytest`: run Qt tests on headless Linux/CI.
- `ruff check src tests` and `ruff format --check src tests`: lint and verify formatting.
- `mypy src`: run strict type checking.
- `python build.py --clean`: build the Windows PyInstaller executable; review generated spec changes before committing.

## Coding Style & Naming Conventions

Use Python 3.12, four-space indentation, and a 100-character preferred line length. Ruff configuration in `pyproject.toml` is authoritative. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_CASE` for constants. Add type annotations to public APIs and keep engine implementations behind the `TranslationEngine` protocol. Avoid blocking work on the Qt main thread. Ensure worker threads, audio streams, and asyncio tasks have explicit stop/close paths.

## Testing Guidelines

Pytest, pytest-qt, and pytest-asyncio are the test stack. Name files `test_<feature>.py` and tests `test_<behavior>()`. Unit tests should mock network, model downloads, audio hardware, and keyring access. Add integration tests for changes spanning capture, engines, and subtitle UI. No fixed coverage threshold is configured; new behavior and regressions should receive focused tests.

## Commit & Pull Request Guidelines

History follows short Conventional Commit-style subjects, for example `feat(audio): improve virtual-device routing` or `fix: handle missing keyring backend`. Keep commits focused. Pull requests should describe user-visible impact, affected platforms/backends, verification commands, and linked issues. Include screenshots for Qt UI changes and logs or reproduction steps for audio/device fixes.

## Security & Configuration Tips

Never commit API keys, local transcripts, downloaded models, or device-specific configuration. Preserve `.gitignore` protections for `.env`, logs, media, caches, and model files. Treat `config/default_config.yaml` as non-secret defaults only.
