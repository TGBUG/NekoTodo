from __future__ import annotations

from pathlib import Path

_storage: "Storage | None" = None


class Storage:
    """On-disk file storage, one directory per user, UUID filenames.

    Files live at ``{base_dir}/{user_id}/{uuid}`` with no extension; the
    real content type is detected from magic bytes when needed.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def _user_dir(self, user_id: int) -> Path:
        return self.base_dir / str(user_id)

    def save(self, user_id: int, uuid: str, data: bytes) -> Path:
        user_dir = self._user_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        path = user_dir / uuid
        path.write_bytes(data)
        return path

    def path(self, user_id: int, uuid: str) -> Path:
        return self._user_dir(user_id) / uuid

    def read(self, user_id: int, uuid: str) -> bytes:
        return self.path(user_id, uuid).read_bytes()

    def exists(self, user_id: int, uuid: str) -> bool:
        return self.path(user_id, uuid).exists()

    def delete(self, user_id: int, uuid: str) -> None:
        try:
            self.path(user_id, uuid).unlink(missing_ok=True)
        except OSError:
            pass


def init_storage(base_dir: str | Path) -> Storage:
    global _storage
    _storage = Storage(base_dir)
    return _storage


def get_storage() -> Storage:
    if _storage is None:
        raise RuntimeError("storage not initialized")
    return _storage


def detect_image_mime(data: bytes) -> str | None:
    """Sniff the real format from magic bytes; returns a mime or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None
