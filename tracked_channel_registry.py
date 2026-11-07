"""Fail-closed, atomic access to the established tracked-channel registry.

An existing production registry is required. A missing, malformed or empty
registry is never treated as a new setup, because that could erase established
channels or silently skip their uploads. Initial bootstrap is a separate,
explicit operator action, not something scheduled workflows may perform.
"""
import json
import os
import tempfile
from pathlib import Path


class RegistryIntegrityError(RuntimeError):
    """An established registry is missing or structurally unsafe."""


def validate_registry(data):
    """Validate structure without rejecting historical metadata conventions.

    Older tracked-channel values may be strings instead of objects; preserve
    them until discovery upgrades them. Unknown per-channel fields and legacy
    timestamps are intentionally retained, not normalized or discarded.
    """
    if not isinstance(data, dict):
        raise RegistryIntegrityError("Invalid tracked-channel registry: expected JSON object")
    channels = data.get("channels")
    if not isinstance(channels, dict) or not channels:
        raise RegistryIntegrityError(
            "Invalid tracked-channel registry: nonempty channels object required"
        )
    for cid, record in channels.items():
        if not isinstance(cid, str) or not cid.strip():
            raise RegistryIntegrityError("Invalid tracked-channel registry: empty channel ID")
        if isinstance(record, str):
            if not record.strip():
                raise RegistryIntegrityError(
                    "Invalid tracked-channel registry: empty legacy channel name"
                )
        elif isinstance(record, dict):
            if not isinstance(record.get("title"), str) or not record["title"].strip():
                raise RegistryIntegrityError(
                    "Invalid tracked-channel registry: missing channel title"
                )
        else:
            raise RegistryIntegrityError(
                "Invalid tracked-channel registry: invalid channel record type"
            )
    last_run = data.get("last_discovery_run")
    if last_run is not None and (not isinstance(last_run, str) or not last_run.strip()):
        raise RegistryIntegrityError(
            "Invalid tracked-channel registry: bad discovery bookmark"
        )
    return data


def read_registry(path):
    """Read one complete existing registry; never substitute an empty one."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RegistryIntegrityError(
            "Tracked-channel registry is missing; restore it before rerunning"
        ) from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise RegistryIntegrityError(
            "Tracked-channel registry is corrupt; restore it before rerunning"
        ) from exc
    return validate_registry(data)


def write_registry(path, channels, *, last_discovery_run):
    """Atomically save a verified nonempty registry, never create on missing.

    Verify the on-disk original again immediately before writing; this protects
    against a file disappearing or becoming corrupt between discovery read and
    commit. Any I/O failure leaves the original filename untouched.
    """
    original = read_registry(path)
    data = {**original, "channels": channels,
            "last_discovery_run": last_discovery_run}
    validate_registry(data)

    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(target.parent),
                prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
