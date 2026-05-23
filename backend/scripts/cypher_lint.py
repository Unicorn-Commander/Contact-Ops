"""Refuse f-string Cypher and unsupported FalkorDB subquery syntax."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

CYPHER_RE = re.compile(r"(?i)\b(MATCH|MERGE|CREATE|RETURN|GRAPH\.QUERY)\b")
DISALLOWED_RE = re.compile(r"(?i)(CALL\s*\{|EXISTS\s*\()")
BASELINE_ALLOWED = {
    Path("contact_ops/agents/cli.py"),
    Path("contact_ops/agents/dedup/merge_executor.py"),
    Path("contact_ops/agents/dedup/unmerge_executor.py"),
    Path("contact_ops/agents/voice_match.py"),
    Path("contact_ops/core/redis.py"),
    Path("contact_ops/services/inbox_mutations.py"),
}


def scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            literal = _joined_literal(node)
            if CYPHER_RE.search(literal):
                errors.append("f-string Cypher is forbidden")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if CYPHER_RE.search(node.value) and DISALLOWED_RE.search(node.value):
                errors.append("FalkorDB CALL{} / EXISTS() subqueries are forbidden")
    return errors


def _joined_literal(node: ast.JoinedStr) -> str:
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("{}")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=["contact_ops"])
    args = parser.parse_args(argv)

    failures: list[str] = []
    for root in args.paths:
        path = Path(root)
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for file_path in files:
            if "__pycache__" in file_path.parts:
                continue
            if file_path in BASELINE_ALLOWED:
                continue
            for error in scan_file(file_path):
                failures.append(f"{file_path}: {error}")

    if failures:
        sys.stderr.write("\n".join(failures) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
