# Data handling and security

This application supports local financial planning, supervised analysis, and synthetic demonstrations. Planning versions and review snapshots persist in SQLite. It has no built-in authentication, tenant/department authorization, independent-approver enforcement, or tamper-proof audit log. All sessions on one deployment can access its saved planning workspace.

- Keep employer/customer financial records out of this repository and public demos.
- `.env`, Streamlit secrets, uploads, working papers, and generated exports are excluded by the supplied ignore rules. Review the staged Git diff before publishing; ignore rules do not remove files that were already committed.
- Local data processing does not require a provider. Choosing an external model sends relevant prompts, history, and tool results to that provider. Configure tracing only when the data and destination are approved.
- The FX feature contacts a public rate endpoint even in local mode. Rates are informational observations, not an accounting translation policy.
- Actuals review notes remain session state; download their JSON backup to retain them. Planning versions persist in `data/planning.sqlite3` or the configured `FPA_DATABASE_PATH`. Protect this file, its parent folder, database backups, workbooks, and JSON exports like the source financial data. The database file is created with owner-only permissions; this is not encryption or application authorization.
- Planning approvals are local workflow labels. Review names and imported history do not authenticate anyone. JSON restoration always creates a new draft. Do not expose a deployment containing private plans on a public URL.
- Bind a local deployment to loopback. Deployments accessible over a network need an approved identity, TLS, access, retention, logging, and monitoring design.

If you find a security issue, use the repository's private vulnerability reporting channel if it is enabled, or contact the maintainer privately. Do not put financial records or credentials in a public issue. This project currently makes no compliance certification or vulnerability-free claim.

Planning Copilot sends the current question to the selected optional provider only when local report routing cannot classify it. It does not send stored planning tables or conversation history; the user's question may itself contain sensitive information. The provider returns a validated report identifier only. It cannot execute code, change scope, supply financial results, write a version, or approve it. Planning history/downloads reset on saved-plan revision changes and are capped at 40 messages/five workbooks.
