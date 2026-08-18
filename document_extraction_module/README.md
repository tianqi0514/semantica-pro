# Semantica 文档知识抽取模块

这是一个文档抽取后端模块，并已作为原生工作区融合到 Knowledge Explorer。它把 PDF、DOCX、TXT、HTML 或 Markdown 文档转换为实体节点和关系边，再导出成现有 Explorer `/api/import` 能读取的 JSON 或 CSV。

## 为什么单独做一层

文档抽取结果并不天然等于正确业务事实。这个模块先生成“候选知识”，保留原文证据和置信度，再由使用者检查并导入正式图谱，避免模型误识别直接污染图数据库。

处理链路是：

```text
业务文档 -> Semantica DocumentParser -> 文本分块
         -> NERExtractor -> 实体候选
         -> RelationExtractor -> 关系候选
         -> 去重、稳定 ID、来源证据 -> JSON / CSV
         -> Knowledge Explorer 导入
```

## 已实现的功能

- 上传 `.pdf`、`.docx`、`.txt`、`.html`、`.md` 文档。
- 使用 Semantica 的 `DocumentParser` 解析文档。
- 规则和 ML 路径使用 Semantica 的 `NERExtractor`、`RelationExtractor`；LLM 路径调用同一注册表中的抽取方法，但禁止静默后备，模型服务错误会真实显示。
- 三种抽取方式：
  - `regex`：按场景配置中的正则执行，结果可复现，适合演示和格式固定的材料。
  - `llm`：调用 OpenAI、DeepSeek、OpenAI 兼容服务或 Ollama，适合中文非结构化文本。
  - `ml`：调用 Semantica 的 spaCy 路径；默认模型主要适合英文。
- 对跨分块重复实体与关系进行合并，使用内容哈希生成稳定 ID。
- 每个节点和关系保留 `source_file`、`document_id`、文本分块和原文证据。
- 导出 Explorer 兼容 JSON，或节点和边在同一文件中的 UTF-8 CSV。
- 提供采购合规审查场景配置和一份可直接试跑的样例文档。

## Docker 启动

本模块复用本仓库已经构建的 `semantica-knowledge-explorer:latest` 镜像，再补充模型客户端和 PDF 解析依赖。

```bash
cd document_extraction_module
cp .env.example .env
docker compose up -d --build
```

打开 `http://localhost:8000`，在左侧进入 `Documents`。`文档抽取` 用于上传和预览，`模型配置` 用于维护 Kimi K3 连接参数。`http://localhost:9004` 保留为后端独立调试入口。

默认模型配置是 `kimi-k3`，本地中国站默认地址是 `https://api.moonshot.cn/v1`；国际站 Key 应切换为 `https://api.moonshot.ai/v1`。API Key 不写入源码、`.env`、场景 JSON、任务结果或导出文件；后端使用 Fernet 加密后保存到 `/data/settings.db`。未配置 `DOCUMENT_EXTRACT_MASTER_KEY` 时，本机环境会自动生成 `/data/.settings.key`；生产环境应通过 Secret 注入主密钥。

查看日志：

```bash
docker compose logs -f document-extractor
```

## Python 命令行

在仓库根目录并安装依赖后运行：

```bash
python -m document_extraction_module.src.cli \
  document_extraction_module/samples/采购合规审查样例.txt \
  --config document_extraction_module/config/procurement-compliance.json \
  --output document_extraction_module/output
```

## 如何建立新场景

复制 `config/procurement-compliance.json`，然后修改两组定义：

- `entity_types` 决定要找哪些业务对象。`name` 是稳定类型编码，`label` 和 `description` 帮助 LLM 理解业务含义，`patterns` 给规则模式使用。
- `relation_types` 决定哪些连接具有业务意义。规则模式的表达式必须包含命名分组 `(?P<subject>...)` 与 `(?P<object>...)`。

类型编码会原样成为 Explorer 中节点和边的 `type`，因此应使用稳定的英文编码，不要把一次性的具体名称当成类型。

## 导入现有 Explorer

抽取完成后下载 JSON 或 CSV，在现有界面的“导入”入口上传：

- JSON 顶层是 `entities` 和 `relationships`，保留的元数据最完整，优先推荐。
- CSV 把节点行放在关系行之前，字段使用现有导入源码识别的 `id`、`type`、`source`、`target`、`weight`。

## 当前边界

- 普通 PDF 的文本解析依赖 `pdfplumber`；扫描件没有文字层时，本版会明确提示先做 OCR，没有假装完成抽取。
- `regex` 是场景规则，不理解自然语言；它只保证命中已配置的表达式。
- `llm` 能处理更自由的中文，但输出仍是候选事实，必须结合证据核对。
- 本版不把结果自动写入 FalkorDB，也没有做多人审核工作流；导入动作由用户确认后执行。
- 本版一次处理一个文档。数据结构已包含 `document_id`，后续可以扩展批量任务而不改变导入格式。
- 当前 Web API 没有登录、限流和自动清理历史任务，只适合本机或受控内网演示；公网部署前必须在反向代理层增加认证、访问限制和数据保留策略。
