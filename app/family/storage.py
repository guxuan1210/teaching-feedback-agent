"""Safe, local storage for student images."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import tempfile
from typing import BinaryIO, Iterable


class ImageStorageError(Exception):
    """Base error for unsafe or failed image storage operations."""


class UnsupportedImage(ImageStorageError):
    """The image bytes do not have a supported image signature."""


class ImageTooLarge(ImageStorageError):
    """The image exceeds the configured size limit."""


@dataclass(frozen=True)
class StoredImage:
    relative_path: str
    mime_type: str
    extension: str
    byte_size: int
    sha256: str


_FORMATS = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]+\Z")


class LocalImageStore:
    def __init__(
        self,
        root: str | Path,
        max_bytes: int = 10 * 1024 * 1024,
        quarantine_root: str | Path | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ImageStorageError("max_bytes must be positive")
        self.root = Path(root).expanduser().resolve()
        self.quarantine_root = Path(quarantine_root).expanduser().resolve() if quarantine_root is not None else (self.root.parent / (self.root.name + "-quarantine")).resolve()
        if self.root == self.quarantine_root or self.root in self.quarantine_root.parents or self.quarantine_root in self.root.parents:
            raise ImageStorageError("image and quarantine roots must be disjoint")
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self.quarantine_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _check_identifier(value: str, label: str) -> None:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ImageStorageError(f"invalid {label}")

    @staticmethod
    def _relative(value: str) -> PurePosixPath:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ImageStorageError("invalid relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise ImageStorageError("invalid relative path")
        return path

    @staticmethod
    def _safe_path(root: Path, relative: str, *, must_exist: bool = True) -> Path:
        parts = LocalImageStore._relative(relative).parts
        path = root
        for part in parts:
            path = path / part
            try:
                if path.is_symlink():
                    raise ImageStorageError("symlink paths are not allowed")
            except OSError as exc:
                raise ImageStorageError("cannot inspect path") from exc
        try:
            resolved = path.resolve(strict=must_exist)
            resolved.relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise ImageStorageError("path is outside storage root or missing") from exc
        return resolved

    @staticmethod
    def _identify(prefix: bytes) -> tuple[str, str] | None:
        if prefix.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "jpg"
        if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "png"
        if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
            return "image/webp", "webp"
        return None

    def save(self, student_id: str, image_id: str, chunks: Iterable[bytes], now: datetime | None = None) -> StoredImage:
        self._check_identifier(student_id, "student_id")
        self._check_identifier(image_id, "image_id")
        moment = datetime.now(timezone.utc) if now is None else now
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ImageStorageError("now must be timezone-aware")
        moment = moment.astimezone(timezone.utc)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".", suffix=".pending", dir=self.root)
        temp_path = Path(temporary)
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        try:
            with os.fdopen(fd, "wb") as target:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise UnsupportedImage("image chunks must be bytes")
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ImageTooLarge("image exceeds max_bytes")
                    if len(prefix) < 12:
                        prefix.extend(chunk[: 12 - len(prefix)])
                    target.write(chunk)
                    digest.update(chunk)
            identified = self._identify(bytes(prefix))
            if identified is None:
                raise UnsupportedImage("unsupported or incomplete image signature")
            mime_type, extension = identified
            relative = f"{student_id}/{moment:%Y/%m}/{image_id}.{extension}"
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise ImageStorageError("image path already exists")
            os.replace(temp_path, destination)
            return StoredImage(relative, mime_type, extension, size, digest.hexdigest())
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            finally:
                raise

    def open(self, relative_path: str) -> BinaryIO:
        return self._safe_path(self.root, relative_path).open("rb")

    def quarantine(self, relative_path: str) -> str:
        source = self._safe_path(self.root, relative_path)
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        for _ in range(20):
            name = secrets.token_hex(16) + source.suffix.lower()
            destination = self.quarantine_root / name
            try:
                fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                continue
            try:
                with source.open("rb") as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                source.unlink()
                return name
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        raise ImageStorageError("unable to allocate quarantine path")

    def restore(self, quarantine_path: str, original_relative_path: str) -> None:
        source = self._safe_path(self.quarantine_root, quarantine_path)
        destination = self._safe_path(self.root, original_relative_path, must_exist=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with source.open("rb") as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst)
        except FileExistsError as exc:
            raise ImageStorageError("restore destination already exists") from exc
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        source.unlink()

    def remove_quarantined(self, quarantine_path: str) -> None:
        self._safe_path(self.quarantine_root, quarantine_path).unlink()

    def remove_temporary(self, temporary_path: str) -> None:
        self._safe_path(self.root, temporary_path).unlink()
