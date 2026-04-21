from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import uvicorn
import yaml
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

ROOT_DIR = Path(__file__).resolve().parents[1]
for dependency_dir in (ROOT_DIR / "RAG-Anything",):
    dependency_path = str(dependency_dir)
    if dependency_dir.exists() and dependency_path not in sys.path:
        sys.path.insert(0, dependency_path)

from app.object_storage import MinIOObjectStore
from raganything.parser import MineruExecutionError, MineruParser


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def generate_job_id() -> str:
    return f"parse_{uuid4().hex[:12]}"


class ServerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = 8090
    log_level: str = "INFO"


class AuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    api_key_env: str = "MINERU_PARSER_API_KEY"

    def resolve_api_key(self) -> str:
        import os

        api_key = os.getenv(self.api_key_env)
        if api_key:
            return api_key
        raise ValueError(f"Environment variable {self.api_key_env} is required for parser service auth.")


class ObjectStorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = "minio:9000"
    bucket: str = "multimodal-kb"
    access_key_env: str = "MINIO_ACCESS_KEY_ID"
    secret_key_env: str = "MINIO_SECRET_ACCESS_KEY"
    secure: bool = False
    region: str | None = None

    def resolve_access_key(self) -> str:
        import os

        access_key = os.getenv(self.access_key_env)
        if access_key:
            return access_key
        raise ValueError(f"Environment variable {self.access_key_env} is required for MinIO access.")

    def resolve_secret_key(self) -> str:
        import os

        secret_key = os.getenv(self.secret_key_env)
        if secret_key:
            return secret_key
        raise ValueError(f"Environment variable {self.secret_key_env} is required for MinIO access.")


class MineruRuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_backend: str = "pipeline"
    default_device: str | None = "cuda:0"
    source: str = "local"
    max_concurrency: int = 1
    local_work_root: Path = Path("./data/parser_jobs")
    cleanup_local_inputs: bool = True
    cleanup_local_outputs: bool = True


class ParserServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    object_storage: ObjectStorageSettings = Field(default_factory=ObjectStorageSettings)
    mineru: MineruRuntimeSettings = Field(default_factory=MineruRuntimeSettings)


class ParseMineruRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    knowledge_base_id: str | None = None
    document_id: str | None = None
    file_name: str
    input_object_key: str
    output_object_prefix: str
    parse_method: str = "auto"
    backend: str = "pipeline"
    lang: str | None = None
    source: str = "local"
    device: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    enable_formula: bool = True
    enable_table: bool = True
    content_kind: Literal["pdf", "image", "office", "generic"] = "generic"


class ParseMineruResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    job_id: str
    status: Literal["completed", "failed"]
    output_object_prefix: str
    pages: int | None = None
    duration_seconds: float | None = None
    error_message: str | None = None


class MineruParseRunner:
    def __init__(self, settings: ParserServiceSettings) -> None:
        self.settings = settings
        self.object_store = MinIOObjectStore(
            endpoint=settings.object_storage.endpoint,
            access_key=settings.object_storage.resolve_access_key(),
            secret_key=settings.object_storage.resolve_secret_key(),
            bucket_name=settings.object_storage.bucket,
            secure=settings.object_storage.secure,
            region=settings.object_storage.region,
        )
        self._semaphore = asyncio.Semaphore(max(1, settings.mineru.max_concurrency))

    async def initialize(self) -> None:
        await self.object_store.ensure_bucket()
        self.settings.mineru.local_work_root.mkdir(parents=True, exist_ok=True)

    async def run_parse(self, payload: ParseMineruRequest) -> ParseMineruResponse:
        async with self._semaphore:
            return await asyncio.to_thread(self._run_parse_sync, payload)

    def _run_parse_sync(self, payload: ParseMineruRequest) -> ParseMineruResponse:
        started_at = datetime.now(UTC)
        job_id = generate_job_id()
        job_root = self.settings.mineru.local_work_root / job_id
        input_dir = job_root / "input"
        convert_dir = job_root / "convert"
        output_root = job_root / "output"
        parse_dir_name = payload.output_object_prefix.rstrip("/").split("/")[-1]
        output_dir = output_root / parse_dir_name
        local_input_path = input_dir / Path(payload.input_object_key).name

        input_dir.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(parents=True, exist_ok=True)

        response = ParseMineruResponse(
            request_id=payload.request_id,
            job_id=job_id,
            status="failed",
            output_object_prefix=payload.output_object_prefix,
            pages=None,
            duration_seconds=None,
            error_message=None,
        )

        try:
            downloaded = self.object_store.download_file_sync(payload.input_object_key, local_input_path)
            if not downloaded:
                raise FileNotFoundError(f"Input object not found in MinIO: {payload.input_object_key}")

            content_list = self._parse_document_sync(
                payload=payload,
                input_path=local_input_path,
                convert_dir=convert_dir,
                output_dir=output_dir,
            )

            if not content_list:
                raise ValueError("Parsing failed: No content was extracted")

            self.object_store.delete_prefix_sync(payload.output_object_prefix)
            self.object_store.upload_directory_sync(payload.output_object_prefix, output_dir)
            response = response.model_copy(
                update={
                    "status": "completed",
                    "pages": self._extract_page_count(content_list),
                    "error_message": None,
                }
            )
        except Exception as exc:
            logging.exception("MinerU parse job failed for %s", payload.request_id)
            try:
                if output_dir.exists():
                    self.object_store.delete_prefix_sync(payload.output_object_prefix)
                    self.object_store.upload_directory_sync(payload.output_object_prefix, output_dir)
            except Exception:
                logging.exception("Failed to upload partial MinerU output for %s", payload.request_id)
            response = response.model_copy(update={"error_message": str(exc)})
        finally:
            duration = (datetime.now(UTC) - started_at).total_seconds()
            response = response.model_copy(update={"duration_seconds": duration})
            if self.settings.mineru.cleanup_local_outputs and job_root.exists():
                shutil.rmtree(job_root, ignore_errors=True)
            elif self.settings.mineru.cleanup_local_inputs:
                local_input_path.unlink(missing_ok=True)
        return response

    def _parse_document_sync(
        self,
        payload: ParseMineruRequest,
        input_path: Path,
        convert_dir: Path,
        output_dir: Path,
    ) -> list[dict]:
        parser = MineruParser()
        parse_input_path, content_kind = self._prepare_parse_input_sync(
            parser=parser,
            input_path=input_path,
            convert_dir=convert_dir,
            content_kind=payload.content_kind,
        )

        effective_method = "ocr" if content_kind == "image" else payload.parse_method
        output_dir.mkdir(parents=True, exist_ok=True)

        parser._run_mineru_command(
            input_path=parse_input_path,
            output_dir=output_dir,
            method=effective_method,
            lang=payload.lang,
            backend=payload.backend or self.settings.mineru.default_backend,
            source=payload.source or self.settings.mineru.source,
            device=payload.device or self.settings.mineru.default_device,
            start_page=payload.start_page,
            end_page=payload.end_page,
            formula=payload.enable_formula,
            table=payload.enable_table,
        )

        read_method = self._resolve_read_method(
            parse_method=effective_method,
            backend=payload.backend or self.settings.mineru.default_backend,
            content_kind=content_kind,
        )
        content_list, _ = parser._read_output_files(output_dir, input_path.stem, method=read_method)
        return content_list

    def _prepare_parse_input_sync(
        self,
        parser: MineruParser,
        input_path: Path,
        convert_dir: Path,
        content_kind: str,
    ) -> tuple[Path, str]:
        suffix = input_path.suffix.lower()
        if content_kind == "pdf" or suffix == ".pdf":
            return input_path, "pdf"

        if content_kind == "office" or suffix in {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".html", ".htm", ".xhtml"}:
            convert_dir.mkdir(parents=True, exist_ok=True)
            return parser.convert_office_to_pdf(input_path, convert_dir), "office"

        if suffix in {".txt", ".md"}:
            convert_dir.mkdir(parents=True, exist_ok=True)
            return parser.convert_text_to_pdf(input_path, convert_dir), "generic"

        if suffix in {".png", ".jpg", ".jpeg"}:
            return input_path, "image"

        if suffix in {".bmp", ".tiff", ".tif", ".gif", ".webp"}:
            convert_dir.mkdir(parents=True, exist_ok=True)
            return self._convert_image_to_png(input_path, convert_dir), "image"

        return input_path, content_kind

    @staticmethod
    def _convert_image_to_png(image_path: Path, convert_dir: Path) -> Path:
        from PIL import Image

        target_path = convert_dir / f"{image_path.stem}.png"
        with Image.open(image_path) as image:
            if image.mode in ("RGBA", "LA", "P"):
                if image.mode == "P":
                    image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "RGBA":
                    background.paste(image, mask=image.split()[-1])
                else:
                    background.paste(image)
                image = background
            elif image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(target_path, "PNG", optimize=True)
        return target_path

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
    def _extract_page_count(content_list: list[dict]) -> int | None:
        page_indexes = [
            int(item["page_idx"])
            for item in content_list
            if isinstance(item, dict) and isinstance(item.get("page_idx"), int)
        ]
        if not page_indexes:
            return None
        return max(page_indexes) + 1

    def health_status(self) -> dict[str, object]:
        mineru_ok = False
        mineru_error: str | None = None
        try:
            result = subprocess.run(
                ["mineru", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            mineru_ok = result.returncode == 0
            if not mineru_ok:
                mineru_error = result.stderr.strip() or result.stdout.strip() or "mineru --version failed"
        except Exception as exc:
            mineru_error = str(exc)

        minio_ok = False
        minio_error: str | None = None
        try:
            self.object_store.ensure_bucket_sync()
            minio_ok = True
        except Exception as exc:
            minio_error = str(exc)

        return {
            "status": "ok" if mineru_ok and minio_ok else "degraded",
            "mineru_available": mineru_ok,
            "mineru_error": mineru_error,
            "minio_available": minio_ok,
            "minio_error": minio_error,
            "checked_at": utc_now_iso(),
        }


def load_settings(config_path: str | Path) -> ParserServiceSettings:
    config_file = Path(config_path).resolve()
    raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    settings = ParserServiceSettings.model_validate(raw_config)
    if not settings.mineru.local_work_root.is_absolute():
        settings.mineru.local_work_root = (config_file.parent / settings.mineru.local_work_root).resolve()
    return settings


def create_app(settings: ParserServiceSettings) -> FastAPI:
    logging.basicConfig(
        level=getattr(logging, settings.server.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runner = MineruParseRunner(settings)
        await runner.initialize()
        app.state.runner = runner
        yield

    app = FastAPI(title="MinerU Parser Service", version="0.1.0", lifespan=lifespan)

    def require_runner(request: Request) -> MineruParseRunner:
        return request.app.state.runner

    def require_auth(authorization: str | None) -> None:
        if not settings.auth.enabled:
            return
        expected = settings.auth.resolve_api_key()
        if authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/health")
    async def health(request: Request):
        return require_runner(request).health_status()

    @app.post("/api/v1/parse/mineru", response_model=ParseMineruResponse)
    async def parse_mineru(
        request: Request,
        payload: ParseMineruRequest,
        authorization: str | None = Header(default=None),
    ):
        require_auth(authorization)
        try:
            return await require_runner(request).run_parse(payload)
        except MineruExecutionError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        except Exception as exc:
            logging.exception("Unhandled parser service error for %s", payload.request_id)
            raise HTTPException(status_code=500, detail=str(exc))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="MinerU GPU parser service")
    parser.add_argument("--config", default="config/mineru_parser_service.yaml", help="Path to parser service YAML configuration file.")
    args = parser.parse_args()

    settings = load_settings(args.config)
    app = create_app(settings)
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_level=settings.server.log_level.lower())


if __name__ == "__main__":
    main()
