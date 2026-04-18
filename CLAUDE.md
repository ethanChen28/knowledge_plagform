# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multimodal knowledge base management service built on LightRAG and RAG-Anything. Provides REST APIs for creating knowledge bases, uploading documents (PDFs, images, tables, equations), and querying with multimodal content.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                      │
│                     (app/main.py)                           │
│  - KnowledgeBaseService: Core business logic                │
│  - EngineRegistry: RAG engine lifecycle management          │
│  - MetadataStore: JSON-based metadata persistence           │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   RAG-Anything  │  │    LightRAG     │  │   Qdrant +      │
│  (Document      │  │  (Retrieval &   │  │   Neo4j         │
│   Parsing)      │  │   Query)        │  │  (Storage)      │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### Key Components

- **app/main.py**: Main application entry point with FastAPI endpoints, knowledge base management, and engine orchestration
- **LightRAG/**: Upstream graph-based RAG framework (see LightRAG/CLAUDE.md for details)
- **RAG-Anything/**: Upstream multimodal document parsing library

### Storage

- **Vector**: Qdrant (configured via `QDRANT_URL` env var)
- **Graph**: Neo4j (configured via `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`)
- **Metadata**: JSON file at `./data/metadata/knowledge_bases.json`
- **Knowledge base data**: `./data/knowledge_bases/{kb_id}/`

## Running the Service

### Docker Deployment (Recommended)

```bash
# 1. Configure models in config/config.yaml
# 2. Set required environment variables
export OPENAI_API_KEY=your_key
export EMBEDDING_API_KEY=your_key  # optional, defaults to "EMPTY"

# 3. Start all services
docker compose up --build -d

# Access points:
# - API: http://localhost:8080
# - API Docs: http://localhost:8080/docs
# - Qdrant Console: http://localhost:6333
# - Neo4j Browser: http://localhost:7474
```

### Local Development

```bash
# Install dependencies
pip install -e ./LightRAG[api,offline-storage]
pip install -e ./RAG-Anything[all]
pip install -e .

# Run locally
export OPENAI_API_KEY=your_key
export QDRANT_URL=http://localhost:6333
export NEO4J_URI=neo4j://localhost:7687
export NEO4J_USERNAME=neo4j
export NEO4J_PASSWORD=neo4jpassword

python -m app.main --config config/config.yaml
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| GET | /api/v1/knowledge-bases | List all knowledge bases |
| POST | /api/v1/knowledge-bases | Create knowledge base |
| GET | /api/v1/knowledge-bases/{id} | Get knowledge base details |
| DELETE | /api/v1/knowledge-bases/{id} | Delete knowledge base |
| GET | /api/v1/knowledge-bases/{id}/documents | List documents |
| POST | /api/v1/knowledge-bases/{id}/documents/upload | Upload documents (async) |
| POST | /api/v1/knowledge-bases/{id}/query | Query knowledge base |

## Configuration

Configuration is managed via `config/config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8080
  data_root: ./data
  log_level: INFO

models:
  llm:
    provider: openai_compatible
    model: Qwen3.5-397B
    base_url: http://192.168.11.18:30055/v1
    api_key_env: OPENAI_API_KEY
  vision:
    # For image processing
    model: Qwen3.5-397B
    base_url: http://192.168.11.18:30055/v1
  embedding:
    model: BAAI/bge-m3
    base_url: http://192.168.16.36:8081/v1
    dimension: 1024

rag_anything:
  parser: mineru
  enable_image_processing: true
  enable_table_processing: true
  enable_equation_processing: true

light_rag:
  init_kwargs:
    vector_storage: QdrantVectorDBStorage
    graph_storage: Neo4JStorage
  query_defaults:
    mode: hybrid
    top_k: 20
```

## Per-Knowledge-Base Configuration

When creating a knowledge base, you can override defaults:

```json
{
  "name": "my-kb",
  "config_overrides": {
    "rag_anything": {
      "parser": "mineru",
      "enable_table_processing": false
    },
    "light_rag_init_kwargs": {
      "cosine_better_than_threshold": 0.3
    },
    "query_defaults": {
      "mode": "local",
      "top_k": 30
    }
  }
}
```

## Key Implementation Details

### Document Upload Flow

1. Files uploaded to `{kb_id}/inputs/`
2. Parsed asynchronously via RAG-Anything
3. Parsed output stored in `{kb_id}/output/`
4. Indexed into LightRAG, stored in `{kb_id}/rag_storage/`

### Query Flow

1. Query request received with optional multimodal content
2. Multimodal content (images, tables, equations) materialized to temp files
3. Lock acquired for knowledge base (prevents concurrent modifications)
4. RAGAnything engine queried with hybrid/local/global/mix mode
5. Temp files cleaned up, lock released

### Engine Lifecycle

- Engines are created lazily on first access
- One engine instance per knowledge base
- Engines are reused across requests (cached in `EngineRegistry`)
- On knowledge base deletion or shutdown, `await engine.finalize_storages()` is called

## Dependencies on Upstream Projects

The service dynamically adds LightRAG and RAG-Anything to `sys.path` at runtime:

```python
for dependency_dir in (ROOT_DIR / 'LightRAG', ROOT_DIR / 'RAG-Anything'):
    if dependency_dir.exists() and str(dependency_dir) not in sys.path:
        sys.path.insert(0, str(dependency_dir))
```

When modifying upstream components, see their respective CLAUDE.md files for detailed guidance.