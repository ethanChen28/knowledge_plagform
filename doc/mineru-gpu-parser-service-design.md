# MinerU GPU 微服务化设计

## 1. 背景

当前 PDF 入库流程中，MinerU 解析直接运行在主服务进程所在容器内：

- 主服务在 `app/main.py` 中调用 `engine.process_document_complete(...)`
- `RAG-Anything` 在 `processor.py` 中调用 `parse_document()`
- `MineruParser.parse_pdf()` 最终在本地拉起 `mineru` 子进程执行解析

这导致几个问题：

- 主服务必须具备 GPU、CUDA、驱动和 MinerU 运行环境
- 主服务和 GPU 解析能力无法独立部署、独立扩缩容
- 解析任务耗时长，主服务实例承载过多重 CPU/GPU 混合职责
- 解析产物与本地绝对路径强耦合，后续迁移为多实例部署困难

当前仓库已经完成两项基础改造：

- `inputs/output` 已接入 MinIO 对象存储
- LightRAG 的 `kv_storage/doc_status_storage` 已切到 Redis

因此，MinerU 拆为 GPU 微服务的条件已经基本具备。

## 2. 目标

本设计的目标是：

- 将 MinerU 从主服务中拆出，形成可独立部署的 GPU 解析服务
- 主服务只负责上传、任务编排、LightRAG 入库和查询
- 通过 MinIO 作为主服务与解析服务的共享文件交换层
- 尽量复用现有 `RAG-Anything` 和 `LightRAG` 流程，避免重写文本入库、多模态入库逻辑
- 为后续多 GPU 调度、任务队列、解析缓存优化预留演进空间

## 3. 非目标

本次设计不做以下事情：

- 不重写 LightRAG 文本切块、图谱构建、向量写入逻辑
- 不在 V1 中重构全部 `img_path` 为对象 ID 模式
- 不在 V1 中引入独立消息队列系统如 Kafka、RabbitMQ
- 不在 V1 中解决跨文档内容去重和全局解析缓存问题

## 4. 当前架构与耦合点

### 4.1 当前主链路

1. 用户上传 PDF
2. 主服务落盘到 `inputs/`
3. 主服务异步调用 `engine.process_document_complete(...)`
4. `RAG-Anything` 本地调用 `MineruParser.parse_pdf()`
5. MinerU 在本地 `output/` 目录产生：
   - `*.md`
   - `*_content_list.json`
   - 图片、表格截图、公式图片等文件
6. `content_list` 被拆分为文本和多模态内容
7. 文本进入 LightRAG，多模态内容进入各自 processor
8. 解析产物归档到 MinIO

### 4.2 当前关键耦合

主要耦合点有两个：

1. GPU 执行耦合
   - MinerU 由主服务本地子进程启动
   - 主服务必须带 GPU 依赖

2. 本地路径耦合
   - `content_list` 内的 `img_path/table_img_path/equation_img_path` 会被转换为本地绝对路径
   - 多模态处理和查询阶段会直接读取这些本地文件

第二点决定了：MinerU 微服务化不能只把 JSON 返回给主服务，还必须保证解析产物能被主服务继续访问。

## 5. 总体方案

采用“两阶段演进”方案：

- V1：先将 MinerU 解析拆为独立 GPU 微服务，主服务通过 HTTP 调用它，解析产物仍按当前 MinerU 目录结构回写到 MinIO，主服务再下载到本地继续执行现有入库逻辑
- V2：引入统一的解析产物定位器（artifact resolver），逐步摆脱查询阶段对本地绝对路径的依赖

## 6. V1 方案

### 6.1 组件拆分

V1 采用以下部署拓扑：

- `multimodal-kb` 主服务
  - CPU 服务
  - 负责上传、知识库管理、文档状态管理、LightRAG 入库、查询
- `mineru-parser-service`
  - GPU 服务
  - 负责从 MinIO 拉取输入文件，调用 MinerU 解析，再将产物回写 MinIO
- `MinIO`
  - 负责输入文档和解析产物交换
- `Redis`
  - 继续作为 LightRAG KV / doc_status 存储
- `Qdrant` / `Neo4j`
  - 继续作为向量库和图数据库

### 6.2 V1 的核心原则

- 主服务不再本地执行 `mineru` 命令
- 主服务不上传原始文件到解析服务，避免大文件经 HTTP 反复传输
- 主服务和解析服务只交换对象存储 key 和解析参数
- 解析服务写回的输出目录结构必须与当前 MinerU 本地输出结构保持兼容
- 主服务下载远端输出到本地后，继续复用现有 `MineruParser._read_output_files()` 和后续处理逻辑

## 7. V1 处理流程

### 7.1 上传与入库流程

1. 用户上传 PDF 到主服务
2. 主服务将原始文件写入 MinIO：
   - `knowledge_bases/{kb_id}/inputs/{stored_filename}`
3. 主服务进入异步 `_ingest_documents()`
4. 当 parser 配置为 `remote_mineru` 时：
   - 主服务向 GPU 解析服务发起解析请求
   - 请求中只传 `input_object_key`、`output_object_prefix`、解析参数、文档标识
5. GPU 解析服务：
   - 从 MinIO 下载输入文件到本地 job 工作目录
   - 本地执行 MinerU
   - 将输出目录完整上传到 MinIO：
     - `knowledge_bases/{kb_id}/output/{parse_dir_name}/...`
6. 主服务收到成功响应后：
   - 从 MinIO 下载该 `output_object_prefix` 到本地 `output/`
   - 调用现有 `MineruParser._read_output_files()` 读取 `content_list`
   - 继续执行现有文本入库、多模态入库逻辑
7. 完成后更新文档状态

### 7.2 输出目录命名规则

为兼容当前 `RAG-Anything` 的唯一目录逻辑，输出目录名保持与当前规则一致：

- `parse_dir_name = "{input_stem}_{md5(abs_input_path)[:8]}"`

主服务已经使用这一规则生成本地解析目录，因此远端服务写回 MinIO 时也必须使用相同目录名。

### 7.3 对象存储键规则

输入对象键：

```text
knowledge_bases/{kb_id}/inputs/{stored_filename}
```

输出对象前缀：

```text
knowledge_bases/{kb_id}/output/{parse_dir_name}/
```

该规则保证：

- 输出路径可由主服务根据文档记录推导
- 删除知识库时可整体清理对应前缀
- 主服务和解析服务不需要共享数据库即可定位文件

## 8. 解析服务接口设计

### 8.1 V1 采用同步 RPC 接口

V1 不引入独立任务队列，解析服务提供同步 HTTP 接口：

- `POST /api/v1/parse/mineru`
- `GET /health`

之所以采用同步接口，而不是先做异步 job 队列，是因为：

- 当前主服务本身已经在后台异步执行 `_ingest_documents()`
- 将 GPU 解析从主服务中拆出后，最核心目标已经达成
- 可以先控制改动面，避免一次性同时引入新队列、新状态机、新回调协议

后续如需更高并发，再升级为异步任务模型。

### 8.2 请求结构

```json
{
  "request_id": "doc_xxx",
  "knowledge_base_id": "kb_xxx",
  "document_id": "doc_xxx",
  "file_name": "paper.pdf",
  "input_object_key": "knowledge_bases/kb_xxx/inputs/doc_xxx_paper.pdf",
  "output_object_prefix": "knowledge_bases/kb_xxx/output/doc_xxx_paper_ab12cd34",
  "parse_method": "auto",
  "backend": "pipeline",
  "lang": null,
  "source": "local",
  "device": "cuda:0",
  "start_page": null,
  "end_page": null,
  "enable_formula": true,
  "enable_table": true
}
```

### 8.3 响应结构

```json
{
  "request_id": "doc_xxx",
  "job_id": "parse_20260421_xxx",
  "status": "completed",
  "output_object_prefix": "knowledge_bases/kb_xxx/output/doc_xxx_paper_ab12cd34",
  "pages": 320,
  "duration_seconds": 148.2,
  "error_message": null
}
```

失败时：

```json
{
  "request_id": "doc_xxx",
  "job_id": "parse_20260421_xxx",
  "status": "failed",
  "output_object_prefix": "knowledge_bases/kb_xxx/output/doc_xxx_paper_ab12cd34",
  "pages": null,
  "duration_seconds": 31.4,
  "error_message": "Mineru command failed ..."
}
```

### 8.4 幂等性

解析服务以以下组合做幂等键：

- `request_id`
- `input_object_key`
- `parse_method`
- `backend`
- `start_page/end_page`

当相同请求重复到达时：

- 如果输出已存在且完整，可直接返回成功
- 如果已有正在执行的同键任务，可返回冲突或等待同一任务结束

V1 可以先实现“同一 `request_id` 重复请求直接复用已有成功结果”。

## 9. 主服务改造设计

### 9.1 新增 `parser_service` 配置

主服务新增配置段：

```yaml
parser_service:
  enabled: true
  provider: remote_mineru
  base_url: http://mineru-parser-service:8090
  api_key_env: MINERU_PARSER_API_KEY
  connect_timeout_seconds: 10
  read_timeout_seconds: 1800
  request_timeout_seconds: 1800
  healthcheck_enabled: true
```

### 9.2 新增 `remote_mineru` 自定义 parser

利用 `RAG-Anything` 的 parser 注册机制，新增一个自定义 parser：

- 名称：`remote_mineru`
- 责任：
  - 发送解析请求到 GPU 服务
  - 成功后从 MinIO 下载输出目录到本地
  - 复用现有 `MineruParser._read_output_files()` 读取 `content_list`

这样可以避免重写 `process_document_complete()` 主流程。

### 9.3 主服务调用流程

`remote_mineru.parse_pdf()` 的执行步骤应为：

1. 根据当前 `pdf_path` 和 `output_dir` 计算本地 `base_output_dir`
2. 推导 `output_object_prefix`
3. 调用解析服务 `POST /api/v1/parse/mineru`
4. 解析成功后，从 MinIO 下载 `output_object_prefix` 到本地 `base_output_dir`
5. 调用 `MineruParser._read_output_files(base_output_dir, file_stem, method)`
6. 返回 `content_list`

### 9.4 文档元数据扩展

建议为 `DocumentRecord` 增加以下字段：

- `parse_backend: str | None`
- `parse_job_id: str | None`
- `parse_started_at: str | None`
- `parse_finished_at: str | None`
- `parse_error_message: str | None`

说明：

- `status` 仍保持当前 `queued/processing/completed/failed`
- 这些字段只用于观测性，不改变主状态机

V1 代码实现时，至少需要 `parse_backend` 与 `parse_job_id`。

## 10. GPU 解析服务设计

### 10.1 服务职责

GPU 解析服务只做三件事：

- 从 MinIO 下载输入文件
- 本地调用 MinerU
- 将输出目录上传回 MinIO

它不负责：

- 知识库元数据管理
- LightRAG 入库
- 向量库和图数据库写入

### 10.2 配置项

建议配置如下：

```yaml
server:
  host: 0.0.0.0
  port: 8090

object_storage:
  endpoint: minio:9000
  bucket: multimodal-kb
  access_key_env: MINIO_ACCESS_KEY_ID
  secret_key_env: MINIO_SECRET_ACCESS_KEY
  secure: false

mineru:
  default_backend: pipeline
  default_device: cuda:0
  source: local
  max_concurrency: 1
  local_work_root: /app/data/parser_jobs
  cleanup_local_inputs: true
  cleanup_local_outputs: true
```

### 10.3 并发控制

V1 采用进程内信号量限制：

- 单 GPU 默认 `max_concurrency=1`
- 多 GPU 环境可通过部署多个实例实现横向扩展
- 更复杂的 GPU 调度放到 V2 再做

### 10.4 本地工作目录

每个任务使用独立工作目录：

```text
/app/data/parser_jobs/{job_id}/input/
/app/data/parser_jobs/{job_id}/output/
```

任务完成后按配置清理。

## 11. 查询与多模态路径兼容性

### 11.1 当前限制

当前多模态入库和查询阶段会直接读取本地绝对路径：

- 多模态入库时，processor 会读取 `img_path`
- 查询时，VLM 增强逻辑也会直接读取 `Image Path: ...`

因此，V1 虽然已经把解析服务拆出去，但仍保留一个限制：

- 主服务仍需要在本地保留解析产物，或者至少在查询前能访问这些文件

### 11.2 V1 处理策略

V1 采用保守策略：

- 远端解析完成后，主服务把 MinIO 中的输出目录下载到本地 `output/`
- 默认保留本地输出目录，避免查询阶段路径失效

这意味着：

- V1 已实现 GPU 解耦
- 但查询阶段暂未完全摆脱本地文件依赖

### 11.3 V2 改造方向

V2 引入 `artifact resolver`：

- 为图片、表格、公式产物建立对象存储定位能力
- 当查询时发现本地文件缺失，可按需从 MinIO 回拉到本地临时目录
- 后续可以逐步把 chunk 中的 `Image Path` 从绝对路径升级为“逻辑路径 + artifact key”

## 12. 错误处理与重试

### 12.1 主服务侧

主服务在以下情况下将文档标记为 `failed`：

- 解析服务不可达
- 解析服务返回失败
- 解析成功但输出目录下载失败
- 下载成功但 `content_list` 读取失败
- 后续 LightRAG / multimodal insert 失败

### 12.2 重试策略

V1 建议：

- HTTP 网络错误：最多重试 2 次
- 解析服务 5xx：最多重试 1 次
- MinerU 业务错误：不自动重试，直接失败

### 12.3 部分产物处理

如果 MinerU 失败但已产生部分输出：

- 解析服务仍可上传部分产物到 MinIO 以便排查
- 主服务文档状态仍标记为 `failed`

## 13. 安全设计

### 13.1 服务间鉴权

V1 建议通过内部网络 + API Key 双重保护：

- 解析服务只部署在内网
- 主服务调用时带固定 `Authorization: Bearer <token>`

### 13.2 输入约束

解析服务不接受任意本地文件路径，只接受：

- `input_object_key`
- `output_object_prefix`

这样可以避免远端服务被利用去读取宿主机任意路径。

### 13.3 路径安全

所有对象键必须经过规范化：

- 禁止 `..`
- 禁止绝对路径
- 禁止跨 bucket 访问

## 14. 观测性

### 14.1 日志

主服务和解析服务都应输出以下关键日志：

- `knowledge_base_id`
- `document_id`
- `request_id`
- `parse_job_id`
- `input_object_key`
- `output_object_prefix`
- `duration_seconds`

### 14.2 健康检查

解析服务健康检查至少包含：

- HTTP 存活检查
- MinIO 连通性检查
- `mineru --version` 可执行性检查

### 14.3 指标建议

后续可增加：

- 解析请求总数
- 解析成功率
- 平均解析时长
- 当前 GPU 队列深度
- MinIO 下载/上传失败次数

## 15. V2 优化方向

V2 建议做以下增强：

1. 异步任务模型
   - `POST /parse-jobs`
   - `GET /parse-jobs/{job_id}`
   - 主服务轮询或使用回调

2. 解析产物定位器
   - 查询阶段缺图时自动回拉 MinIO 产物

3. 内容哈希缓存
   - 以文件内容 hash + 解析参数作为缓存键
   - 不再依赖本地路径和 mtime

4. 多 GPU 调度
   - 单服务多 worker
   - 或多个 GPU 服务实例由 LB 分流

5. 可观测性完善
   - Prometheus 指标
   - 任务追踪 ID

## 16. 实施顺序

建议按以下顺序实施：

### 第一步

- 新增设计文档
- 明确主服务和 GPU 服务配置模型

### 第二步

- 在仓库内新增 `mineru-parser-service` FastAPI 服务
- 实现 MinIO 下载、MinerU 调用、MinIO 上传

### 第三步

- 在主服务中增加 `parser_service` 配置
- 注册 `remote_mineru` parser
- 打通“远端解析 -> 下载输出 -> 继续现有入库”

### 第四步

- 为文档记录增加 `parse_job_id/parse_backend`
- 增加基础日志和错误处理

### 第五步

- 增加部署说明和单独的 GPU compose 文件
- 在后续版本补充 artifact resolver 和异步 job 模式

## 17. 本次代码实现边界

按本设计继续写代码时，建议本轮只实现 V1：

- 实现独立 GPU 解析服务
- 实现 `remote_mineru` parser
- 主服务切换到远端解析
- 保持现有 LightRAG、多模态入库主流程不变

本轮不实现：

- 解析服务异步 job 队列
- 查询阶段的按需回拉 artifact resolver
- 全量路径模型重构

这样可以先把“GPU 解析从主服务剥离”这个核心目标真正落地。
