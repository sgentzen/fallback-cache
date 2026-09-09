# Contributing to fallback-cache

Thanks for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/sgentzen/fallback-cache.git
cd fallback-cache
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev,redis]"
```

## Running Tests

```bash
pytest --cov=fallback_cache --cov-report=term-missing -v
```

## Linting and Type Checking

```bash
ruff check src/ tests/
mypy src/fallback_cache/
```

## Pull Request Guidelines

1. Fork the repo and create a feature branch from `main`.
2. Add tests for any new functionality.
3. Ensure all tests pass and coverage stays above 90%.
4. Run `ruff check` and `mypy` before submitting.
5. Keep commits focused — one logical change per commit.
6. Write a clear PR description explaining **what** and **why**.

## Reporting Bugs

Open an issue with:

- Python version
- Redis version (if applicable)
- Minimal reproduction steps
- Expected vs. actual behavior
