from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Patch MinerU VLM client to skip model name validation against /v1/models,
# since some OpenAI-compatible servers use model aliases not listed in the models endpoint.
try:
    from mineru_vl_utils.vlm_client import http_client as _hc
    _hc.HttpVlmClient._check_model_name = lambda self, base_url, model_name: None
except Exception:
    pass
from typing import Any, Literal
from uuid import uuid4

import uvicorn
import yaml
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / 'static'
for dependency_dir in (ROOT_DIR / 'LightRAG', ROOT_DIR / 'RAG-Anything'):
    dependency_path = str(dependency_dir)
    if dependency_dir.exists() and dependency_path not in sys.path:
        sys.path.insert(0, dependency_path)

from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import EmbeddingFunc
from raganything import RAGAnything, RAGAnythingConfig
from app.object_storage import MinIOObjectStore
from app.preprocess import preprocess_large_document
from app.progress import ProgressTracker
from app.remote_mineru_parser import (
    RemoteMineruParserConfig,
    consume_remote_mineru_parse_result,
    configure_remote_mineru_parser,
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def generate_identifier(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex[:12]}'


class ServerSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    host: str = '0.0.0.0'
    port: int = 8080
    data_root: Path = Path('./data')
    log_level: str = 'INFO'


class RedisSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    uri: str = 'redis://redis:6379/0'
    max_connections: int = 200
    socket_timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 10.0
    retry_attempts: int = 3


class ObjectStorageSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: Literal['minio'] = 'minio'
    enabled: bool = False
    endpoint: str = 'minio:9000'
    bucket: str = 'multimodal-kb'
    prefix: str = 'knowledge_bases'
    access_key_env: str = 'MINIO_ACCESS_KEY_ID'
    secret_key_env: str = 'MINIO_SECRET_ACCESS_KEY'
    secure: bool = False
    region: str | None = None
    upload_inputs: bool = True
    upload_outputs: bool = True
    preserve_local_inputs: bool = False
    preserve_local_outputs: bool = False

    def resolve_access_key(self) -> str:
        access_key = os.getenv(self.access_key_env)
        if access_key:
            return access_key
        raise ValueError(f'Environment variable {self.access_key_env} is required for MinIO access.')

    def resolve_secret_key(self) -> str:
        secret_key = os.getenv(self.secret_key_env)
        if secret_key:
            return secret_key
        raise ValueError(f'Environment variable {self.secret_key_env} is required for MinIO access.')


class ParserServiceSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    provider: Literal['remote_mineru'] = 'remote_mineru'
    base_url: str = 'http://mineru-parser-service:8090'
    api_key_env: str = 'MINERU_PARSER_API_KEY'
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 1800.0
    default_backend: str = 'pipeline'
    default_source: str = 'local'
    default_device: str | None = None
    healthcheck_enabled: bool = True

    def resolve_api_key(self) -> str:
        api_key = os.getenv(self.api_key_env)
        if api_key:
            return api_key
        raise ValueError(f'Environment variable {self.api_key_env} is required for remote parser access.')


class OpenAICompatibleModelSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: Literal['openai_compatible'] = 'openai_compatible'
    enabled: bool = True
    model: str
    base_url: str
    api_key_env: str
    api_key_optional: bool = False
    placeholder_api_key: str = "EMPTY"
    timeout_seconds: int = 180
    client_configs: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    def resolve_api_key(self) -> str:
        api_key = os.getenv(self.api_key_env)
        if api_key:
            return api_key
        if self.api_key_optional:
            return self.placeholder_api_key
        raise ValueError(f'Environment variable {self.api_key_env} is required for model {self.model}.')


class EmbeddingSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: Literal['openai_compatible'] = 'openai_compatible'
    enabled: bool = True
    model: str
    base_url: str
    api_key_env: str | None = None
    api_key_optional: bool = False
    placeholder_api_key: str = "EMPTY"
    dimension: int
    max_token_size: int = 8192
    timeout_seconds: int = 120
    client_configs: dict[str, Any] = Field(default_factory=dict)

    def resolve_api_key(self) -> str:
        api_key = os.getenv(self.api_key_env) if self.api_key_env else None
        if api_key:
            return api_key
        if self.api_key_optional:
            return self.placeholder_api_key
        if self.api_key_env:
            raise ValueError(f"Environment variable {self.api_key_env} is required for embedding model {self.model}.")
        raise ValueError(f"An API key or placeholder is required for embedding model {self.model}.")


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    llm: OpenAICompatibleModelSettings
    vision: OpenAICompatibleModelSettings | None = None
    embedding: EmbeddingSettings


class RAGAnythingSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    parser: str = 'mineru'
    parse_method: str = 'auto'
    parser_output_dir: str = 'output'
    display_content_stats: bool = True
    enable_image_processing: bool = True
    enable_table_processing: bool = True
    enable_equation_processing: bool = True
    max_concurrent_files: int = 1
    context_window: int = 1
    context_mode: str = 'page'
    max_context_tokens: int = 2000
    include_headers: bool = True
    include_captions: bool = True
    context_filter_content_types: list[str] = Field(default_factory=lambda: ['text'])
    content_format: str = 'minerU'
    use_full_path: bool = False


class LightRAGSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    init_kwargs: dict[str, Any] = Field(default_factory=dict)
    query_defaults: dict[str, Any] = Field(default_factory=lambda: {'mode': 'hybrid', 'top_k': 20, 'chunk_top_k': 10, 'vlm_enhanced': True})


class AppSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')

    server: ServerSettings = Field(default_factory=ServerSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    object_storage: ObjectStorageSettings = Field(default_factory=ObjectStorageSettings)
    parser_service: ParserServiceSettings = Field(default_factory=ParserServiceSettings)
    models: ModelSettings
    rag_anything: RAGAnythingSettings = Field(default_factory=RAGAnythingSettings)
    light_rag: LightRAGSettings = Field(default_factory=LightRAGSettings)

    @model_validator(mode='after')
    def validate_multimodal_requirements(self) -> 'AppSettings':
        if self.rag_anything.enable_image_processing:
            if self.models.vision is None or not self.models.vision.enabled:
                raise ValueError('Vision model configuration is required when enable_image_processing is true.')
        if self.parser_service.enabled:
            if not self.object_storage.enabled:
                raise ValueError('object_storage must be enabled when parser_service is enabled.')
            if not self.object_storage.upload_inputs:
                raise ValueError('object_storage.upload_inputs must be enabled when parser_service is enabled.')
            if not self.object_storage.upload_outputs:
                raise ValueError('object_storage.upload_outputs must be enabled when parser_service is enabled.')
        return self


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    original_filename: str
    stored_filename: str
    content_type: str | None = None
    size_bytes: int
    status: Literal['queued', 'processing', 'completed', 'failed'] = 'queued'
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    doc_id: str | None = None
    error_message: str | None = None
    input_object_key: str | None = None
    output_object_prefix: str | None = None
    parse_backend: str | None = None
    parse_job_id: str | None = None
    parse_started_at: str | None = None
    parse_finished_at: str | None = None
    parse_error_message: str | None = None


class KnowledgeBaseRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    name: str
    description: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    documents: list[DocumentRecord] = Field(default_factory=list)
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class CreateKnowledgeBaseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    description: str | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator('name', mode='after')
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError('name cannot be empty')
        return normalized


class UploadDocumentsResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    knowledge_base_id: str
    accepted_count: int
    documents: list[DocumentRecord]


class DeleteKnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    knowledge_base_id: str
    deleted: bool
    data_purged: bool


class QueryMultimodalItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['image', 'table', 'equation']
    image_base64: str | None = None
    mime_type: str = 'image/png'
    table_data: str | None = None
    table_caption: str | None = None
    latex: str | None = None
    equation_caption: str | None = None

    @model_validator(mode='after')
    def validate_payload(self) -> 'QueryMultimodalItem':
        if self.type == 'image' and not self.image_base64:
            raise ValueError('image_base64 is required for image content')
        if self.type == 'table' and not self.table_data:
            raise ValueError('table_data is required for table content')
        if self.type == 'equation' and not self.latex:
            raise ValueError('latex is required for equation content')
        return self


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    query: str
    mode: str | None = None
    system_prompt: str | None = None
    query_options: dict[str, Any] = Field(default_factory=dict)
    multimodal_content: list[QueryMultimodalItem] = Field(default_factory=list)

    @field_validator('query', mode='after')
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError('query must be at least 3 characters long')
        return normalized


class QueryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    knowledge_base_id: str
    mode: str
    answer: str


class SystemSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    service_name: str
    service_version: str
    llm_model: str
    vision_model: str | None = None
    embedding_model: str
    vector_storage: str | None = None
    graph_storage: str | None = None
    default_query_mode: str
    image_processing_enabled: bool
    table_processing_enabled: bool
    equation_processing_enabled: bool


class KnowledgeBaseNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class KnowledgeBasePaths:
    root: Path
    input_dir: Path
    output_dir: Path
    working_dir: Path
    temp_dir: Path


class MetadataStore:
    def __init__(self, data_root: Path):
        self.file_path = data_root / 'metadata' / 'knowledge_bases.json'
        self._lock = asyncio.Lock()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_records(self) -> list[KnowledgeBaseRecord]:
        if not self.file_path.exists():
            return []
        raw_payload = json.loads(self.file_path.read_text(encoding='utf-8'))
        return [KnowledgeBaseRecord.model_validate(item) for item in raw_payload.get('knowledge_bases', [])]

    def _write_records(self, records: list[KnowledgeBaseRecord]) -> None:
        payload = {'knowledge_bases': [record.model_dump(mode='json') for record in records]}
        self.file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    async def list_knowledge_bases(self) -> list[KnowledgeBaseRecord]:
        async with self._lock:
            return self._read_records()

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseRecord | None:
        async with self._lock:
            for record in self._read_records():
                if record.id == knowledge_base_id:
                    return record
        return None

    async def save_knowledge_base(self, record: KnowledgeBaseRecord) -> KnowledgeBaseRecord:
        async with self._lock:
            records = self._read_records()
            replaced = False
            for index, existing in enumerate(records):
                if existing.id == record.id:
                    records[index] = record
                    replaced = True
                    break
            if not replaced:
                records.append(record)
            self._write_records(records)
            return record

    async def delete_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseRecord | None:
        async with self._lock:
            records = self._read_records()
            remaining: list[KnowledgeBaseRecord] = []
            deleted: KnowledgeBaseRecord | None = None
            for record in records:
                if record.id == knowledge_base_id:
                    deleted = record
                else:
                    remaining.append(record)
            if deleted is not None:
                self._write_records(remaining)
            return deleted

    async def list_documents(self, knowledge_base_id: str) -> list[DocumentRecord]:
        async with self._lock:
            for record in self._read_records():
                if record.id == knowledge_base_id:
                    return record.documents
        raise KnowledgeBaseNotFoundError(knowledge_base_id)

    async def append_documents(self, knowledge_base_id: str, documents: list[DocumentRecord]) -> list[DocumentRecord]:
        async with self._lock:
            records = self._read_records()
            for record in records:
                if record.id == knowledge_base_id:
                    record.documents.extend(documents)
                    record.updated_at = utc_now_iso()
                    self._write_records(records)
                    return documents
        raise KnowledgeBaseNotFoundError(knowledge_base_id)

    async def update_document(self, knowledge_base_id: str, document_id: str, **changes: Any) -> DocumentRecord:
        async with self._lock:
            records = self._read_records()
            for record in records:
                if record.id != knowledge_base_id:
                    continue
                for index, document in enumerate(record.documents):
                    if document.id == document_id:
                        updated = document.model_copy(update={**changes, 'updated_at': utc_now_iso()})
                        record.documents[index] = updated
                        record.updated_at = utc_now_iso()
                        self._write_records(records)
                        return updated
                raise ValueError(f'Document {document_id} was not found.')
        raise KnowledgeBaseNotFoundError(knowledge_base_id)


class EngineRegistry:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._instances: dict[str, RAGAnything] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._build_lock = asyncio.Lock()

    def get_lock(self, knowledge_base_id: str) -> asyncio.Lock:
        if knowledge_base_id not in self._locks:
            self._locks[knowledge_base_id] = asyncio.Lock()
        return self._locks[knowledge_base_id]

    async def get_engine(self, knowledge_base: KnowledgeBaseRecord, paths: KnowledgeBasePaths) -> RAGAnything:
        if knowledge_base.id in self._instances:
            return self._instances[knowledge_base.id]
        async with self._build_lock:
            if knowledge_base.id in self._instances:
                return self._instances[knowledge_base.id]
            engine = await self._build_engine(knowledge_base, paths)
            self._instances[knowledge_base.id] = engine
            return engine

    async def close_engine(self, knowledge_base_id: str) -> None:
        engine = self._instances.pop(knowledge_base_id, None)
        self._locks.pop(knowledge_base_id, None)
        if engine is not None:
            await engine.finalize_storages()

    async def close_all(self) -> None:
        for knowledge_base_id in list(self._instances.keys()):
            await self.close_engine(knowledge_base_id)

    async def _build_engine(self, knowledge_base: KnowledgeBaseRecord, paths: KnowledgeBasePaths) -> RAGAnything:
        rag_overrides = knowledge_base.config_overrides.get('rag_anything', {})
        light_rag_overrides = knowledge_base.config_overrides.get('light_rag_init_kwargs', {})

        rag_config = RAGAnythingConfig(
            working_dir=str(paths.working_dir),
            parser_output_dir=str(paths.output_dir),
            parser=rag_overrides.get('parser', self.settings.rag_anything.parser),
            parse_method=rag_overrides.get('parse_method', self.settings.rag_anything.parse_method),
            display_content_stats=rag_overrides.get('display_content_stats', self.settings.rag_anything.display_content_stats),
            enable_image_processing=rag_overrides.get('enable_image_processing', self.settings.rag_anything.enable_image_processing),
            enable_table_processing=rag_overrides.get('enable_table_processing', self.settings.rag_anything.enable_table_processing),
            enable_equation_processing=rag_overrides.get('enable_equation_processing', self.settings.rag_anything.enable_equation_processing),
            max_concurrent_files=rag_overrides.get('max_concurrent_files', self.settings.rag_anything.max_concurrent_files),
            context_window=rag_overrides.get('context_window', self.settings.rag_anything.context_window),
            context_mode=rag_overrides.get('context_mode', self.settings.rag_anything.context_mode),
            max_context_tokens=rag_overrides.get('max_context_tokens', self.settings.rag_anything.max_context_tokens),
            include_headers=rag_overrides.get('include_headers', self.settings.rag_anything.include_headers),
            include_captions=rag_overrides.get('include_captions', self.settings.rag_anything.include_captions),
            context_filter_content_types=rag_overrides.get('context_filter_content_types', self.settings.rag_anything.context_filter_content_types),
            content_format=rag_overrides.get('content_format', self.settings.rag_anything.content_format),
            use_full_path=rag_overrides.get('use_full_path', self.settings.rag_anything.use_full_path),
        )

        llm_model_func = self._build_llm_model_func(self.settings.models.llm)
        vision_model_func = self._build_vision_model_func(self.settings.models.vision, llm_model_func)
        embedding_func = self._build_embedding_func(self.settings.models.embedding)

        light_rag_kwargs = dict(self.settings.light_rag.init_kwargs)
        light_rag_kwargs.update(light_rag_overrides)
        light_rag_kwargs['workspace'] = knowledge_base.id
        self._prepare_storage_environment(light_rag_kwargs)

        engine = RAGAnything(
            config=rag_config,
            llm_model_func=llm_model_func,
            vision_model_func=vision_model_func,
            embedding_func=embedding_func,
            lightrag_kwargs=light_rag_kwargs,
        )

        status = await engine._ensure_lightrag_initialized()
        if not status.get('success', False):
            raise RuntimeError(status.get('error', 'Failed to initialize RAG engine.'))

        return engine

    def _prepare_storage_environment(self, light_rag_kwargs: dict[str, Any]) -> None:
        storage_names = {
            str(light_rag_kwargs.get('kv_storage', '')),
            str(light_rag_kwargs.get('doc_status_storage', '')),
        }
        uses_redis = any(name.startswith('Redis') for name in storage_names if name)
        if not uses_redis:
            return

        if self.settings.redis.enabled:
            os.environ['REDIS_URI'] = self.settings.redis.uri
            os.environ['REDIS_MAX_CONNECTIONS'] = str(self.settings.redis.max_connections)
            os.environ['REDIS_SOCKET_TIMEOUT'] = str(self.settings.redis.socket_timeout_seconds)
            os.environ['REDIS_CONNECT_TIMEOUT'] = str(self.settings.redis.connect_timeout_seconds)
            os.environ['REDIS_RETRY_ATTEMPTS'] = str(self.settings.redis.retry_attempts)
            return

        if not os.getenv('REDIS_URI'):
            raise ValueError('REDIS_URI must be configured when using Redis storage backends.')

    def _build_llm_model_func(self, settings: OpenAICompatibleModelSettings):
        api_key = settings.resolve_api_key()
        client_configs = dict(settings.client_configs)
        client_configs.setdefault('timeout', settings.timeout_seconds)

        async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            merged_kwargs = dict(settings.extra_body)
            merged_kwargs.update(kwargs)
            return await openai_complete_if_cache(
                settings.model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=api_key,
                base_url=settings.base_url,
                timeout=settings.timeout_seconds,
                openai_client_configs=client_configs,
                **merged_kwargs,
            )

        return llm_model_func

    def _build_vision_model_func(self, settings: OpenAICompatibleModelSettings | None, fallback_llm_model_func):
        if settings is None or not settings.enabled:
            return None

        api_key = settings.resolve_api_key()
        client_configs = dict(settings.client_configs)
        client_configs.setdefault('timeout', settings.timeout_seconds)

        async def vision_model_func(prompt, system_prompt=None, history_messages=None, image_data=None, messages=None, **kwargs):
            merged_kwargs = dict(settings.extra_body)
            merged_kwargs.update(kwargs)
            if messages is not None:
                return await openai_complete_if_cache(
                    settings.model,
                    '',
                    api_key=api_key,
                    base_url=settings.base_url,
                    timeout=settings.timeout_seconds,
                    openai_client_configs=client_configs,
                    messages=messages,
                    **merged_kwargs,
                )
            if image_data:
                message_list = []
                if system_prompt:
                    message_list.append({'role': 'system', 'content': system_prompt})
                message_list.append({'role': 'user', 'content': [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{image_data}'}}]})
                return await openai_complete_if_cache(
                    settings.model,
                    '',
                    api_key=api_key,
                    base_url=settings.base_url,
                    timeout=settings.timeout_seconds,
                    openai_client_configs=client_configs,
                    messages=message_list,
                    **merged_kwargs,
                )
            return await fallback_llm_model_func(prompt, system_prompt=system_prompt, history_messages=history_messages or [], **kwargs)

        return vision_model_func

    def _build_embedding_func(self, settings: EmbeddingSettings) -> EmbeddingFunc:
        api_key = settings.resolve_api_key()
        client_configs = dict(settings.client_configs)
        client_configs.setdefault('timeout', settings.timeout_seconds)

        async def embed_texts(texts: list[str]):
            return await openai_embed.func(
                texts,
                model=settings.model,
                base_url=settings.base_url,
                api_key=api_key,
                client_configs=client_configs,
            )

        return EmbeddingFunc(embedding_dim=settings.dimension, max_token_size=settings.max_token_size, func=embed_texts)


class KnowledgeBaseService:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.store = MetadataStore(settings.server.data_root)
        self.engines = EngineRegistry(settings)
        self._background_tasks: set[asyncio.Task] = set()
        self.object_store = self._build_object_store()

    def _build_object_store(self) -> MinIOObjectStore | None:
        if not self.settings.object_storage.enabled:
            return None
        return MinIOObjectStore(
            endpoint=self.settings.object_storage.endpoint,
            access_key=self.settings.object_storage.resolve_access_key(),
            secret_key=self.settings.object_storage.resolve_secret_key(),
            bucket_name=self.settings.object_storage.bucket,
            secure=self.settings.object_storage.secure,
            region=self.settings.object_storage.region,
        )

    async def initialize(self) -> None:
        if self.object_store is not None:
            await self.object_store.ensure_bucket()
        if self.settings.parser_service.enabled:
            if self.object_store is None:
                raise ValueError('Object storage is required when parser_service is enabled.')
            configure_remote_mineru_parser(
                RemoteMineruParserConfig(
                    base_url=self.settings.parser_service.base_url,
                    api_key=self.settings.parser_service.resolve_api_key(),
                    connect_timeout_seconds=self.settings.parser_service.connect_timeout_seconds,
                    read_timeout_seconds=self.settings.parser_service.read_timeout_seconds,
                    default_backend=self.settings.parser_service.default_backend,
                    default_source=self.settings.parser_service.default_source,
                    default_device=self.settings.parser_service.default_device,
                ),
                self.object_store,
            )

    def get_paths(self, knowledge_base_id: str) -> KnowledgeBasePaths:
        root = self.settings.server.data_root / 'knowledge_bases' / knowledge_base_id
        return KnowledgeBasePaths(
            root=root,
            input_dir=root / 'inputs',
            output_dir=root / 'output',
            working_dir=root / 'rag_storage',
            temp_dir=root / 'tmp',
        )

    def ensure_directories(self, paths: KnowledgeBasePaths) -> None:
        for directory in (paths.root, paths.input_dir, paths.output_dir, paths.working_dir, paths.temp_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _track_background_task(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def sanitize_filename(self, filename: str) -> str:
        normalized = Path(filename or 'upload.bin').name.strip()
        normalized = normalized.replace('/', '_').replace(chr(92), '_')
        if normalized in {'', '.', '..'}:
            raise ValueError('Invalid filename.')
        return normalized

    def resolve_rag_override(self, knowledge_base: KnowledgeBaseRecord, key: str, default: Any) -> Any:
        return knowledge_base.config_overrides.get('rag_anything', {}).get(key, default)

    def resolve_rag_parser(self, knowledge_base: KnowledgeBaseRecord) -> str:
        return str(self.resolve_rag_override(knowledge_base, 'parser', self.settings.rag_anything.parser))

    def _build_object_key(self, *parts: str) -> str:
        return MinIOObjectStore.normalize_key(*parts)

    def build_knowledge_base_object_prefix(self, knowledge_base_id: str) -> str:
        return self._build_object_key(self.settings.object_storage.prefix, knowledge_base_id)

    def build_input_object_key(self, knowledge_base_id: str, stored_filename: str) -> str:
        return self._build_object_key(self.build_knowledge_base_object_prefix(knowledge_base_id), 'inputs', stored_filename)

    def get_parse_output_dir(self, input_path: Path, output_root: Path) -> Path:
        path_hash = hashlib.md5(str(input_path.resolve()).encode('utf-8')).hexdigest()[:8]
        return output_root / f'{input_path.stem}_{path_hash}'

    def build_output_object_prefix(self, knowledge_base_id: str, input_path: Path, output_root: Path) -> str:
        return self._build_object_key(
            self.build_knowledge_base_object_prefix(knowledge_base_id),
            'output',
            self.get_parse_output_dir(input_path, output_root).name,
        )

    async def _upload_document_input(
        self,
        knowledge_base_id: str,
        paths: KnowledgeBasePaths,
        document: DocumentRecord,
    ) -> DocumentRecord:
        if self.object_store is None or not self.settings.object_storage.upload_inputs:
            return document
        object_key = document.input_object_key or self.build_input_object_key(knowledge_base_id, document.stored_filename)
        await self.object_store.upload_file(
            object_key,
            paths.input_dir / document.stored_filename,
            document.content_type,
        )
        updates: dict[str, Any] = {'input_object_key': object_key}
        if self.settings.object_storage.upload_outputs:
            updates['output_object_prefix'] = (
                document.output_object_prefix
                or self.build_output_object_prefix(
                    knowledge_base_id,
                    paths.input_dir / document.stored_filename,
                    paths.output_dir,
                )
            )
        return document.model_copy(update=updates)

    async def _ensure_document_input_local(
        self,
        knowledge_base_id: str,
        paths: KnowledgeBasePaths,
        document: DocumentRecord,
    ) -> Path:
        input_path = paths.input_dir / document.stored_filename
        if input_path.exists():
            return input_path

        if self.object_store is None:
            raise FileNotFoundError(f'Input file not found: {input_path}')

        object_key = document.input_object_key or self.build_input_object_key(knowledge_base_id, document.stored_filename)
        downloaded = await self.object_store.download_file(object_key, input_path)
        if not downloaded:
            raise FileNotFoundError(f'Input file missing locally and in object storage: {object_key}')
        return input_path

    async def _ensure_document_output_local(
        self,
        knowledge_base_id: str,
        paths: KnowledgeBasePaths,
        document: DocumentRecord,
        input_path: Path,
    ) -> Path:
        output_dir = self.get_parse_output_dir(input_path, paths.output_dir)
        if output_dir.exists() or self.object_store is None or not self.settings.object_storage.upload_outputs:
            return output_dir

        object_prefix = document.output_object_prefix or self.build_output_object_prefix(knowledge_base_id, input_path, paths.output_dir)
        await self.object_store.download_prefix(object_prefix, output_dir)
        return output_dir

    async def _archive_document_output(
        self,
        knowledge_base_id: str,
        paths: KnowledgeBasePaths,
        document: DocumentRecord,
        input_path: Path,
    ) -> str | None:
        if self.object_store is None or not self.settings.object_storage.upload_outputs:
            return document.output_object_prefix

        output_dir = self.get_parse_output_dir(input_path, paths.output_dir)
        if not output_dir.exists():
            return document.output_object_prefix

        object_prefix = document.output_object_prefix or self.build_output_object_prefix(knowledge_base_id, input_path, paths.output_dir)
        await self.object_store.upload_directory(object_prefix, output_dir)
        return object_prefix

    def _cleanup_local_document_artifacts(
        self,
        paths: KnowledgeBasePaths,
        document: DocumentRecord,
        input_path: Path | None,
    ) -> None:
        if self.object_store is None:
            return

        if input_path is not None and document.input_object_key and not self.settings.object_storage.preserve_local_inputs:
            input_path.unlink(missing_ok=True)

        if document.output_object_prefix and not self.settings.object_storage.preserve_local_outputs:
            output_dir = self.get_parse_output_dir(paths.input_dir / document.stored_filename, paths.output_dir)
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)

    async def _ensure_query_outputs_local(
        self,
        knowledge_base: KnowledgeBaseRecord,
        paths: KnowledgeBasePaths,
    ) -> None:
        if self.object_store is None or not self.settings.object_storage.upload_outputs:
            return

        for document in knowledge_base.documents:
            if document.status != 'completed' or not document.output_object_prefix:
                continue
            await self._ensure_document_output_local(
                knowledge_base.id,
                paths,
                document,
                paths.input_dir / document.stored_filename,
            )

    async def require_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseRecord:
        knowledge_base = await self.store.get_knowledge_base(knowledge_base_id)
        if knowledge_base is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        return knowledge_base

    async def list_knowledge_bases(self) -> list[KnowledgeBaseRecord]:
        return await self.store.list_knowledge_bases()

    async def create_knowledge_base(self, request: CreateKnowledgeBaseRequest) -> KnowledgeBaseRecord:
        knowledge_base = KnowledgeBaseRecord(
            id=generate_identifier('kb'),
            name=request.name,
            description=request.description,
            config_overrides=request.config_overrides,
        )
        self.ensure_directories(self.get_paths(knowledge_base.id))
        return await self.store.save_knowledge_base(knowledge_base)

    async def delete_knowledge_base(self, knowledge_base_id: str, purge_data: bool) -> DeleteKnowledgeBaseResponse:
        await self.require_knowledge_base(knowledge_base_id)
        paths = self.get_paths(knowledge_base_id)
        await self.engines.close_engine(knowledge_base_id)
        if purge_data and self.object_store is not None:
            await self.object_store.delete_prefix(self.build_knowledge_base_object_prefix(knowledge_base_id))
        deleted = await self.store.delete_knowledge_base(knowledge_base_id)
        if deleted is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        if purge_data and paths.root.exists():
            shutil.rmtree(paths.root)
        return DeleteKnowledgeBaseResponse(knowledge_base_id=knowledge_base_id, deleted=True, data_purged=purge_data)

    async def list_documents(self, knowledge_base_id: str) -> list[DocumentRecord]:
        return await self.store.list_documents(knowledge_base_id)

    async def upload_documents(self, knowledge_base_id: str, files: list[UploadFile]) -> UploadDocumentsResponse:
        knowledge_base = await self.require_knowledge_base(knowledge_base_id)
        paths = self.get_paths(knowledge_base.id)
        self.ensure_directories(paths)

        documents: list[DocumentRecord] = []
        for upload in files:
            safe_name = self.sanitize_filename(upload.filename or 'upload.bin')
            document_id = generate_identifier('doc')
            stored_filename = f'{document_id}_{safe_name}'
            target_path = paths.input_dir / stored_filename
            size_bytes = 0

            with target_path.open('wb') as output_file:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    output_file.write(chunk)

            await upload.close()

            # Preprocess large documents (split PDFs exceeding 200 pages)
            try:
                needs_split, file_paths = preprocess_large_document(
                    target_path,
                    paths.input_dir
                )
                if needs_split:
                    # Create separate document records for each chunk
                    logging.info(f"Large document split into {len(file_paths)} chunks")
                    for i, chunk_path in enumerate(file_paths):
                        chunk_doc_id = generate_identifier('doc')
                        chunk_filename = f"{chunk_doc_id}_{safe_name}_chunk_{i+1}.pdf"
                        chunk_stored_path = paths.input_dir / chunk_filename

                        # Move chunk to standard location
                        shutil.move(str(chunk_path), str(chunk_stored_path))

                        documents.append(DocumentRecord(
                            id=chunk_doc_id,
                            original_filename=f"{safe_name} (pages {i*50+1} to {(i+1)*50})",
                            stored_filename=chunk_filename,
                            content_type='application/pdf',
                            size_bytes=chunk_stored_path.stat().st_size
                        ))
                    # Remove original large file
                    target_path.unlink()
                    logging.info(f"Large document split complete, original removed")
                else:
                    # No split needed, use original file
                    documents.append(DocumentRecord(
                        id=document_id,
                        original_filename=safe_name,
                        stored_filename=stored_filename,
                        content_type=upload.content_type,
                        size_bytes=size_bytes
                    ))
            except Exception as e:
                logging.warning(f"Preprocessing failed, fallback to original file: {e}")
                documents.append(DocumentRecord(
                    id=document_id,
                    original_filename=safe_name,
                    stored_filename=stored_filename,
                    content_type=upload.content_type,
                    size_bytes=size_bytes
                ))

        if self.object_store is not None and self.settings.object_storage.upload_inputs:
            documents = [
                await self._upload_document_input(knowledge_base_id, paths, document)
                for document in documents
            ]

        await self.store.append_documents(knowledge_base_id, documents)
        task = asyncio.create_task(self._ingest_documents(knowledge_base_id, [document.id for document in documents]))
        self._track_background_task(task)
        return UploadDocumentsResponse(knowledge_base_id=knowledge_base_id, accepted_count=len(documents), documents=documents)

    async def _ingest_documents(self, knowledge_base_id: str, document_ids: list[str]) -> None:
        knowledge_base = await self.require_knowledge_base(knowledge_base_id)
        paths = self.get_paths(knowledge_base_id)
        lock = self.engines.get_lock(knowledge_base_id)

        async with lock:
            try:
                engine = await self.engines.get_engine(knowledge_base, paths)
            except Exception as exc:
                logging.exception('Failed to build RAG engine for %s', knowledge_base_id)
                for document_id in document_ids:
                    await self.store.update_document(knowledge_base_id, document_id, status='failed', error_message=str(exc))
                return

            for document_id in document_ids:
                refreshed_knowledge_base = await self.require_knowledge_base(knowledge_base_id)
                document_map = {document.id: document for document in refreshed_knowledge_base.documents}
                document = document_map.get(document_id)
                if document is None:
                    continue

                parser_name = self.resolve_rag_parser(refreshed_knowledge_base)
                document = await self.store.update_document(
                    knowledge_base_id,
                    document_id,
                    status='processing',
                    error_message=None,
                    parse_backend=parser_name,
                    parse_started_at=utc_now_iso(),
                    parse_finished_at=None,
                    parse_error_message=None,
                )
                input_path: Path | None = None
                parse_result = None
                try:
                    if parser_name == 'remote_mineru' and self.object_store is not None and not document.input_object_key:
                        document = await self._upload_document_input(knowledge_base_id, paths, document)
                        document = await self.store.update_document(
                            knowledge_base_id,
                            document_id,
                            input_object_key=document.input_object_key,
                            output_object_prefix=document.output_object_prefix,
                        )

                    input_path = await self._ensure_document_input_local(knowledge_base_id, paths, document)
                    await self._ensure_document_output_local(knowledge_base_id, paths, document, input_path)

                    parser_kwargs: dict[str, Any] = {}
                    backend_value = 'pipeline'
                    if parser_name == 'remote_mineru':
                        output_object_prefix = document.output_object_prefix or self.build_output_object_prefix(
                            knowledge_base_id,
                            input_path,
                            paths.output_dir,
                        )
                        if output_object_prefix != document.output_object_prefix:
                            document = await self.store.update_document(
                                knowledge_base_id,
                                document_id,
                                output_object_prefix=output_object_prefix,
                            )
                        backend_value = self.settings.parser_service.default_backend
                        parser_kwargs.update({
                            'request_id': document.id,
                            'document_id': document.id,
                            'knowledge_base_id': knowledge_base_id,
                            'input_object_key': document.input_object_key,
                            'output_object_prefix': document.output_object_prefix,
                            'source': self.settings.parser_service.default_source,
                            'device': self.settings.parser_service.default_device,
                            'parser_file_name': document.original_filename,
                        })

                    await engine.process_document_complete(
                        file_path=str(input_path),
                        output_dir=str(paths.output_dir),
                        parse_method=self.resolve_rag_override(refreshed_knowledge_base, 'parse_method', self.settings.rag_anything.parse_method),
                        display_stats=self.resolve_rag_override(refreshed_knowledge_base, 'display_content_stats', self.settings.rag_anything.display_content_stats),
                        doc_id=document.id,
                        file_name=document.original_filename,
                        backend=backend_value,
                        **parser_kwargs,
                    )
                    if parser_name == 'remote_mineru':
                        parse_result = consume_remote_mineru_parse_result(document.id)

                    output_object_prefix = document.output_object_prefix
                    if parse_result is not None and parse_result.output_object_prefix:
                        output_object_prefix = parse_result.output_object_prefix
                    elif input_path is not None:
                        try:
                            output_object_prefix = await self._archive_document_output(knowledge_base_id, paths, document, input_path)
                        except Exception:
                            logging.exception('Failed to archive document output for %s', document_id)

                    document = document.model_copy(
                        update={
                            'output_object_prefix': output_object_prefix,
                            'parse_job_id': parse_result.job_id if parse_result is not None else document.parse_job_id,
                            'parse_error_message': parse_result.error_message if parse_result is not None else None,
                        }
                    )
                    await self.store.update_document(
                        knowledge_base_id,
                        document_id,
                        status='completed',
                        doc_id=document.id,
                        error_message=None,
                        output_object_prefix=output_object_prefix,
                        parse_backend=parser_name,
                        parse_job_id=document.parse_job_id,
                        parse_finished_at=utc_now_iso(),
                        parse_error_message=document.parse_error_message,
                    )
                except Exception as exc:
                    logging.exception('Document ingestion failed for %s', document_id)
                    if parser_name == 'remote_mineru':
                        parse_result = consume_remote_mineru_parse_result(document.id)
                    output_object_prefix = document.output_object_prefix
                    if parse_result is not None and parse_result.output_object_prefix:
                        output_object_prefix = parse_result.output_object_prefix
                    elif input_path is not None:
                        try:
                            output_object_prefix = await self._archive_document_output(knowledge_base_id, paths, document, input_path)
                        except Exception:
                            logging.exception('Failed to archive partial document output for %s', document_id)
                    parse_error_message = str(exc)
                    if parse_result is not None and parse_result.error_message:
                        parse_error_message = parse_result.error_message
                    document = document.model_copy(
                        update={
                            'output_object_prefix': output_object_prefix,
                            'parse_job_id': parse_result.job_id if parse_result is not None else document.parse_job_id,
                            'parse_error_message': parse_error_message,
                        }
                    )
                    await self.store.update_document(
                        knowledge_base_id,
                        document_id,
                        status='failed',
                        error_message=str(exc),
                        output_object_prefix=output_object_prefix,
                        parse_backend=parser_name,
                        parse_job_id=document.parse_job_id,
                        parse_finished_at=utc_now_iso(),
                        parse_error_message=document.parse_error_message,
                    )
                finally:
                    self._cleanup_local_document_artifacts(paths, document, input_path)

    def materialize_query_content(self, knowledge_base_id: str, items: list[QueryMultimodalItem]) -> tuple[list[dict[str, Any]], list[Path]]:
        if not items:
            return [], []

        paths = self.get_paths(knowledge_base_id)
        query_temp_dir = paths.temp_dir / 'query'
        query_temp_dir.mkdir(parents=True, exist_ok=True)

        payloads: list[dict[str, Any]] = []
        temp_files: list[Path] = []
        for item in items:
            if item.type == 'image':
                encoded = item.image_base64 or ''
                if encoded.startswith('data:') and ',' in encoded:
                    encoded = encoded.split(',', 1)[1]
                raw_bytes = base64.b64decode(encoded)
                suffix = mimetypes.guess_extension(item.mime_type) or '.png'
                query_file_name = generate_identifier('query') + suffix
                file_path = query_temp_dir / query_file_name
                file_path.write_bytes(raw_bytes)
                temp_files.append(file_path)
                payloads.append({'type': 'image', 'img_path': str(file_path)})
            elif item.type == 'table':
                payload = {'type': 'table', 'table_data': item.table_data or ''}
                if item.table_caption:
                    payload['table_caption'] = item.table_caption
                payloads.append(payload)
            elif item.type == 'equation':
                payload = {'type': 'equation', 'latex': item.latex or ''}
                if item.equation_caption:
                    payload['equation_caption'] = item.equation_caption
                payloads.append(payload)
        return payloads, temp_files

    async def query(self, knowledge_base_id: str, request: QueryRequest) -> QueryResponse:
        knowledge_base = await self.require_knowledge_base(knowledge_base_id)
        paths = self.get_paths(knowledge_base_id)
        query_kwargs = dict(self.settings.light_rag.query_defaults)
        query_kwargs.update(knowledge_base.config_overrides.get('query_defaults', {}))
        query_kwargs.update(request.query_options)
        mode = request.mode or query_kwargs.pop('mode', 'hybrid')
        lock = self.engines.get_lock(knowledge_base_id)
        payloads, temp_files = self.materialize_query_content(knowledge_base_id, request.multimodal_content)

        try:
            async with lock:
                await self._ensure_query_outputs_local(knowledge_base, paths)
                engine = await self.engines.get_engine(knowledge_base, paths)
                if payloads:
                    answer = await engine.aquery_with_multimodal(request.query, multimodal_content=payloads, mode=mode, system_prompt=request.system_prompt, **query_kwargs)
                else:
                    answer = await engine.aquery(request.query, mode=mode, system_prompt=request.system_prompt, **query_kwargs)
        finally:
            for file_path in temp_files:
                file_path.unlink(missing_ok=True)

        return QueryResponse(knowledge_base_id=knowledge_base_id, mode=mode, answer=answer)

    async def shutdown(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        await self.engines.close_all()


def load_settings(config_path: str | Path) -> AppSettings:
    config_file = Path(config_path).resolve()
    raw_config = yaml.safe_load(config_file.read_text(encoding='utf-8')) or {}
    settings = AppSettings.model_validate(raw_config)
    if not settings.server.data_root.is_absolute():
        settings.server.data_root = (config_file.parent / settings.server.data_root).resolve()
    return settings


def get_service(request: Request) -> KnowledgeBaseService:
    return request.app.state.service


def create_app(settings: AppSettings) -> FastAPI:
    logging.basicConfig(level=getattr(logging, settings.server.log_level.upper(), logging.INFO), format='%(asctime)s %(levelname)s %(message)s')

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = KnowledgeBaseService(settings)
        await service.initialize()
        app.state.service = service
        yield
        await service.shutdown()

    app = FastAPI(title='Multimodal Knowledge Base Manager', version='0.1.0', lifespan=lifespan)
    app.mount('/assets', StaticFiles(directory=STATIC_DIR), name='assets')

    @app.get('/', include_in_schema=False)
    async def frontend_index() -> FileResponse:
        return FileResponse(STATIC_DIR / 'index.html')

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/api/v1/system-summary', response_model=SystemSummaryResponse)
    async def system_summary() -> SystemSummaryResponse:
        return SystemSummaryResponse(
            service_name='Multimodal Knowledge Base Manager',
            service_version='0.1.0',
            llm_model=settings.models.llm.model,
            vision_model=settings.models.vision.model if settings.models.vision and settings.models.vision.enabled else None,
            embedding_model=settings.models.embedding.model,
            vector_storage=settings.light_rag.init_kwargs.get('vector_storage'),
            graph_storage=settings.light_rag.init_kwargs.get('graph_storage'),
            default_query_mode=str(settings.light_rag.query_defaults.get('mode', 'hybrid')),
            image_processing_enabled=settings.rag_anything.enable_image_processing,
            table_processing_enabled=settings.rag_anything.enable_table_processing,
            equation_processing_enabled=settings.rag_anything.enable_equation_processing,
        )

    @app.get('/api/v1/knowledge-bases', response_model=list[KnowledgeBaseRecord])
    async def list_knowledge_bases(request: Request):
        return await get_service(request).list_knowledge_bases()

    @app.post('/api/v1/knowledge-bases', response_model=KnowledgeBaseRecord, status_code=201)
    async def create_knowledge_base(request: Request, payload: CreateKnowledgeBaseRequest):
        return await get_service(request).create_knowledge_base(payload)

    @app.get('/api/v1/knowledge-bases/{knowledge_base_id}', response_model=KnowledgeBaseRecord)
    async def get_knowledge_base(request: Request, knowledge_base_id: str):
        try:
            return await get_service(request).require_knowledge_base(knowledge_base_id)
        except KnowledgeBaseNotFoundError:
            raise HTTPException(status_code=404, detail='Knowledge base not found.')

    @app.delete('/api/v1/knowledge-bases/{knowledge_base_id}', response_model=DeleteKnowledgeBaseResponse)
    async def delete_knowledge_base(request: Request, knowledge_base_id: str, purge_data: bool = True):
        try:
            return await get_service(request).delete_knowledge_base(knowledge_base_id, purge_data)
        except KnowledgeBaseNotFoundError:
            raise HTTPException(status_code=404, detail='Knowledge base not found.')

    @app.get('/api/v1/knowledge-bases/{knowledge_base_id}/documents', response_model=list[DocumentRecord])
    async def list_documents(request: Request, knowledge_base_id: str):
        try:
            return await get_service(request).list_documents(knowledge_base_id)
        except KnowledgeBaseNotFoundError:
            raise HTTPException(status_code=404, detail='Knowledge base not found.')

    @app.post('/api/v1/knowledge-bases/{knowledge_base_id}/documents/upload', response_model=UploadDocumentsResponse, status_code=202)
    async def upload_documents(request: Request, knowledge_base_id: str, files: list[UploadFile] = File(...)):
        try:
            return await get_service(request).upload_documents(knowledge_base_id, files)
        except KnowledgeBaseNotFoundError:
            raise HTTPException(status_code=404, detail='Knowledge base not found.')
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post('/api/v1/knowledge-bases/{knowledge_base_id}/query', response_model=QueryResponse)
    async def query_knowledge_base(request: Request, knowledge_base_id: str, payload: QueryRequest):
        try:
            return await get_service(request).query(knowledge_base_id, payload)
        except KnowledgeBaseNotFoundError:
            raise HTTPException(status_code=404, detail='Knowledge base not found.')
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logging.exception('Query failed for knowledge base %s', knowledge_base_id)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get('/api/v1/documents/{document_id}/progress')
    async def get_document_progress(document_id: str):
        """Get real-time processing progress for a document.

        Returns:
            Progress information including:
            - current_page: Current page being processed
            - total_pages: Total number of pages
            - progress_percent: Percentage completed
            - elapsed_seconds: Time elapsed so far
            - estimated_remaining_seconds: Estimated time to completion
        """
        progress = ProgressTracker.get_progress(document_id)
        if not progress:
            raise HTTPException(status_code=404, detail=f'Progress not found for document {document_id}.')
        return progress

    @app.get('/api/v1/progress')
    async def get_all_progress():
        """Get all active document processing progress.

        Returns:
            List of all active progress tracking
        """
        return ProgressTracker.get_all_progress()

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description='Multimodal knowledge-base manager')
    parser.add_argument('--config', default='config/config.yaml', help='Path to YAML configuration file.')
    args = parser.parse_args()

    settings = load_settings(args.config)
    app = create_app(settings)
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_level=settings.server.log_level.lower())


if __name__ == '__main__':
    main()
