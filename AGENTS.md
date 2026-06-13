# Repository Guidelines

## Project Structure & Module Organization

This is a Python Anki add-on packaged from the repository root. Core modules live at the top level: `__init__.py` registers Anki UI hooks, `processor.py` coordinates note processing, `llm_client.py` and `http_client.py` handle model calls, and `config.py`, `config_dialog.py`, and `models.py` define configuration and data structures. Search providers live in `search/`. Unit tests live in `tests/`. `manifest.json` and `config.json` are packaged metadata/defaults; `dist/` is generated output.

## Build, Test, and Development Commands

Use the repository’s existing `uv run python` workflow:

```bash
uv run python -m unittest discover -s tests
```

Runs the full unit test suite without requiring Anki.

```bash
uv run python -m unittest tests.test_config
```

Runs one test module while iterating.

```bash
uv run python package_addon.py
```

Builds `dist/llm_remark_generator.ankiaddon` using the file list in `package_addon.py`.

There is no local development server. Check UI behavior in Anki after installing the package or source add-on directory.

## Coding Style & Naming Conventions

Target Python 3.12, matching CI. Use 4-space indentation, type hints, and `from __future__ import annotations` for new Python modules. Keep imports grouped as standard library, third-party/Anki, then local imports. Follow existing naming: modules and functions use `snake_case`, classes use `PascalCase`, constants use `UPPER_SNAKE_CASE`.

Keep Anki-specific imports inside runtime registration or UI functions so tests run without Anki installed. Prefer explicit exceptions such as `ConfigError`.

## Testing Guidelines

Tests use standard library `unittest`. Name files `tests/test_*.py`, classes `*Test`, and methods `test_*`. Keep network and LLM behavior mocked with fake request functions; do not call real APIs in tests. Add focused tests for config validation, request formatting, processing behavior, and packaging changes.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects, sometimes with a `feat:` prefix, for example `feat: fetch LLM models in config UI` and `Fix release workflow and add batch stop control`. Keep commits scoped to one change.

Pull requests should include a concise summary, verification commands, related issue links when applicable, and screenshots or notes for Anki UI changes. Mention config, packaging, or release behavior changes explicitly.

## Security & Configuration Tips

Never commit real API keys in `config.json`, tests, logs, or examples. Treat `llm.api_key`, `search.brave_api_key`, and `search.tavily_api_key` as secrets. If adding packaged files, update `package_addon.py` and verify archive contents with the packaging test.
