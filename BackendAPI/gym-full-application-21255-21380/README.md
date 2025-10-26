# Gym Full Application - BackendAPI Container

This workspace includes the BackendAPI FastAPI service.

Linting
- CI may call .init/.linter.sh
- The script is resilient: if flake8 is not installed, it prints a message and exits 0 to avoid blocking builds.
- To enforce linting locally or in CI, install dependencies first:
  pip install -r BackendAPI/requirements.txt
