"""Architecture boundary tests (dependency direction is enforced, not just documented)."""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "marketlens"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def files(sub: str) -> list[Path]:
    return sorted((PKG / sub).rglob("*.py"))


def violations(sub: str, forbidden: tuple[str, ...]) -> list[str]:
    bad = []
    for f in files(sub):
        for imp in imports(f):
            if imp.startswith(forbidden):
                bad.append(f"{f.relative_to(PKG)} -> {imp}")
    return bad


def test_domain_is_pure():
    assert violations("domain", ("marketlens.providers", "marketlens.infrastructure", "marketlens.api", "marketlens.application",
                                 "marketlens.workers", "marketlens.config", "fastapi", "sqlalchemy", "httpx", "requests", "anthropic", "pydantic")) == []


def test_domain_cannot_import_frontend():
    for f in files("domain"):
        assert "frontend" not in "".join(imports(f)), f


def test_decision_engine_has_no_network_or_llm():
    for mod in ("decision.py", "scoring.py", "entry.py"):
        imps = imports(PKG / "domain" / mod)
        assert not any(i.startswith(("httpx", "socket", "urllib", "requests", "marketlens.providers", "anthropic", "openai")) for i in imps), mod


def test_scoring_cannot_import_decision_or_llm():
    imps = imports(PKG / "domain" / "scoring.py")
    assert "marketlens.domain.decision" not in imps
    assert not any("llm" in i or "committee" in i for i in imps)


def test_pipeline_is_pure():
    imps = imports(PKG / "application" / "pipeline.py")
    assert not any(i.startswith(("marketlens.providers", "marketlens.infrastructure", "httpx", "sqlalchemy")) for i in imps)


def test_committee_cannot_touch_database_or_providers_data():
    assert violations("application/committee", ("marketlens.infrastructure.db", "sqlalchemy", "marketlens.providers.live", "marketlens.providers.mock", "marketlens.application.data_access")) == []


def test_providers_do_not_import_application_or_api():
    assert violations("providers", ("marketlens.application", "marketlens.api", "marketlens.infrastructure.db")) == []


def test_api_has_no_domain_calculations():
    imps = imports(PKG / "api" / "routes.py")
    assert not any(i in imps for i in ("marketlens.domain.scoring", "marketlens.domain.decision", "marketlens.domain.entry"))


def test_no_bare_except_or_silent_pass():
    for f in PKG.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ExceptHandler):
                assert n.type is not None, f"bare except in {f}"
                assert not (len(n.body) == 1 and isinstance(n.body[0], ast.Pass)), f"silent except/pass in {f}:{n.lineno}"


def test_no_hardcoded_api_keys():
    import re

    pat = re.compile(r"(sk-ant-[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{20,}|api_key\s*=\s*['\"][A-Za-z0-9]{16,}['\"])")
    for f in PKG.rglob("*.py"):
        assert not pat.search(f.read_text(encoding="utf-8")), f


def test_infrastructure_does_not_depend_on_application_or_api():
    assert violations("infrastructure", ("marketlens.application", "marketlens.api", "marketlens.workers")) == []
