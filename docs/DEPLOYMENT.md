# Running and deploying the application

The tested release supports a supervised local workflow. A public demonstration should contain synthetic data. Confidential company use requires an approved access-controlled environment and the operational work below.

## Local environment

Use Python 3.11 and the complete environment snapshot:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip check
python scripts/check_repository.py
python -m unittest discover -s tests -v
streamlit run app.py --server.address 127.0.0.1
```

`requirements.txt` lists pinned direct dependencies; `requirements.lock.txt` captures the full tested environment. Python 3.11 is the supported baseline. The environment snapshot has no integrity hashes and is not a software-supply-chain certification. Review updates deliberately and rerun the suite after changing dependencies.

## Persistent planning storage

Plans use `data/planning.sqlite3` by default. Set `FPA_DATABASE_PATH` in the server environment to choose a protected persistent path. The parent directory must be writable by the app process. SQLite transactions and revision checks reject stale writes; they do not supply user authorization or a multi-tenant database.

All sessions share this store. Local review names are labels, not sign-in identities. Keep private plans behind appropriate access controls. Do not use an unprotected public demonstration as a business planning service.

Use a persistent volume on container/hosted deployments. For Docker, a named volume can retain the default data directory:

```bash
docker run --rm -p 127.0.0.1:8501:8501 -v finance-planning-data:/app/data ai-finance-agent
```

Download individual plan JSON backups for portable recovery. For complete revision history, take consistent SQLite database backups using its backup API or after stopping the app; test restoration separately. The JSON plan backup preserves the selected payload and review-event metadata, not every earlier revision's full payload. Keep both kinds of backup outside public source control.

## Optional language-model provider

Copy `.env.example` to `.env`, populate credentials privately, and select the provider in Assistant settings or Commentary provider. Local mode is the UI default even if credentials exist. Top-level environment variables can also be supplied by the host's secret manager. Do not place keys in source files or screenshots.

| Variable | Purpose |
| --- | --- |
| `LLM_PROVIDER` | Default adapter for programmatic calls: `openai` or `anthropic` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Credential and model for the OpenAI adapter |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Credential and model for the Anthropic adapter |
| `FVA_TIMEOUT_SECONDS` | Provider-call timeout, default 45 seconds, clamped to 1–60 seconds |
| `LANGSMITH_TRACING` | Example configuration disables external tracing |

Provider calls have no automatic retries. Copilot allows at most four tool-calling rounds, eight tool calls, and one final synthesis call. A cooperative 120-second budget prevents starting additional work after the deadline. An already running call can finish after that deadline; this is not a hard request-cancellation system. See [Copilot reliability](COPILOT.md).

Questions, recent conversation history, and requested data-tool results may be sent to the selected provider. The briefing workflow sends calculated analysis to the provider. A public FX query contacts a public exchange-rate endpoint independently of language-model mode. Confirm the provider's current model availability and your organization's data policy before enabling it.

Live-provider validation must use synthetic data first. Check tool schemas, exact entity/period scope, latency, fallback rates, token cost, numerical attribution, and prompt-injection attempts. Offline mocks establish application behavior, not a live model's quality or service availability.

## Container option

The included Dockerfile runs as a non-root user and exposes the Streamlit health endpoint. Build and validate it on a machine with Docker:

```bash
docker build -t ai-finance-agent .
docker run --rm -p 127.0.0.1:8501:8501 ai-finance-agent
```

For a private local provider test, pass a private environment file with `--env-file .env`. Do not bake secrets into the image. The container configuration is provided but was not built in the recorded validation environment because Docker was unavailable.

## Public demonstration hosting

Publish the reviewed source repository first. For a Streamlit host, choose `app.py` as the entry point and Python 3.11. Hosts that auto-install `requirements.txt` resolve transitive packages themselves; to match the tested snapshot, configure an install step using `requirements.lock.txt`, or use the provided container with a host that supports it.

The app has no built-in authentication. Keep public demonstrations synthetic, leave paid providers disabled unless the host has an access and spending policy, and do not treat an unlisted URL as access control. Read the official [Streamlit dependency guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies) for platform-specific installation behavior.

## Business deployment acceptance

| Area | Current implementation | Work required for shared business use |
| --- | --- | --- |
| Identity | No application login | SSO/authenticated gateway, session access policy, role checks |
| Persistence | SQLite planning versions/snapshots; session-based actuals notes; JSON exports | Approved shared storage, encryption/retention policy, complete workspace recovery tests |
| Auditability | Planning revision snapshots/local review labels; report context and ledger fingerprint | Authenticated authorship, tamper-resistant history, approval separation |
| Data governance | Local default; external provider is opt-in | Approved residency, retention, deletion, and provider arrangements |
| Abuse and spending | Bounded questions, history, tools, calls | Per-user quotas, rate limits, concurrency limits, budget alerts |
| Observability | Response mode and fallback notes | Central redacted telemetry, error monitoring, availability alerts |
| Capacity | Upload and context limits | Representative concurrency, memory, export, and latency tests |
| AI quality | Schemas, numeric checks, offline failure tests | Versioned live evaluations, human acceptance criteria, drift review |

The application performs FP&A planning and expense analysis; it has no ERP writeback or payment/journal execution. Deployments should preserve this boundary unless separate authorized workflows are designed and tested.

## Troubleshooting

- **Provider selected but local answer returned:** inspect Response notes; verify credentials/model access privately. Missing keys, invalid tool output, scope changes, and timeouts trigger fallback.
- **No matching rows:** confirm the year, cutoff, department, account, quarter, direction, and Answer using control. An empty intersection is not a zero spend result.
- **Upload rejected:** consult the [data contract](DATA_CONTRACT.md). Aggregate transactions to monthly department/account balances before uploading.
- **Review does not restore:** load the same keys, budget/actual amounts, and currency. A changed source must start a new review.
- **Actuals notes disappeared after session ended:** restore their downloaded JSON; actuals review notes remain session state.
- **Planning versions disappeared:** verify the same `FPA_DATABASE_PATH` and persistent volume are in use. An ephemeral server filesystem is not durable hosting.
- **Planning save rejected as stale:** reload the latest revision and reapply the intended change; another session saved first.
- **Cash report asks for opening balances:** the actuals cutoff advanced; confirm balances dated to the new close in Cash planning.
- **Expired chat download:** the UI retains the latest five Excel results and 40 messages. Regenerate the extract or use a saved working paper.

If Git is unavailable or not configured, the repository checker reports that failure. `python scripts/check_repository.py --source-only` can separately validate source/documentation; it does **not** verify tracked contents. Complete the normal check once Git works before publishing a commit.
