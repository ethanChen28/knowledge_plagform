# 多模态知识库管理系统 - 用户手册

## 目录

1. [系统简介](#系统简介)
2. [快速开始](#快速开始)
3. [知识库管理](#知识库管理)
4. [文档上传与处理](#文档上传与处理)
5. [知识查询](#知识查询)
6. [常见问题](#常见问题)

---

## 系统简介

### 什么是多模态知识库？

多模态知识库管理系统是基于 **LightRAG** 和 **RAG-Anything** 构建的知识检索增强生成（RAG）平台，支持：

**多模态内容处理**：
- **文本**: PDF、Word、Markdown 等文档
- **图片**: 图表、示意图、架构图
- **表格**: 数据表、配置表
- **公式**: 数学公式、化学方程

**智能检索**：
- **知识图谱检索**: 基于实体-关系图谱的结构化检索
- **向量检索**: 语义相似度搜索
- **混合检索**: 结合图谱和向量，提高召回率

**智能问答**：
- 基于检索内容的 LLM 生成回答
- 支持多轮对话和上下文理解
- 支持自定义系统提示词

### 核心优势

1. **自动化文档处理**: MinerU 自动解析 PDF，提取文本、表格、公式
2. **知识图谱构建**: 自动抽取实体和关系，构建结构化知识图谱
3. **大型文档优化**: 超过 200 页的 PDF 自动拆分处理
4. **GPU 加速**: 配置 GPU 访问后，处理速度提升 10-20 倍
5. **实时进度跟踪**: 监控文档处理进度和预估时间

---

## 快速开始

### 访问系统

**前端控制台**: http://localhost:8080/

**API 文档**: http://localhost:8080/docs

### 第一步：创建知识库

**示例请求**:
```bash
curl -X POST http://localhost:8080/api/v1/knowledge-bases \
  -H "Content-Type: application/json" \
  -d '{
    "name": "AI技术知识库",
    "description": "存储AI相关技术文档"
  }'
```

**响应示例**:
```json
{
  "id": "kb_123abc456",
  "name": "AI技术知识库",
  "description": "存储AI相关技术文档",
  "created_at": "2026-04-16T06:55:45.776644+00:00"
}
```

### 第二步：上传文档

**示例请求**:
```bash
curl -X POST http://localhost:8080/api/v1/knowledge-bases/kb_123abc456/documents/upload \
  -F "files=@report.pdf" \
  -F "files=@diagram.png"
```

**响应示例**:
```json
{
  "knowledge_base_id": "kb_123abc456",
  "accepted_count": 2,
  "documents": [
    {
      "id": "doc_789def",
      "original_filename": "report.pdf",
      "status": "queued"
    }
  ]
}
```

### 第三步：等待文档入库

**检查文档状态**:
```bash
curl http://localhost:8080/api/v1/knowledge-bases/kb_123abc456/documents
```

**查看处理进度**:
```bash
curl http://localhost:8080/api/v1/documents/doc_789def/progress
```

**响应示例**:
```json
{
  "document_id": "doc_789def",
  "current_page": 9,
  "total_pages": 312,
  "progress_percent": 14.21,
  "estimated_remaining_seconds": 3500
}
```

### 第四步：查询知识库

**示例请求**:
```bash
curl -X POST http://localhost:8080/api/v1/knowledge-bases/kb_123abc456/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "什么是深度学习?",
    "mode": "hybrid"
  }'
```

**响应示例**:
```json
{
  "knowledge_base_id": "kb_123abc456",
  "mode": "hybrid",
  "answer": "深度学习是机器学习的一个子集，它使用神经网络模拟人脑处理信息的方式..."
}
```

---

## 知识库管理

### 创建知识库

**必填字段**:
- `name` - 知识库名称（建议 10 字符以内）

**可选字段**:
- `description` - 知识库描述
- `config_overrides` - 配置覆盖

**配置覆盖示例**:
```json
{
  "config_overrides": {
    "rag_anything": {
      "parser": "mineru",
      "enable_table_processing": true,
      "enable_equation_processing": true
    },
    "light_rag_init_kwargs": {
      "cosine_better_than_threshold": 0.3
    },
    "query_defaults": {
      "mode": "hybrid",
      "top_k": 60
    }
  }
}
```

**配置项说明**:

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `parser` | 文档解析器 | `mineru` |
| `enable_table_processing` | 表格处理 | `true` |
| `enable_equation_processing` | 公式处理 | `true` |
| `cosine_better_than_threshold` | 相似度阈值 | `0.2` |
| `mode` | 查询模式 | `hybrid` |
| `top_k` | 检索结果数量 | `20` |

### 删除知识库

**删除模式**:
- `purge_data=true` - 删除知识库及所有关联数据
- `purge_data=false` - 仅删除元数据，保留知识图谱数据

**警告**: 删除知识库后无法恢复，请谨慎操作！

---

## 文档上传与处理

### 支持的文档类型

| 文档类型 | 文件扩展名 | 处理方式 |
|----------|------------|----------|
| **PDF 文档** | .pdf | MinerU 自动解析 |
| **图片** | .png, .jpg, .jpeg | VLM 图像理解 |
| **PowerPoint** | .pptx | 转换 PDF 后解析 |
| **文本** | .txt, .md | 通用文本处理 |
| **Office 文档** | .docx, .xlsx | 转换 PDF 后解析 |

### 自动处理规则

**大型 PDF 自动拆分**:
- **阈值**: 超过 200 页的 PDF
- **拆分大小**: 每块 50 页
- **处理**: 每块作为独立文档入库

**示例**:
- 上传 312 页 PDF
- 自动拆分为约 6 个块
- 每块独立入库到知识图谱

### 文档处理流程

**步骤 1**: 文件上传 → 存储到输入目录

**步骤 2**: 文档解析 → MinerU 提取内容
- 提取文本、图片、表格、公式
- 生成结构化内容列表

**步骤 3**: 知识抽取 → LightRAG 构建图谱
- 实体抽取：识别文本中的关键实体
- 关系抽取：识别实体之间的关系
- 向量化：生成实体和关系的向量表示

**步骤 4**: 存储入库 → Qdrant + Neo4j
- 向量存储：Qdrant 存储向量嵌入
- 图存储：Neo4j 存储知识图谱

### 查看处理进度

**API 端点**: `/api/v1/documents/{document_id}/progress`

**进度字段说明**:

| 字段 | 说明 | 单位 |
|------|------|------|
| `current_page` | 当前处理页码 | 页 |
| `total_pages` | 文档总页数 | 页 |
| `progress_percent` | 完成百分比 | % |
| `elapsed_seconds` | 已耗时 | 秒 |
| `estimated_remaining_seconds` | 预估剩余时间 | 秒 |

**典型处理时间**:

| 文档类型 | 页数 | GPU 模式 | CPU 模式 |
|----------|------|----------|----------|
| 文本文档 | 1-10 页 | 30-60 秒 | 120-180 秒 |
| PDF 文档 | 50-100 页 | 5-10 分钟 | 15-30 分钟 |
| 大型 PDF | 200+ 页 | 自动拆分处理 |

---

## 知识查询

### 查询模式说明

**1. hybrid 模式（推荐）**
- 结合知识图谱和向量检索
- 平衡精确性和召回率
- 适合大多数场景

**2. local 模式**
- 聚焦特定实体和局部上下文
- 适合精确查询特定信息

**3. global 模式**
- 基于知识图谱的全局摘要
- 适合广泛知识检索

**4. naive 模式**
- 纯向量相似度检索
- 不使用知识图谱
- 适合简单语义搜索

**5. mix 模式（高级）**
- 结合知识图谱和向量检索
- 支持重排序器优化
- 需要配置 reranker

### 查询参数调优

**关键参数**:

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `top_k` | 检索实体/关系数量 | 20-60 |
| `chunk_top_k` | 检索文本块数量 | 10-20 |
| `mode` | 查询模式 | hybrid |
| `system_prompt` | 自定义提示词 | - |

**自定义提示词示例**:
```json
{
  "query": "什么是机器学习?",
  "mode": "hybrid",
  "system_prompt": "请用简洁的中文回答，不超过200字"
}
```

### 多模态查询

**支持查询时携带图片**:
```json
{
  "query": "这张图展示了什么架构?",
  "multimodal_content": [
    {
      "type": "image",
      "image_base64": "<base64编码>",
      "mime_type": "image/png"
    }
  ]
}
```

**VLM 增强查询**:
- 系统自动尝试 VLM 增强查询
- 如果知识库中有图片，会自动理解图片内容
- 如果没有图片，降级为普通查询

---

## 常见问题

### Q1: 文档处理时间过长怎么办？

**原因分析**:
1. **GPU 未配置**: 检查是否配置了 GPU 访问
2. **文档过大**: 超过 200 页的 PDF 已自动拆分
3. **网络问题**: LLM 服务可能响应慢

**解决方案**:
```bash
# 检查 GPU 配置
docker exec multimodal-kb env | grep NVIDIA

# 查看处理进度
curl http://localhost:8080/api/v1/documents/{doc_id}/progress
```

### Q2: 查询返回空结果怎么办？

**原因分析**:
- 知识库内容太少，无法构建有效知识图谱
- 文档入库未完成
- 查询模式不适合

**解决方案**:
1. 等待文档入库完成（状态变为 `completed`）
2. 上传更多丰富内容的文档
3. 尝试不同的查询模式

### Q3: 如何提高检索质量？

**优化建议**:
1. **上传丰富内容**: PDF、图片、表格等
2. **等待知识图谱构建**: 文档入库后需要时间构建图谱
3. **调整查询参数**: 提高 `top_k` 和 `chunk_top_k`
4. **使用合适模式**: 根据场景选择 hybrid/local/global
5. **自定义提示词**: 引导 LLM 生成更贴合的答案

### Q4: 系统架构是怎样的？

**核心组件**:
- **FastAPI 应用**: 提供 REST API 服务
- **RAG-Anything**: 多模态文档解析
- **LightRAG**: 知识图谱构建与检索
- **Qdrant**: 向量存储
- **Neo4j**: 图存储

**数据流**:
```
文档上传 → MinerU 解析 → LightRAG 入库 → Qdrant/Neo4j 存储
    ↓
查询请求 → LightRAG 检索 → LLM 生成 → 返回答案
```

### Q5: 支持哪些文件类型？

**全面支持**:
- PDF 文档（学术论文、技术报告）
- 图片（图表、示意图）
- PowerPoint 演示文稿
- 文本文档（TXT, Markdown）
- Office 文档（Word, Excel）

**处理方式**:
- PDF: MinerU 直接解析
- 图片: VLM 理解
- Office: 转换 PDF 后解析

---

## 高级配置

### 环境变量配置

**必需环境变量**:
```bash
OPENAI_API_KEY=your_key  # LLM API 密钥
NEO4J_PASSWORD=12345678   # Neo4j 密码
```

**可选环境变量**:
```bash
EMBEDDING_API_KEY=EMPTY  # Embedding API 密钥（可选）
```

### Docker Compose 配置

**GPU 访问配置**:
```yaml
services:
  multimodal-kb:
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
    runtime: nvidia
```

**存储配置**:
```yaml
services:
  multimodal-kb:
    environment:
      QDRANT_URL: http://host.docker.internal:6333
      NEO4J_URI: neo4j://host.docker.internal:7687
      NEO4J_USERNAME: neo4j
      NEO4J_PASSWORD: ${NEO4J_PASSWORD:-12345678}
```

---

## 性能优化建议

### 1. GPU 加速

**启用 GPU 后**:
- 处理速度提升 10-20 倍
- batch_size 可提升到 64+
- VLM 功能可用

### 2. 文档内容丰富度

**建议上传**:
- 多个相关文档
- 图表和示意图
- 表格数据

**避免上传**:
- 极短文本（无法构建知识图谱）
- 重复内容

### 3. 查询参数调优

**调整策略**:
- 提高 `top_k` 提升召回率
- 降低 `cosine_better_than_threshold` 提升匹配率
- 使用 `mix` 模式 + reranker 提升排序质量

---

## 最佳实践

### 知识库规划

1. **明确知识库主题**: 每个知识库聚焦一个领域
2. **避免知识库混用**: 不同领域使用独立知识库
3. **定期清理低质量文档**: 删除错误或低质文档

### 文档上传策略

1. **批量上传**: 一次性上传多个相关文档
2. **等待入库**: 上传后等待处理完成再查询
3. **监控进度**: 使用进度 API 监控处理状态

### 查询优化策略

1. **明确问题**: 问题具体化，避免模糊查询
2. **选择模式**: 根据需求选择 hybrid/local/global
3. **调整参数**: 根据结果质量调整 top_k 等参数

---

## 技术支持

**API 文档**: http://localhost:8080/docs

**系统摘要**: http://localhost:8080/api/v1/system-summary

**健康检查**: http://localhost:8080/health

---

## 更新日志

**版本 0.1.0** (2026-04-16):
- 初始版本发布
- 支持多模态文档处理
- 知识图谱检索与 LLM 问答
- 大型 PDF 自动拆分处理
- 实时进度跟踪
- GPU 加速支持