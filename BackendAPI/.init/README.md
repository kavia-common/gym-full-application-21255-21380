This folder contains CI helper scripts.

- .linter.sh: Resilient linter entrypoint. It attempts to activate a local venv if present and runs flake8 if available. If flake8 is not installed, it skips linting and exits successfully to avoid blocking CI for scaffolding tasks.
