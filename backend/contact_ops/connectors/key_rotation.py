"""Secret-safe entry point for connector-payload key rotation.

Keep this module limited to the standard library. Python imports the parent
package before executing a ``-m`` target, and any eager application import can
raise before an ordinary ``main()`` exception handler exists. The connector
package is correspondingly lazy; this wrapper catches implementation, dependency,
and Settings startup failures without rendering exception values.
"""

from __future__ import annotations

import sys

_GENERIC_FAILURE = "ERROR: connector key operation failed; transaction rolled back\n"


def main(argv: list[str] | None = None) -> int:
    try:
        from contact_ops.connectors.key_rotation_impl import main as implementation_main

        return implementation_main(argv)
    except Exception:  # noqa: BLE001 - never render potentially secret-bearing startup errors.
        sys.stderr.write(_GENERIC_FAILURE)
        return 2


if __name__ == "__main__":
    sys.exit(main())
