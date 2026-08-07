#!/usr/bin/env python3
"""Write one dotenv-style key from stdin without exposing the value in argv."""

import os
import re
import sys
import tempfile
from pathlib import Path

_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]*\Z")


def _validated_key(key):
    if not _KEY_RE.fullmatch(key):
        raise ValueError("invalid environment key")
    return key.encode("ascii")


def _validate_value(value):
    if not value:
        raise ValueError("empty value")
    if b"\x00" in value or b"\n" in value or b"\r" in value:
        raise ValueError("value must be a single non-empty line")


def _updated_content(existing, key_bytes, value):
    prefix = key_bytes + b"="
    lines = existing.splitlines(keepends=True)
    updated = []
    replaced = False

    for line in lines:
        if line.startswith(prefix):
            if replaced:
                continue
            if line.endswith(b"\r\n"):
                ending = b"\r\n"
            elif line.endswith(b"\n"):
                ending = b"\n"
            elif line.endswith(b"\r"):
                ending = b"\r"
            else:
                ending = b""
            updated.append(prefix + value + ending)
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        if updated and not updated[-1].endswith((b"\n", b"\r")):
            updated[-1] += b"\n"
        updated.append(prefix + value + b"\n")

    return b"".join(updated)


def write_env_value(path, key, value):
    """Atomically replace or append one key while preserving unrelated bytes."""
    key_bytes = _validated_key(key)
    _validate_value(value)

    path = Path(path)
    parent = path.parent
    if not parent.is_dir():
        raise OSError("environment directory does not exist")

    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        existing = b""

    updated = _updated_content(existing, key_bytes, value)
    fd, temp_name = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(parent))
    temp_path = Path(temp_name)

    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
        os.chmod(str(path), 0o600)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("ERROR: credential writer requires a file path and key", file=sys.stderr)
        return 2

    path, key = argv
    value = sys.stdin.buffer.read()
    try:
        write_env_value(path, key, value)
    except Exception:
        print("ERROR: unable to update environment file", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
