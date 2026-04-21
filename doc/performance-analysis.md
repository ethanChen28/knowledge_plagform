# 文档入库处理速度分析与优化建议

## 当前处理流程

```
上传 PDF → MinerU OCR 解析 → RAG-Anything 多模态处理 → LightRAG 知识图谱构建
```

## 各阶段耗时分析

### 1. MinerU OCR 解析（较快，约 2-3 分钟/140 页 PDF）

- Pipeline 模式使用 PaddleOCR，在 GPU 上运行
- 63 页实际处理页，OCR 约 377 次预测
- GPU 0 占用约 1.4GB 显存
- **瓶颈**：首次运行需要下载 OCR 模型文件（约 1-2 分钟），后续有缓存

### 2. RAG-Anything 多模态处理（较快）

- 将 MinerU 输出解析为结构化内容（文本、图片、表格、公式）
- 提取上下文信息
- 通常在数秒内完成

### 3. LightRAG 知识图谱构建（**主要瓶颈**）

这是最慢的阶段，占据 90% 以上的处理时间。

#### 慢的原因

| 原因 | 说明 |
|------|------|
| **LLM 串行调用** | 140 页 PDF 产生约 20 个 chunk，每个 chunk 需调用 Qwen API 提取实体和关系，**串行处理** |
| **LLM 响应时间** | 每次调用 Qwen3.5-397B 需要 10-30 秒（含推理/思考时间），20 个 chunk 需要 5-10 分钟 |
| **InvalidResponseError** | Qwen 有时返回格式不符合 LightRAG 要求，导致重试（最多 20 次），进一步增加耗时 |
| **知识图谱写入** | 每次提取后需写入 Neo4j + Qdrant，网络 I/O 开销 |
| **Embedding 调用** | 每个 chunk 和实体需调用 Embedding API 生成向量 |

#### 详细瓶颈分解

```
24887 字符文本 → 20 个 chunk
每个 chunk:
  1. 调用 Qwen 提取实体和关系 → 10-30s（含 think 时间）
  2. 解析 LLM 响应 → 如果格式不对会 InvalidResponseError 重试
  3. Embedding 实体/关系 → 调用远程 192.168.16.36:8081
  4. 写入 Neo4j 图数据库
  5. 写入 Qdrant 向量数据库

总耗时 ≈ 20 chunks × (20s LLM + 2s embedding + 1s DB) ≈ 8-10 分钟
```

## 优化建议

### 短期优化（配置调整）

1. **增大 chunk 大小，减少 chunk 数量**
   ```yaml
   light_rag:
     init_kwargs:
       chunk_size: 2400  # 默认 1200，增大可减少 chunk 数
   ```

2. **降低 LLM 重试次数**
   ```yaml
   light_rag:
     init_kwargs:
       max_retries: 3  # 默认可能较高，降低可减少重试等待
   ```

3. **使用更快的 LLM 模型**
   - 当前用 Qwen3.5-397B（含 thinking），每次调用含推理 token
   - 可考虑用更小但更快的模型（如 Qwen3-8B）做实体提取

4. **关闭 thinking 模式**
   - Qwen3.5 的 thinking_blocks 占用大量 token 和时间
   - 在 extra_body 中添加 `"enable_thinking": false` 可显著加速

### 中期优化（架构调整）

5. **并行 LLM 调用**
   - LightRAG 默认串行处理 chunk
   - 修改为并行调用可成倍提速

6. **使用本地 LLM**
   - 在 8×RTX3090 上部署 vLLM + 小模型（如 Qwen3-8B）
   - 消除网络延迟，吞吐量更高

7. **异步 Embedding 批处理**
   - 将 embedding 请求批量发送，而非逐个调用

### 长期优化

8. **增量索引** — 只处理新增/修改的 chunk
9. **缓存 LLM 响应** — 相似内容复用已有实体
10. **分级处理** — 先快速 naive 模式入库，后台再优化知识图谱

## 当前已知问题

- `InvalidResponseError`: Qwen3.5 的输出有时不满足 LightRAG 的格式要求，导致 chunk 处理失败
- 建议在 `light_rag.init_kwargs` 中调整 `cosine_better_than_threshold` 或使用更稳定的模型
