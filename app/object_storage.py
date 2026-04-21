from __future__ import annotations

import asyncio
import logging
import mimetypes
import threading
from pathlib import Path, PurePosixPath

from minio import Minio


class MinIOObjectStore:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket_name: str,
        secure: bool = False,
        region: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.bucket_name = bucket_name
        self.region = region
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
        )
        self._bucket_ready = False
        self._bucket_lock = threading.Lock()

    @staticmethod
    def normalize_key(*parts: str) -> str:
        segments: list[str] = []
        for part in parts:
            for segment in str(part or "").replace("\\", "/").split("/"):
                cleaned = segment.strip()
                if not cleaned:
                    continue
                if cleaned in {".", ".."}:
                    raise ValueError(f"Invalid object storage path segment: {cleaned}")
                segments.append(cleaned)
        return str(PurePosixPath(*segments))

    def ensure_bucket_sync(self) -> None:
        if self._bucket_ready:
            return
        with self._bucket_lock:
            if self._bucket_ready:
                return
            last_error: Exception | None = None
            for attempt in range(1, 11):
                try:
                    exists = self._client.bucket_exists(self.bucket_name)
                    if not exists:
                        self._client.make_bucket(
                            self.bucket_name,
                            location=self.region,
                        )
                    self._bucket_ready = True
                    return
                except Exception as exc:
                    last_error = exc
                    if attempt == 10:
                        break
                    logging.warning(
                        "MinIO bucket initialization attempt %s/10 failed: %s",
                        attempt,
                        exc,
                    )
                    time_to_sleep = min(attempt, 3)
                    threading.Event().wait(time_to_sleep)

            if last_error is not None:
                raise last_error

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self.ensure_bucket_sync)

    def upload_file_sync(
        self,
        object_key: str,
        source_path: Path,
        content_type: str | None = None,
    ) -> str:
        self.ensure_bucket_sync()
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Object storage upload source does not exist: {source}")

        normalized_key = self.normalize_key(object_key)
        media_type = (
            content_type
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream"
        )
        self._client.fput_object(
            self.bucket_name,
            normalized_key,
            str(source),
            content_type=media_type,
        )
        return normalized_key

    async def upload_file(
        self,
        object_key: str,
        source_path: Path,
        content_type: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self.upload_file_sync,
            object_key,
            source_path,
            content_type,
        )

    def upload_directory_sync(self, object_prefix: str, source_dir: Path) -> int:
        self.ensure_bucket_sync()
        directory = Path(source_dir)
        if not directory.exists():
            return 0

        uploaded = 0
        prefix = self.normalize_key(object_prefix)
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            relative_path = file_path.relative_to(directory).as_posix()
            self.upload_file_sync(
                self.normalize_key(prefix, relative_path),
                file_path,
            )
            uploaded += 1
        return uploaded

    async def upload_directory(self, object_prefix: str, source_dir: Path) -> int:
        return await asyncio.to_thread(
            self.upload_directory_sync,
            object_prefix,
            source_dir,
        )

    def download_file_sync(self, object_key: str, target_path: Path) -> bool:
        self.ensure_bucket_sync()
        normalized_key = self.normalize_key(object_key)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._client.fget_object(
                self.bucket_name,
                normalized_key,
                str(target),
            )
            return True
        except Exception:
            target.unlink(missing_ok=True)
            return False

    async def download_file(self, object_key: str, target_path: Path) -> bool:
        return await asyncio.to_thread(
            self.download_file_sync,
            object_key,
            target_path,
        )

    def download_prefix_sync(self, object_prefix: str, target_dir: Path) -> int:
        self.ensure_bucket_sync()
        prefix = self.normalize_key(object_prefix).rstrip("/") + "/"
        destination = Path(target_dir)
        return self._download_prefix_sync(prefix, destination)

    async def download_prefix(self, object_prefix: str, target_dir: Path) -> int:
        return await asyncio.to_thread(
            self.download_prefix_sync,
            object_prefix,
            target_dir,
        )

    def _download_prefix_sync(self, prefix: str, target_dir: Path) -> int:
        downloaded = 0
        for obj in self._client.list_objects(
            self.bucket_name,
            prefix=prefix,
            recursive=True,
        ):
            relative = obj.object_name[len(prefix):]
            if not relative:
                continue
            file_path = target_dir / relative
            file_path.parent.mkdir(parents=True, exist_ok=True)
            self._client.fget_object(self.bucket_name, obj.object_name, str(file_path))
            downloaded += 1
        return downloaded

    def delete_prefix_sync(self, object_prefix: str) -> int:
        self.ensure_bucket_sync()
        prefix = self.normalize_key(object_prefix).rstrip("/") + "/"
        return self._delete_prefix_sync(prefix)

    async def delete_prefix(self, object_prefix: str) -> int:
        return await asyncio.to_thread(self.delete_prefix_sync, object_prefix)

    def _delete_prefix_sync(self, prefix: str) -> int:
        deleted = 0
        for obj in self._client.list_objects(
            self.bucket_name,
            prefix=prefix,
            recursive=True,
        ):
            self._client.remove_object(self.bucket_name, obj.object_name)
            deleted += 1
        return deleted
