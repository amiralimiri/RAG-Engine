# Ticket 01: Dependencies and Configuration

## Status
Pending

## Dependencies
None

## Scope
Add the `langfuse` dependency to the project package configuration, and extend application settings to support Langfuse environment variables with sensible defaults.

## Deliverables
1. `pyproject.toml`: Add `"langfuse>=2.0.0"` to `project.dependencies`.
2. `app/core/config.py`: Add the following fields to `Settings`:
   - `langfuse_public_key: str | None = None`
   - `langfuse_secret_key: SecretStr | None = None`
   - `langfuse_host: str = "https://cloud.langfuse.com"`
   - `langfuse_enabled: bool = True`
3. `tests/test_telemetry_config.py`: Unit tests validating settings parsing and defaults.

## Acceptance Criteria
- [ ] `langfuse` is listed in `pyproject.toml` dependencies and installs via `uv sync`.
- [ ] `Settings` successfully loads `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, and `LANGFUSE_ENABLED` from environment variables / `.env`.
- [ ] `langfuse_host` defaults to `"https://cloud.langfuse.com"`.
- [ ] `langfuse_enabled` defaults to `True`.
- [ ] When keys are not supplied, `langfuse_public_key` and `langfuse_secret_key` default to `None` without validation errors.

## TDD Test Requirements
Create `tests/test_telemetry_config.py`:
- `test_default_langfuse_settings()`: Initialize `Settings()` with empty environment; assert `langfuse_enabled is True`, `langfuse_host == "https://cloud.langfuse.com"`, and keys are `None`.
- `test_custom_langfuse_settings(monkeypatch)`: Set environment variables `LANGFUSE_PUBLIC_KEY="pk-123"`, `LANGFUSE_SECRET_KEY="sk-456"`, `LANGFUSE_HOST="https://my-langfuse.example.com"`, `LANGFUSE_ENABLED="false"`; verify `Settings()` properly parses them.
