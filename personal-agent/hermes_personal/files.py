"""Constrained, verified filesystem operations. Never executes model-written code."""
from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import threading
import zipfile
from pathlib import Path


SENSITIVE_NAMES = {".git", ".ssh", ".aws", ".azure", ".kube", ".gnupg", "auth.json", "credentials", "credentials.json", "login data", "cookies", "user data", "browser-profile"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx"}


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def windows_reserved(part: str) -> bool:
    check = getattr(os.path, "isreserved", None)
    if check is not None:
        return check(part)
    # os.path.isreserved arrived in Python 3.13. Keep the same restrictions
    # for the supported Python 3.11/3.12 project environments.
    device = part.partition(".")[0].rstrip(" ").upper()
    names = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    names.update(prefix + number for prefix in ("COM", "LPT") for number in "123456789¹²³")
    return (part.endswith((" ", ".")) or device in names
            or any(ord(char) < 32 or char in '*?"<>|:' for char in part))


class PathPolicy:
    def __init__(self, roots, protected=()):
        self.roots = tuple(Path(p).resolve() for p in roots)
        self.protected = tuple(Path(p).resolve() for p in protected)

    def resolve(self, value: str, *, exists=False) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("A nonempty absolute local path is required")
        path = Path(value)
        if not path.is_absolute() or value.startswith(("\\\\", "//")):
            raise ValueError("Use an absolute local drive path; network/device paths are disabled")
        if os.name == "nt" and ":" in str(path)[len(path.drive):]:
            raise ValueError("NTFS alternate data streams are disabled")
        for part in path.parts[1:]:
            low = part.lower()
            if low in SENSITIVE_NAMES or low.startswith(".env") or Path(low).suffix in SENSITIVE_SUFFIXES:
                raise PermissionError("Credential and configuration paths are protected")
            if os.name == "nt" and windows_reserved(part):
                raise ValueError("Reserved or ambiguous Windows path")
        resolved = path.resolve(strict=exists)
        # Check both lexical and resolved names: symlinks/junctions cannot turn
        # an allowed ordinary name into a protected file or escape the roots.
        for part in resolved.parts[1:]:
            low = part.lower()
            if low in SENSITIVE_NAMES or low.startswith(".env") or Path(low).suffix in SENSITIVE_SUFFIXES:
                raise PermissionError("Resolved credential path is protected")
        if not any(within(resolved, root) for root in self.roots):
            raise PermissionError("Path is outside configured personal folders")
        if any(within(resolved, root) for root in self.protected):
            raise PermissionError("Agent runtime, credentials, and policy files are protected")
        return resolved


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class FileService:
    def __init__(self, policy: PathPolicy, *, trash_fn=None):
        self.policy = policy
        self._lock = threading.RLock()
        self.trash_fn = trash_fn

    def _walk(self, root, *, limit=5000):
        seen = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            safe_dirs = []
            for name in sorted(dirs):
                candidate = Path(directory) / name
                try:
                    self.policy.resolve(str(candidate), exists=True)
                    if not candidate.is_symlink() and not (hasattr(candidate, "is_junction") and candidate.is_junction()):
                        safe_dirs.append(name)
                except (OSError, ValueError):
                    pass
            dirs[:] = safe_dirs
            for name in sorted(files):
                seen += 1
                if seen > limit:
                    raise ValueError("Folder exceeds the scan limit; choose a smaller folder")
                candidate = Path(directory) / name
                try:
                    resolved = self.policy.resolve(str(candidate), exists=True)
                    if not candidate.is_symlink():
                        yield resolved
                except (OSError, ValueError):
                    continue

    def run(self, action, path, **kwargs):
        try:
            with self._lock:
                return self._run(action, path, **kwargs)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    def _run(self, action, path, **kwargs):
        allowed = {"list", "search", "read", "write", "mkdir", "copy", "move", "rename", "archive", "trash"}
        if action not in allowed:
            raise ValueError("Unsupported action; permanent deletion is not a routine tool")
        source = self.policy.resolve(path, exists=action not in {"write", "mkdir"})
        if action == "list":
            entries = []
            for child in sorted(source.iterdir(), key=lambda p: p.name.lower()):
                try:
                    verified = self.policy.resolve(str(child), exists=True)
                    entries.append({"name": child.name, "path": str(verified), "directory": child.is_dir(), "bytes": child.stat().st_size if child.is_file() else None})
                except (OSError, ValueError):
                    continue
                if len(entries) >= 500:
                    break
            return {"ok": True, "entries": entries, "limit": 500}
        if action == "search":
            result = []
            limit = max(1, min(int(kwargs.get("limit", 50)), 200))
            pattern, query = kwargs.get("pattern", "*"), kwargs.get("query", "")
            for candidate in self._walk(source):
                if fnmatch.fnmatch(candidate.name.lower(), pattern.lower()):
                    if query:
                        if candidate.stat().st_size > 1_048_576:
                            continue
                        try:
                            if query.casefold() not in candidate.read_text(encoding="utf-8").casefold():
                                continue
                        except UnicodeError:
                            continue
                    result.append({"path": str(candidate), "bytes": candidate.stat().st_size})
                    if len(result) >= limit:
                        break
            return {"ok": True, "matches": result, "limit": limit}
        if action == "read":
            if source.stat().st_size > 1_048_576:
                raise ValueError("Text read limit is 1 MiB; request a smaller extract")
            return {"ok": True, "path": str(source), "content": source.read_text(encoding="utf-8"), "sha256": digest(source)}
        if action == "mkdir":
            source.mkdir(parents=True, exist_ok=True)
            return {"ok": source.is_dir(), "path": str(source), "evidence": "Directory exists"}
        if action == "write":
            data = kwargs.get("content", "").encode("utf-8")
            if len(data) > 1_048_576:
                raise ValueError("Text write limit is 1 MiB")
            # Exclusive creation: no silent overwrite, including races.
            with source.open("xb") as stream:
                stream.write(data)
            expected = hashlib.sha256(data).hexdigest()
            if digest(source) != expected:
                raise OSError("Written file verification failed")
            return {"ok": True, "path": str(source), "sha256": expected, "bytes": len(data), "evidence": "Written bytes verified", "artifacts": [str(source)]}
        if action == "trash":
            if source in self.policy.roots:
                raise PermissionError("Cannot trash a configured root folder")
            if source.is_dir():
                self._validate_tree(source)
            if self.trash_fn is None:
                from send2trash import send2trash
                send2trash(str(source))
            else:
                self.trash_fn(str(source))
            return {"ok": not source.exists(), "path": str(source), "evidence": "Moved to Windows Recycle Bin; original path absent"}
        destination = self.policy.resolve(kwargs["destination"])
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Destination already exists; choose a new name")
        if source == destination or (source.is_dir() and within(destination, source) and action != "archive"):
            raise ValueError("Destination cannot be inside the source directory")
        if action == "archive":
            candidates = list(self._walk(source)) if source.is_dir() else [source]
            candidates = [p for p in candidates if p != destination]
            if len(candidates) > 2000 or sum(p.stat().st_size for p in candidates) > 256 * 1024 * 1024:
                raise ValueError("Archive limit: 2000 files or 256 MiB")
            created = False
            try:
                with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
                    created = True
                    for item in candidates:
                        self.policy.resolve(str(item), exists=True)
                        archive.write(item, item.relative_to(source) if source.is_dir() else item.name)
                with zipfile.ZipFile(destination) as archive:
                    if archive.testzip():
                        raise OSError("Archive integrity verification failed")
            except Exception:
                # Only our exclusively created archive can be removed here.
                if created and destination.exists():
                    destination.unlink()
                raise
            return {"ok": True, "path": str(destination), "files": len(candidates), "sha256": digest(destination), "evidence": "ZIP contents and CRC verified", "artifacts": [str(destination)]}
        if source.is_dir():
            self._validate_tree(source)
            expected = self._manifest(source)
            if action in {"move", "rename"}:
                if source.drive != destination.drive:
                    raise ValueError("For cross-drive folders, copy first, verify, then trash the original")
                source.rename(destination)
            else:
                destination.mkdir()
                for directory, dirs, files in os.walk(source, followlinks=False):
                    relative = Path(directory).relative_to(source)
                    for name in dirs:
                        self.policy.resolve(str(Path(directory) / name), exists=True)
                        (destination / relative / name).mkdir()
                    for name in files:
                        original = self.policy.resolve(str(Path(directory) / name), exists=True)
                        with original.open("rb") as incoming, (destination / relative / name).open("xb") as outgoing:
                            shutil.copyfileobj(incoming, outgoing)
            if self._manifest(destination) != expected:
                raise OSError("Folder verification failed; inspect the preserved destination")
            return {"ok": True, "path": str(destination), "files": sum(not p.endswith("/") for p in expected), "source_absent": not source.exists(), "evidence": "Folder structure and file hashes verified"}
        original_hash = digest(source)
        try:
            with source.open("rb") as incoming, destination.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if digest(destination) != original_hash:
                raise OSError("Destination verification failed")
            if action in {"move", "rename"}:
                self.policy.resolve(str(source), exists=True)
                if digest(source) != original_hash:
                    raise OSError("Source changed during move; both files have been preserved")
                source.unlink()
        except Exception:
            # Keep the source; leave any partial copy visible for recovery.
            raise
        return {"ok": True, "path": str(destination), "sha256": original_hash, "source_absent": not source.exists(), "evidence": "Destination bytes verified", "artifacts": [str(destination)]}

    def _validate_tree(self, root):
        count = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                count += 1
                if count > 5000:
                    raise ValueError("Folder exceeds 5000 entries; choose a smaller folder")
                candidate = Path(directory) / name
                self.policy.resolve(str(candidate), exists=True)
                if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
                    raise PermissionError("Bulk mutations containing links/junctions are disabled")

    def _manifest(self, root):
        manifest = {}
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs:
                manifest[str((Path(directory) / name).relative_to(root)).replace("\\", "/") + "/"] = None
            for name in files:
                path = self.policy.resolve(str(Path(directory) / name), exists=True)
                manifest[str(path.relative_to(root)).replace("\\", "/")] = digest(path)
        return manifest
