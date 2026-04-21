# 多模态知识库管理服务

该服务基于仓库中的 LightRAG 和 RAG-Anything 两个上游项目实现，新增了一层管理 API，用于完成知识库创建、文档上传、异步入库、查询和 Docker 部署。默认部署形态已经切换为 Qdrant + Neo4j + Redis + MinIO + MinerU GPU 解析服务，其中 Qdrant 存储向量，Neo4j 存储知识图谱，Redis 存储 LightRAG 的 KV、文档状态和解析缓存，MinIO 存储知识库输入文件与解析产物，独立的 MinerU 服务负责 GPU 解析。

## 核心能力

- 为每个知识库创建独立的数据目录。
- 通过 RAG-Anything 解析文档、图片、表格和公式。
- 通过 LightRAG 完成图谱检索和问答。
- 通过 Redis 托管 LightRAG 热状态，避免 `rag_storage` 下的大量本地 JSON 文件。
- 通过 MinIO 归档 `inputs` 与 `output`，支持处理后清理本地副本。
- 通过 `remote_mineru` parser 将 MinerU 解析拆到独立 GPU 服务。
- 通过 config/config.yaml 声明模型、解析参数和默认查询参数。
- 通过 docker compose 一次启动应用、Redis、MinIO、MinerU 解析服务，外接宿主机上的 Qdrant 与 Neo4j。

## 当前范围

当前实现覆盖 RAG-Anything 已具备的文档、图片、表格、公式处理能力。音频和视频能力未在这一层继续扩展。

## 默认存储

- KV / 文档状态 / 解析缓存: Redis。
- 向量数据库: Qdrant。
- 知识图谱: Neo4j。
- 对象存储: MinIO，默认归档 `inputs/` 与 `output/`。
- GPU 解析服务: 独立的 `mineru-parser-service`，通过内部 HTTP + MinIO 与主服务交互。
- 本地目录: `./data/knowledge_bases` 仍保留知识库元数据、临时工作目录和可选的本地缓存。
- 连接变量: docker-compose.yml 会向应用注入 `QDRANT_URL`、`NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD`、`NEO4J_DATABASE`、`REDIS_URI`、`MINIO_ACCESS_KEY_ID`、`MINIO_SECRET_ACCESS_KEY`、`MINERU_PARSER_API_KEY`。
- 注意: 不要设置 QDRANT_WORKSPACE 或 NEO4J_WORKSPACE，否则会覆盖知识库级隔离。

## 启动步骤

1. 修改 [config/config.yaml](/media/gh/78b691ea-44b9-4302-a49c-c4b04084b44f1/workspace/knowledge_plagform/config/config.yaml)，声明主服务的 llm、vision、embedding 和 `parser_service` 配置。
2. 如需单独调整 GPU 解析服务，修改 [config/mineru_parser_service.yaml](/media/gh/78b691ea-44b9-4302-a49c-c4b04084b44f1/workspace/knowledge_plagform/config/mineru_parser_service.yaml)。
3. 导出 `OPENAI_API_KEY`；如 embedding 服务需要鉴权，再导出 `EMBEDDING_API_KEY`；如需自定义图数据库密码，再导出 `NEO4J_PASSWORD`。
4. 如需覆盖对象存储账号或内部鉴权 token，再导出 `MINIO_ROOT_USER`、`MINIO_ROOT_PASSWORD`、`MINERU_PARSER_API_KEY`。
5. 执行 `docker compose up --build -d`。
6. 打开 http://localhost:8080/ 使用前端控制台，或访问 http://localhost:8080/docs 查看接口文档。
7. 如需查看底层存储，Qdrant 为 http://localhost:6333，Neo4j Browser 为 http://localhost:7474，MinIO Console 为 http://localhost:9001。

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

创建知识库时可以传入 `config_overrides`，支持 `rag_anything`、`light_rag_init_kwargs`、`query_defaults` 三类覆盖。默认已经启用 Redis + Qdrant + Neo4j + MinIO + `remote_mineru`，如有需要也可以按知识库继续覆写 LightRAG 初始化参数；`workspace` 会被服务端固定为知识库 ID，用于保证 Redis、Qdrant、Neo4j 的隔离。

## 目录结构

- app/main.py: 服务入口
- app/mineru_parser_service.py: 独立 MinerU GPU 解析服务
- app/object_storage.py: MinIO 对象存储封装
- app/remote_mineru_parser.py: 主服务侧远端 MinerU parser 适配层
- config/config.yaml: 默认配置
- config/mineru_parser_service.yaml: GPU 解析服务配置
- docker-compose.yml: 应用、Redis、MinIO、MinerU 编排文件
- Dockerfile: 容器构建文件
- Dockerfile.mineru-parser-service: GPU 解析服务镜像构建文件
- LightRAG/: 上游图谱 RAG 能力
- RAG-Anything/: 上游多模态解析能力
