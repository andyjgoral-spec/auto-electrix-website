"""Private file storage and ECU file validation.

Files are written outside the public static folder under random names, and
are only ever served through permission-checked download routes. The app
stores files byte-for-byte: nothing here reads or changes calibration data
beyond checking that the upload is a plausible ECU read.
"""
import hashlib
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from .models import utcnow


class FileRejected(ValueError):
    pass


# Signatures of file types that are never ECU reads.
FORBIDDEN_SIGNATURES = [
    (b"MZ", "Windows programs"),
    (b"\x7fELF", "Linux programs"),
    (b"PK\x03\x04", "ZIP archives or Office documents"),
    (b"Rar!", "RAR archives"),
    (b"7z\xbc\xaf", "7-Zip archives"),
    (b"%PDF", "PDF documents"),
    (b"#!", "scripts"),
    (b"<?php", "scripts"),
    (b"\x89PNG", "images"),
    (b"\xff\xd8\xff", "images"),
    (b"GIF8", "images"),
]


@dataclass
class StoredFile:
    storage_key: str
    filename: str
    size: int
    sha256: str


def _root():
    root = Path(current_app.config["STORAGE_DIR"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_for(storage_key):
    root = _root()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise FileNotFoundError(storage_key)
    return path


def validate_ecu_file(filename, data):
    """Check an uploaded ECU read. Raises FileRejected with a plain-English reason."""
    cfg = current_app.config
    safe = secure_filename(filename or "")
    if not safe or "." not in safe:
        raise FileRejected("The file needs a name with an extension, for example original.bin.")
    ext = safe.rsplit(".", 1)[1].lower()
    if ext not in cfg["ECU_ALLOWED_EXTENSIONS"]:
        allowed = ", ".join("." + e for e in sorted(cfg["ECU_ALLOWED_EXTENSIONS"]))
        raise FileRejected(f"Files ending .{ext} are not accepted. Accepted types: {allowed}.")
    size = len(data)
    if size < cfg["ECU_MIN_BYTES"]:
        raise FileRejected("The file is too small to be a full ECU read. Please check the read and try again.")
    if size > cfg["ECU_MAX_BYTES"]:
        limit = cfg["ECU_MAX_BYTES"] // (1024 * 1024)
        raise FileRejected(f"The file is larger than {limit} MB.")
    head = data[:16]
    for signature, label in FORBIDDEN_SIGNATURES:
        if head.startswith(signature):
            raise FileRejected(f"This looks like one of the {label}, not an ECU read.")
    lowered = data[:512].lower()
    if b"<html" in lowered or b"<script" in lowered:
        raise FileRejected("This looks like a web page, not an ECU read.")
    if ext == "hex":
        lines = [ln for ln in data.splitlines()[:50] if ln.strip()]
        if not lines or not all(ln.strip().startswith(b":") for ln in lines):
            raise FileRejected("This .hex file is not in Intel HEX format.")
    if ext in ("s19", "srec", "mot"):
        lines = [ln for ln in data.splitlines()[:50] if ln.strip()]
        if not lines or not all(ln.strip()[:1] == b"S" for ln in lines):
            raise FileRejected("This file is not in Motorola S-record format.")
    if ext in ("bin", "ori") and data.count(data[:1]) == size:
        raise FileRejected("Every byte in this file is the same value. The read is probably blank or failed.")
    return safe


def save_bytes(data, filename, folder):
    """Write bytes to private storage. Returns a StoredFile."""
    now = utcnow()
    key = f"{folder}/{now:%Y/%m}/{secrets.token_hex(16)}"
    path = path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return StoredFile(
        storage_key=key,
        filename=secure_filename(filename) or "file.bin",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def copy_stored(storage_key, filename, folder):
    with open(path_for(storage_key), "rb") as fh:
        return save_bytes(fh.read(), filename, folder)


def sha256_of(storage_key):
    digest = hashlib.sha256()
    with open(path_for(storage_key), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remove_all():
    """Used by tests and the demo reset command only."""
    root = Path(current_app.config["STORAGE_DIR"]).resolve()
    if root.exists():
        shutil.rmtree(root)
