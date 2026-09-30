# Contributing

Use Python 3.11 and install `requirements.lock.txt`. Run:

```bash
python -m pip check
python scripts/check_repository.py
python -m unittest discover -s tests -v
```

Use synthetic fixtures in issues, pull requests, screenshots, and tests. Never include customer ledgers, credentials, or private review notes.

Changes to financial calculations need a small numerical example with an expected result. Changes to data scope should cover the dashboard, Copilot, and exports. Changes to provider behavior should have mocked regression coverage; do not require live keys for the standard test suite.

Explain the business trigger, resulting behavior, and validation in a pull request. Update the data contract or architecture documentation when assumptions change. Keep examples reproducible and distinguish measured results from intended benefits.

`requirements.txt` lists pinned direct dependencies. `requirements.lock.txt` records the complete tested environment. Revalidate both after upgrading dependencies; do not treat a lock file as permanent security assurance.
