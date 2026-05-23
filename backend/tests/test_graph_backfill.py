from __future__ import annotations

from scripts.cypher_lint import scan_file


def test_cypher_lint_catches_f_string(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "bad.py"
    path.write_text('name = "x"\nquery = f"MATCH (p:Person {name}) RETURN p"\n', encoding="utf-8")
    assert scan_file(path) == ["f-string Cypher is forbidden"]


def test_cypher_lint_catches_unsupported_subquery(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "bad.py"
    path.write_text('query = "MATCH (p) WHERE EXISTS((p)--()) RETURN p"\n', encoding="utf-8")
    assert scan_file(path) == ["FalkorDB CALL{} / EXISTS() subqueries are forbidden"]
