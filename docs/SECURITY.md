# Security

- **No orders**: MarketLens has no broker integration and no order code path.
- **Secrets**: read from environment/`.env` (never committed; `.gitignore`) or the OS keychain via the
  optional `keyring` package (Windows Credential Manager, service name `marketlens`). No keys in code
  (architecture test scans for key patterns). The Settings API returns only whether a key is configured.
- **Logs**: a `SecretRedactor` filter masks configured secret values, `sk-…`/`sk-ant-…` patterns and
  `token=`/`api_key=` query parameters; HTTP helpers never log URLs with keys.
- **Network exposure**: the backend binds to 127.0.0.1 by default; CORS allows only the Vite dev server
  and the Tauri origins. The Tauri webview has no shell permissions (`capabilities/default.json`).
- **Prompt injection**: external text (news, filings) is untrusted data. It is scanned for injection
  patterns, sanitised (control characters and envelope/role tags removed, length-limited) and wrapped in
  `<untrusted_external_data>`; only the News analyst receives it; system prompts instruct agents to
  ignore instructions inside it; outputs are schema-validated, numeric claims verified, and the committee
  can only downgrade. Tests: `tests/ai_safety/`.
- **Data licensing**: only licensed APIs; no scraping; rate limits respected.
- **Mock isolation**: MOCK and LIVE providers cannot be mixed; the mock LLM is refused in LIVE mode; the
  UI shows a MOCK DATA banner and mock names end with "(MOCK)".
- **Dependencies**: backend deps pinned by minimum version in `pyproject.toml`; frontend lockfile committed.
