# API 接口文档

## 基础信息

**服务地址**: http://localhost:8080

**API 文档**: http://localhost:8080/docs (Swagger UI 交互式文档)

**健康检查**: `/health` - GET 请求，返回服务状态

---

## 知识库管理 API

### 1. 列出所有知识库

**端点**: `/api/v1/knowledge-bases`
**方法**: GET
**响应示例**:
```json
[
  {
    "id": "kb_450b19a43226",
    "name": "test-kb",
    "description": "测试知识库",
    "created_at": "2026-04-16T06:55:45.776644+00:00",
    "updated_at": "2026-04-16T06:55:45.776661+00:00",
    "documents": [],
    "config_overrides": {}
  }
]
```

---

### 2. 创建知识库

**端点**: `/api/v1/knowledge-bases`
**方法**: POST
**请求体**:
```json
{
  "name": "my-knowledge-base",
  "description": "专业知识库",
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

**响应示例**:
```json
{
  "id": "kb_450b19a43226",
  "name": "my-knowledge-base",
  "description": "专业知识库",
  "created_at": "2026-04-16T06:55:45.776644+00:00",
  "updated_at": "2026-04-16T06:55:45.776661+00:00",
  "documents": [],
  "config_overrides": {}
}
```

---

### 3. 获取知识库详情

**端点**: `/api/v1/knowledge-bases/{knowledge_base_id}`
**方法**: GET
**路径参数**: `knowledge_base_id` - 知识库 ID
**响应示例**: 同创建知识库响应

---

### 4. 删除知识库

**端点**: `/api/v1/knowledge-bases/{knowledge_base_id}`
**方法**: DELETE
**路径参数**: `knowledge_base_id` - 知识库 ID
**查询参数**: `purge_data=true` - 是否删除关联数据
**响应示例**:
```json
{
  "knowledge_base_id": "kb_6169ea1dfc77",
  "deleted": true,
  "data_purged": true
}
```

---

## 文档管理 API

### 1. 列出知识库文档

**端点**: `/api/v1/knowledge-bases/{knowledge_base_id}/documents`
**方法**: GET
**路径参数**: `knowledge_base_id` - 知识库 ID
**响应示例**:
```json
[
  {
    "id": "doc_2199fbe17dbb",
    "original_filename": "report.pdf",
    "stored_filename": "doc_2199fbe17dbb_report.pdf",
    "content_type": "application/pdf",
    "size_bytes": 1024,
    "status": "completed",
    "doc_id": "doc_2199fbe17dbb",
    "error_message": null,
    "created_at": "2026-04-16T06:58:38.210327+00:00",
    "updated_at": "2026-04-16T06:58:38.210360+00:00"
  }
]
```

**文档状态**:
- `queued` - 排队中
- `processing` - 处理中
- `completed` - 完成
- `failed` - 失败

---

### 2. 上传文档

**端点**: `/api/v1/knowledge-bases/{knowledge_base_id}/documents/upload`
**方法**: POST
**路径参数**: `knowledge_base_id` - 知识库 ID
**请求体**: `multipart/form-data`
**表单字段**: `files` - 文件列表（支持多文件上传）
**响应示例**:
```json
{
  "knowledge_base_id": "kb_450b19a43226",
  "accepted_count": 2,
  "documents": [
    {
      "id": "doc_2199fbe17dbb",
      "original_filename": "report.pdf",
      "stored_filename": "doc_2199fbe17dbb_report.pdf",
      "content_type": "application/pdf",
      "size_bytes": 1024,
      "status": "queued"
    }
  ]
}
```

**支持的文件类型**:
- PDF 文档 (.pdf)
- 图片 (.png, .jpg, .jpeg)
- PowerPoint (.pptx)
- 文本 (.txt)
- Markdown (.md)

**自动处理规则**:
- PDF 超过 200 页会自动拆分为 50 页/块
- 每个块作为独立文档入库

---

## 查询 API

### 1. 知识库查询

**端点**: `/api/v1/knowledge-bases/{knowledge_base_id}/query`
**方法**: POST
**路径参数**: `knowledge_base_id` - 知识库 ID
**请求体**:
```json
{
  "query": "什么是机器学习?",
  "mode": "hybrid",
  "query_options": {
    "top_k": 20
  },
  "system_prompt": "请用中文回答问题",
  "multimodal_content": []
}
```

**查询模式**:
- `hybrid` - 混合模式（推荐，结合知识图谱和向量检索）
- `local` - 本地模式（聚焦特定实体）
- `global` - 全局模式（广泛知识检索）
- `naive` - 朴素模式（纯向量检索）
- `mix` - 混合模式（推荐与重排序器一起使用）

**响应示例**:
```json
{
  "knowledge_base_id": "kb_450b19a43226",
  "mode": "hybrid",
  "answer": "机器学习是人工智能的一个子领域，它使计算机系统能够从数据中学习并改进，无需显式编程..."
}
```

---

### 2. 文档处理进度查询

**端点**: `/api/v1/documents/{document_id}/progress`
**方法**: GET
**路径参数**: `document_id` - 文档 ID
**响应示例**:
```json
{
  "document_id": "doc_2199fbe17dbb",
  "current_page": 9,
  "total_pages": 312,
  "progress_percent": 14.21,
  "elapsed_seconds": 621,
  "estimated_remaining_seconds": 3500
}
```

**字段说明**:
- `current_page` - 当前正在处理的页码
- `total_pages` - 文档总页数
- `progress_percent` - 完成百分比
- `elapsed_seconds` - 已耗时（秒）
- `estimated_remaining_seconds` - 预估剩余时间（秒）

---

### 3. 全局进度查询

**端点**: `/api/v1/progress`
**方法**: GET
**响应示例**: 返回所有正在处理的文档进度列表

---

## 系统信息 API

### 1. 系统摘要

**端点**: `/api/v1/system-summary`
**方法**: GET
**响应示例**:
```json
{
  "service_name": "Multimodal Knowledge Base Manager",
  "service_version": "0.1.0",
  "llm_model": "Qwen3.5-397B",
  "vision_model": "Qwen3.5-397B",
  "embedding_model": "BAAI/bge-m3",
  "vector_storage": "QdrantVectorDBStorage",
  "graph_storage": "Neo4JStorage",
  "default_query_mode": "hybrid",
  "image_processing_enabled": true,
  "table_processing_enabled": true,
  "equation_processing_enabled": true
}
```

---

## 错误响应

所有错误响应格式统一为:

```json
{
  "detail": "错误描述信息"
}
```

**常见错误码**:
- `404` - 资源不存在
- `400` - 请求参数错误
- `500` - 内部服务器错误
- `503` - 服务不可用（文档处理超时）

---

## 配置说明

### 模型配置

**LLM 模型**: Qwen3.5-397B
**Vision 模型**: Qwen3.5-397B
**Embedding 模型**: BAAI/bge-m3 (1024 维度)
**超时时间**: LLM 180 秒, Embedding 120 秒

### 存储配置

**向量存储**: Qdrant (http://localhost:6333)
**图存储**: Neo4j (http://localhost:7474)
**元数据存储**: JSON 文件

### 查询配置

**默认模式**: hybrid
**默认 top_k**: 20
**默认 chunk_top_k**: 10
**VLM 增强**: 启用

---

## 数据隔离

每个知识库独立存储:
- 文档目录: `./data/knowledge_bases/{kb_id}/inputs/`
- 解析输出: `./data/knowledge_bases/{kb_id}/output/`
- RAG 存储: `./data/knowledge_bases/{kb_id}/rag_storage/`

---

## 批量操作

**批量上传文档**: 支持同时上传多个文件
**批量文档入库**: 后台异步处理，支持并行入库
**最大并行入库数**: 2（默认，可配置到 10）

---

## 注意事项

1. **GPU 要求**: 需要配置 GPU 访问才能使用 VLM 功能
2. **文档入库时间**: 大型文档需要数分钟处理时间
3. **知识图谱构建**: 文档入库后需要时间构建知识图谱
4. **模型上下文**: LLM 需要 32KB+ 上下文窗口
5. **Embedding 一致性**: 入库和查询必须使用相同的 embedding 模型

---

## API 版本

当前 API 版本: `v1`
基础路径: `/api/v1/`