# 多模态知识库管理服务

该服务基于仓库中的 LightRAG 和 RAG-Anything 两个上游项目实现，新增了一层管理 API，用于完成知识库创建、文档上传、异步入库、查询和 Docker 部署。默认部署形态已经切换为 Qdrant + Neo4j，其中 Qdrant 存储向量，Neo4j 存储知识图谱，知识库自身的输入文件、解析产物和元数据仍保存在 ./data 下。

## 核心能力

- 为每个知识库创建独立的数据目录。
- 通过 RAG-Anything 解析文档、图片、表格和公式。
- 通过 LightRAG 完成图谱检索和问答。
- 通过 config/config.yaml 声明模型、解析参数和默认查询参数。
- 通过 docker compose 一次启动应用、Qdrant、Neo4j 三个服务。

## 当前范围

当前实现覆盖 RAG-Anything 已具备的文档、图片、表格、公式处理能力。音频和视频能力未在这一层继续扩展。

## 默认存储

- 向量数据库: Qdrant。
- 知识图谱: Neo4j。
- 本地目录: ./data/knowledge_bases 保存上传文件、解析产物、状态元数据。
- 连接变量: docker-compose.yml 会向应用注入 QDRANT_URL、NEO4J_URI、NEO4J_USERNAME、NEO4J_PASSWORD、NEO4J_DATABASE。
- 注意: 不要设置 QDRANT_WORKSPACE 或 NEO4J_WORKSPACE，否则会覆盖知识库级隔离。

## 启动步骤

1. 修改 config/config.yaml，声明 llm、vision、embedding 配置。
2. 导出 OPENAI_API_KEY；如 embedding 服务需要鉴权，再导出 EMBEDDING_API_KEY；如需自定义图数据库密码，再导出 NEO4J_PASSWORD。
3. 执行 docker compose up --build -d。
4. 打开 http://localhost:8080/ 使用前端控制台，或访问 http://localhost:8080/docs 查看接口文档。
5. 如需查看底层存储，Qdrant 为 http://localhost:6333，Neo4j Browser 为 http://localhost:7474。

## 主要接口

- GET /health
- GET /api/v1/knowledge-bases
- POST /api/v1/knowledge-bases
- GET /api/v1/knowledge-bases/{id}
- DELETE /api/v1/knowledge-bases/{id}
- GET /api/v1/knowledge-bases/{id}/documents
- POST /api/v1/knowledge-bases/{id}/documents/upload
- POST /api/v1/knowledge-bases/{id}/query

## 知识库级覆盖配置

创建知识库时可以传入 config_overrides，支持 rag_anything、light_rag_init_kwargs、query_defaults 三类覆盖。默认已经启用 Qdrant + Neo4j，如有需要也可以按知识库继续覆写 LightRAG 初始化参数。

## 目录结构

- app/main.py: 服务入口
- config/config.yaml: 默认配置
- docker-compose.yml: 应用、Qdrant、Neo4j 编排文件
- Dockerfile: 容器构建文件
- LightRAG/: 上游图谱 RAG 能力
- RAG-Anything/: 上游多模态解析能力
