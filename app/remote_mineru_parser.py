from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from app.object_storage import MinIOObjectStore
from raganything.parser import MineruParser, Parser, register_parser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteMineruParserConfig:
    base_url: str
    api_key: str | None = None
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 1800.0
    default_backend: str = "pipeline"
    default_source: str = "local"
    default_device: str | None = None


class RemoteMineruParseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str
    job_id: str
    status: str
    output_object_prefix: str
    pages: int | None = None
    duration_seconds: float | None = None
    error_message: str | None = None


class RemoteMineruParser(Parser):
    _config: RemoteMineruParserConfig | None = None
    _object_store: MinIOObjectStore | None = None
    _results: dict[str, RemoteMineruParseResponse] = {}
    _results_lock = threading.Lock()

    def check_installation(self) -> bool:
        return self.__class__._config is not None and self.__class__._object_store is not None

    def parse_pdf(
        self,
        pdf_path,
        output_dir: str | None = None,
        method: str = "auto",
        lang: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        return self._parse_remote(
            file_path=Path(pdf_path),
            output_dir=output_dir,
            parse_method=method,
            lang=lang,
            content_kind="pdf",
            **kwargs,
        )

    def parse_image(
        self,
        image_path,
        output_dir: str | None = None,
        lang: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        return self._parse_remote(
            file_path=Path(image_path),
            output_dir=output_dir,
            parse_method="ocr",
            lang=lang,
            content_kind="image",
            **kwargs,
        )

    def parse_office_doc(
        self,
        doc_path,
        output_dir: str | None = None,
        lang: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        return self._parse_remote(
            file_path=Path(doc_path),
            output_dir=output_dir,
            parse_method=kwargs.get("method", "auto"),
            lang=lang,
            content_kind="office",
            **kwargs,
        )

    def parse_document(
        self,
        file_path,
        output_dir: str | None = None,
        method: str = "auto",
        lang: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        return self._parse_remote(
            file_path=Path(file_path),
            output_dir=output_dir,
            parse_method=method,
            lang=lang,
            content_kind="generic",
            **kwargs,
        )

    def _parse_remote(
        self,
        file_path: Path,
        output_dir: str | None,
        parse_method: str,
        lang: str | None,
        content_kind: str,
        **kwargs,
    ) -> list[dict[str, Any]]:
        config = self.__class__._config
        object_store = self.__class__._object_store
        if config is None or object_store is None:
            raise RuntimeError("remote_mineru parser is not configured.")

        if not file_path.exists():
            raise FileNotFoundError(f"Input file does not exist: {file_path}")

        output_root = Path(output_dir) if output_dir else file_path.parent / "mineru_output"
        base_output_dir = MineruParser._unique_output_dir(output_root, file_path)
        request_id = str(kwargs.get("request_id") or kwargs.get("document_id") or file_path.name)
        input_object_key = str(kwargs.get("input_object_key") or "")
        output_object_prefix = str(kwargs.get("output_object_prefix") or "")
        if not input_object_key:
            raise RuntimeError("input_object_key is required for remote_mineru parser.")
        if not output_object_prefix:
            raise RuntimeError("output_object_prefix is required for remote_mineru parser.")

        read_method = self._resolve_read_method(
            parse_method=parse_method,
            backend=str(kwargs.get("backend") or config.default_backend),
            content_kind=content_kind,
        )

        local_content = self._read_local_output_if_available(
            base_output_dir,
            file_path.stem,
            read_method,
        )
        if local_content is not None:
            return local_content

        downloaded = object_store.download_prefix_sync(output_object_prefix, base_output_dir)
        if downloaded > 0:
            local_content = self._read_local_output_if_available(
                base_output_dir,
                file_path.stem,
                read_method,
            )
            if local_content is not None:
                logger.info("Reused remote MinerU output for %s from object storage.", request_id)
                return local_content

        response = self._call_parse_service(
            config=config,
            request_id=request_id,
            file_path=file_path,
            input_object_key=input_object_key,
            output_object_prefix=output_object_prefix,
            parse_method=parse_method,
            lang=lang,
            content_kind=content_kind,
            **kwargs,
        )
        self._store_result(response)

        if response.status != "completed":
            raise RuntimeError(response.error_message or f"Remote MinerU parse failed for {request_id}.")

        if base_output_dir.exists():
            shutil.rmtree(base_output_dir, ignore_errors=True)
        downloaded = object_store.download_prefix_sync(response.output_object_prefix, base_output_dir)
        if downloaded == 0:
            raise FileNotFoundError(
                f"Remote MinerU parse completed but no output was found in object storage: {response.output_object_prefix}"
            )

        local_content = self._read_local_output_if_available(
            base_output_dir,
            file_path.stem,
            read_method,
        )
        if local_content is None:
            raise RuntimeError(f"Failed to materialize remote MinerU output for {request_id}.")
        return local_content

    def _call_parse_service(
        self,
        config: RemoteMineruParserConfig,
        request_id: str,
        file_path: Path,
        input_object_key: str,
        output_object_prefix: str,
        parse_method: str,
        lang: str | None,
        content_kind: str,
        **kwargs,
    ) -> RemoteMineruParseResponse:
        headers: dict[str, str] = {}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        payload = {
            "request_id": request_id,
            "knowledge_base_id": kwargs.get("knowledge_base_id"),
            "document_id": kwargs.get("document_id") or request_id,
            "file_name": kwargs.get("parser_file_name") or file_path.name,
            "input_object_key": input_object_key,
            "output_object_prefix": output_object_prefix,
            "parse_method": parse_method,
            "backend": kwargs.get("backend") or config.default_backend,
            "lang": lang,
            "source": kwargs.get("source") or config.default_source,
            "device": kwargs.get("device", config.default_device),
            "start_page": kwargs.get("start_page"),
            "end_page": kwargs.get("end_page"),
            "enable_formula": kwargs.get("formula", True),
            "enable_table": kwargs.get("table", True),
            "content_kind": content_kind,
        }

        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.connect_timeout_seconds,
            pool=config.connect_timeout_seconds,
        )

        try:
            with httpx.Client(base_url=config.base_url.rstrip("/"), timeout=timeout, headers=headers) as client:
                http_response = client.post("/api/v1/parse/mineru", json=payload)
                http_response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Remote MinerU service request failed: {exc}") from exc

        try:
            response_json = http_response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Remote MinerU service returned a non-JSON response.") from exc

        return RemoteMineruParseResponse.model_validate(response_json)

    @staticmethod
    def _resolve_read_method(parse_method: str, backend: str, content_kind: str) -> str:
        if content_kind == "image":
            return "ocr"
        if backend.startswith("vlm-"):
            return "vlm"
        if backend.startswith("hybrid-"):
            return "hybrid_auto"
        return parse_method or "auto"

    @staticmethod
    def _has_output_artifacts(base_output_dir: Path, file_stem: str, method: str) -> bool:
        direct_json = base_output_dir / f"{file_stem}_content_list.json"
        if direct_json.exists():
            return True

        stem_dir = base_output_dir / file_stem
        if stem_dir.is_dir():
            fallback_json = stem_dir / method / f"{file_stem}_content_list.json"
            if fallback_json.exists():
                return True
            for subdir in stem_dir.iterdir():
                if not subdir.is_dir():
                    continue
                candidate_json = subdir / f"{file_stem}_content_list.json"
                if candidate_json.exists():
                    return True
        return False

    @classmethod
    def _read_local_output_if_available(
        cls,
        base_output_dir: Path,
        file_stem: str,
        method: str,
    ) -> list[dict[str, Any]] | None:
        if not cls._has_output_artifacts(base_output_dir, file_stem, method):
            return None
        content_list, _ = MineruParser._read_output_files(base_output_dir, file_stem, method=method)
        return content_list

    @classmethod
    def _store_result(cls, response: RemoteMineruParseResponse) -> None:
        with cls._results_lock:
            cls._results[response.request_id] = response

    @classmethod
    def consume_result(cls, request_id: str) -> RemoteMineruParseResponse | None:
        with cls._results_lock:
            return cls._results.pop(request_id, None)


def configure_remote_mineru_parser(
    config: RemoteMineruParserConfig,
    object_store: MinIOObjectStore,
) -> None:
    RemoteMineruParser._config = config
    RemoteMineruParser._object_store = object_store
    register_parser("remote_mineru", RemoteMineruParser)


def consume_remote_mineru_parse_result(request_id: str) -> RemoteMineruParseResponse | None:
    return RemoteMineruParser.consume_result(request_id)
