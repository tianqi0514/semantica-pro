from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from semantica.semantic_extract import (
    NERExtractor,
    RelationExtractor,
    create_provider,
    get_entity_method,
    get_relation_method,
)
from semantica.semantic_extract.types import Entity, Relation

from .chunker import TextChunk, split_text
from .document_loader import DocumentLoader
from .models import ExtractionConfig, TypeDefinition


class DocumentExtractionService:
    """Document -> chunks -> entities -> relations -> Explorer import payload."""

    def __init__(self) -> None:
        self.loader = DocumentLoader()

    def extract(
        self,
        file_path: Path,
        source_name: str,
        config: ExtractionConfig,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        self._report_progress(progress_callback, stage="loading_document")
        text, document_metadata = self.loader.load(file_path)
        self._report_progress(
            progress_callback,
            stage="splitting_text",
            characters_total=len(text),
        )
        chunks = split_text(text, config.chunk_size, config.chunk_overlap)
        if not chunks:
            raise ValueError("文档中没有可处理的文本分块")

        self._report_progress(
            progress_callback,
            stage="checking_model" if config.method == "llm" else "preparing_extractor",
            chunks_total=len(chunks),
            chunks_completed=0,
        )

        if config.method == "llm":
            self._check_provider(config)

        document_id = self._stable_id("doc", source_name, text[:1000])
        entity_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        entity_object_ids: Dict[int, str] = {}
        relationships: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        warnings: List[str] = []
        processed_chunks = 0

        for chunk_index, chunk in enumerate(chunks, start=1):
            try:
                entities, relations = self._extract_chunk(
                    chunk,
                    config,
                    progress_callback=progress_callback,
                    chunk_index=chunk_index,
                    chunks_total=len(chunks),
                )
                processed_chunks += 1
            except Exception as exc:
                warnings.append(f"{chunk.chunk_id} 抽取失败：{self._safe_error(exc)}")
                self._report_progress(
                    progress_callback,
                    stage="chunk_failed",
                    chunks_total=len(chunks),
                    chunks_completed=chunk_index,
                    chunk_index=chunk_index,
                )
                continue

            if config.method == "llm" and entities and not any(
                str(entity.metadata.get("extraction_method", "")).startswith("llm")
                for entity in entities
            ):
                warnings.append(f"{chunk.chunk_id} 的实体结果来自后备规则，而不是 LLM")

            for entity in entities:
                if float(entity.confidence) < config.entity_confidence:
                    continue
                entity_id = self._merge_entity(
                    entity_map,
                    entity,
                    chunk,
                    source_name,
                    document_id,
                    config.entity_types,
                )
                entity_object_ids[id(entity)] = entity_id

            for relation in relations:
                if float(relation.confidence) < config.relation_confidence:
                    continue
                self._merge_relation(
                    relationships,
                    relation,
                    entity_map,
                    entity_object_ids,
                    chunk,
                    source_name,
                    document_id,
                    config,
                )

            self._report_progress(
                progress_callback,
                stage="chunk_completed",
                chunks_total=len(chunks),
                chunks_completed=chunk_index,
                chunk_index=chunk_index,
                entities_found=len(entities),
                relationships_found=len(relations),
            )

        if processed_chunks == 0:
            detail = warnings[0] if warnings else "未知错误"
            raise RuntimeError(f"所有文本分块均抽取失败。{detail}")

        self._report_progress(
            progress_callback,
            stage="document_completed",
            chunks_total=len(chunks),
            chunks_completed=len(chunks),
        )

        entities = sorted(entity_map.values(), key=lambda item: (item["type"], item["text"]))
        relation_list = sorted(
            relationships.values(),
            key=lambda item: (item["type"], item["source"], item["target"]),
        )
        return {
            "schema_version": "1.0",
            "run": {
                "scenario": config.scenario_name,
                "method": config.method,
                "provider": config.provider if config.method == "llm" else None,
                "model": config.model if config.method == "llm" else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            "documents": [
                {
                    "id": document_id,
                    "name": source_name,
                    "character_count": len(text),
                    "chunk_count": len(chunks),
                    "text_preview": text[:1500],
                    "metadata": document_metadata,
                }
            ],
            "entities": entities,
            "relationships": relation_list,
            "statistics": {
                "documents": 1,
                "chunks": len(chunks),
                "chunks_processed": processed_chunks,
                "entities": len(entities),
                "relationships": len(relation_list),
                "warnings": len(warnings),
            },
            "warnings": warnings,
        }

    def _report_progress(
        self,
        callback: Optional[Callable[[Dict[str, Any]], None]],
        **progress: Any,
    ) -> None:
        if callback is not None:
            callback(progress)

    def _check_provider(self, config: ExtractionConfig) -> None:
        kwargs = self._provider_options(config)
        provider = create_provider(config.provider, model=config.model, **kwargs)
        if not provider.is_available():
            env_name = f"{config.provider.upper()}_API_KEY"
            if config.provider.lower() == "ollama":
                raise RuntimeError("Ollama 不可用，请确认服务地址和模型服务已经启动")
            raise RuntimeError(f"模型服务不可用，请安装对应客户端并配置环境变量 {env_name}")

    def _extract_chunk(
        self,
        chunk: TextChunk,
        config: ExtractionConfig,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        chunk_index: int = 1,
        chunks_total: int = 1,
    ) -> Tuple[List[Entity], List[Relation]]:
        entity_names = [item.name for item in config.entity_types]
        common: Dict[str, Any] = {}

        if config.method == "llm":
            common.update(
                {
                    "provider": config.provider,
                    "model": config.model,
                    "base_url": config.base_url,
                    "silent_fail": False,
                    "max_retries": 2,
                    # Kimi K3 is a thinking model and rejects Semantica's
                    # provider default (0.1). The Kimi API requires 1.0.
                    "temperature": 1.0,
                }
            )
            if config.api_key:
                common["api_key"] = config.api_key.get_secret_value()
            entity_type_hints = [item.prompt_text() for item in config.entity_types]
            relation_type_hints = [item.prompt_text() for item in config.relation_types]
            # Use Semantica's registered LLM methods without the facade's
            # automatic rule fallback. Provider errors must remain visible in
            # an auditable extraction UI instead of looking like valid results.
            entity_method = get_entity_method("llm")
            self._report_progress(
                progress_callback,
                stage="extracting_entities",
                chunk_index=chunk_index,
                chunks_total=chunks_total,
            )
            entities = entity_method(
                chunk.text,
                entity_types=entity_type_hints,
                **common,
            )
            relation_method = get_relation_method("llm")
            self._report_progress(
                progress_callback,
                stage="extracting_relations",
                chunk_index=chunk_index,
                chunks_total=chunks_total,
                entities_found=len(entities),
            )
            relations = relation_method(
                chunk.text,
                entities,
                relation_types=relation_type_hints,
                extract_temporal_bounds=config.extract_temporal_bounds,
                **common,
            )
        elif config.method == "regex":
            entity_patterns = {
                item.name: "|".join(f"(?:{pattern})" for pattern in item.patterns)
                for item in config.entity_types
                if item.patterns
            }
            relation_patterns = {
                item.name: item.patterns for item in config.relation_types if item.patterns
            }
            # Regex labels are already the configured canonical codes.  Do not pass
            # entity_types here: Semantica's type-similarity rescoring may load a
            # sentence-transformer, which is unnecessary for deterministic rules.
            ner = NERExtractor(method="regex", min_confidence=0.0)
            self._report_progress(
                progress_callback,
                stage="extracting_entities",
                chunk_index=chunk_index,
                chunks_total=chunks_total,
            )
            entities = ner.extract_entities(
                chunk.text,
                patterns=entity_patterns,
                min_confidence=0.0,
            )
            relation_extractor = RelationExtractor(
                method="regex",
                confidence_threshold=0.0,
                min_confidence=0.0,
            )
            self._report_progress(
                progress_callback,
                stage="extracting_relations",
                chunk_index=chunk_index,
                chunks_total=chunks_total,
                entities_found=len(entities),
            )
            relations = relation_extractor.extract_relations(
                chunk.text,
                entities,
                patterns=relation_patterns,
                min_confidence=0.0,
            )
        else:
            ner = NERExtractor(method="ml", min_confidence=0.0, entity_types=entity_names)
            self._report_progress(
                progress_callback,
                stage="extracting_entities",
                chunk_index=chunk_index,
                chunks_total=chunks_total,
            )
            entities = ner.extract_entities(chunk.text, min_confidence=0.0)
            relation_extractor = RelationExtractor(method="dependency", min_confidence=0.0)
            self._report_progress(
                progress_callback,
                stage="extracting_relations",
                chunk_index=chunk_index,
                chunks_total=chunks_total,
                entities_found=len(entities),
            )
            relations = relation_extractor.extract_relations(
                chunk.text,
                entities,
                min_confidence=0.0,
            )

        entity_list = list(entities)
        relation_list = list(relations)
        self._report_progress(
            progress_callback,
            stage="merging_results",
            chunk_index=chunk_index,
            chunks_total=chunks_total,
            entities_found=len(entity_list),
            relationships_found=len(relation_list),
        )
        return entity_list, relation_list

    def _provider_options(self, config: ExtractionConfig) -> Dict[str, Any]:
        options: Dict[str, Any] = {}
        if config.base_url:
            options["base_url"] = config.base_url
        if config.api_key:
            options["api_key"] = config.api_key.get_secret_value()
        return options

    def _merge_entity(
        self,
        entity_map: Dict[Tuple[str, str], Dict[str, Any]],
        entity: Entity,
        chunk: TextChunk,
        source_name: str,
        document_id: str,
        definitions: List[TypeDefinition],
    ) -> str:
        text = self._clean_entity_text(entity.text)
        entity_type = self._canonical_type(entity.label, definitions, "ENTITY")
        key = (entity_type, self._normalize_text(text))
        entity_id = self._stable_id("ent", entity_type, key[1])
        start, end = self._resolve_span(chunk.text, text, entity.start_char, entity.end_char)
        evidence = {
            "chunk_id": chunk.chunk_id,
            "start": chunk.start + start,
            "end": chunk.start + end,
            "text": self._evidence_text(chunk.text, start, end),
        }
        current = entity_map.get(key)
        if current is None:
            entity_map[key] = {
                "id": entity_id,
                "type": entity_type,
                "text": text,
                "confidence": round(float(entity.confidence), 4),
                "metadata": {
                    "document_id": document_id,
                    "source_file": source_name,
                    "extraction_method": entity.metadata.get("extraction_method", "unknown"),
                    "evidence": [evidence],
                },
            }
        else:
            current["confidence"] = max(current["confidence"], round(float(entity.confidence), 4))
            if evidence not in current["metadata"]["evidence"]:
                current["metadata"]["evidence"].append(evidence)
        return entity_id

    def _merge_relation(
        self,
        relationships: Dict[Tuple[str, str, str], Dict[str, Any]],
        relation: Relation,
        entity_map: Dict[Tuple[str, str], Dict[str, Any]],
        entity_object_ids: Dict[int, str],
        chunk: TextChunk,
        source_name: str,
        document_id: str,
        config: ExtractionConfig,
    ) -> None:
        source_id = entity_object_ids.get(id(relation.subject))
        target_id = entity_object_ids.get(id(relation.object))
        if not source_id:
            source_id = self._find_entity_id(entity_map, relation.subject, config.entity_types)
        if not target_id:
            target_id = self._find_entity_id(entity_map, relation.object, config.entity_types)
        if not source_id or not target_id or source_id == target_id:
            return

        relation_type = self._canonical_type(relation.predicate, config.relation_types, "RELATED_TO")
        key = (source_id, relation_type, target_id)
        relation_id = self._stable_id("rel", *key)
        metadata = relation.metadata or {}
        evidence = {
            "chunk_id": chunk.chunk_id,
            "text": (relation.context or chunk.text[:300]).strip()[:500],
        }
        for field in ("valid_from", "valid_until", "temporal_confidence", "temporal_source_text"):
            if metadata.get(field) is not None:
                evidence[field] = metadata[field]

        current = relationships.get(key)
        if current is None:
            relationships[key] = {
                "id": relation_id,
                "source": source_id,
                "target": target_id,
                "type": relation_type,
                "weight": round(float(relation.confidence), 4),
                "valid_from": metadata.get("valid_from"),
                "valid_until": metadata.get("valid_until"),
                "metadata": {
                    "confidence": round(float(relation.confidence), 4),
                    "document_id": document_id,
                    "source_file": source_name,
                    "extraction_method": metadata.get("extraction_method", "unknown"),
                    "temporal_confidence": metadata.get("temporal_confidence"),
                    "temporal_source_text": metadata.get("temporal_source_text"),
                    "evidence": [evidence],
                },
            }
        else:
            confidence = round(float(relation.confidence), 4)
            current["weight"] = max(current["weight"], confidence)
            current["metadata"]["confidence"] = max(current["metadata"]["confidence"], confidence)
            if evidence not in current["metadata"]["evidence"]:
                current["metadata"]["evidence"].append(evidence)

    def _find_entity_id(
        self,
        entity_map: Dict[Tuple[str, str], Dict[str, Any]],
        entity: Entity,
        definitions: List[TypeDefinition],
    ) -> Optional[str]:
        key = (
            self._canonical_type(entity.label, definitions, "ENTITY"),
            self._normalize_text(self._clean_entity_text(entity.text)),
        )
        if key in entity_map:
            return entity_map[key]["id"]
        normalized = key[1]
        matches = [item["id"] for (item_type, text), item in entity_map.items() if text == normalized]
        return matches[0] if len(matches) == 1 else None

    def _canonical_type(
        self,
        raw_value: str,
        definitions: Iterable[TypeDefinition],
        fallback: str,
    ) -> str:
        raw = str(raw_value or "").strip()
        raw_upper = raw.upper()
        for definition in definitions:
            candidates = [definition.name, definition.label, *definition.aliases]
            if any(candidate and raw.casefold() == candidate.casefold() for candidate in candidates):
                return definition.name
            if re.search(rf"(?<![A-Z0-9_]){re.escape(definition.name)}(?![A-Z0-9_])", raw_upper):
                return definition.name
        normalized = re.sub(r"[^A-Z0-9_]+", "_", raw_upper).strip("_")
        return normalized[:64] or fallback

    def _resolve_span(self, text: str, value: str, start: int, end: int) -> Tuple[int, int]:
        if 0 <= start < end <= len(text) and text[start:end].strip() == value.strip():
            return start, end
        position = text.find(value)
        if position < 0:
            position = text.casefold().find(value.casefold())
        if position < 0:
            return 0, min(len(text), max(1, len(value)))
        return position, position + len(value)

    def _clean_entity_text(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n，。；;：:,、\"'（）()[]【】")
        return cleaned or "未命名实体"

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    def _evidence_text(self, text: str, start: int, end: int) -> str:
        return text[max(0, start - 60) : min(len(text), end + 60)].strip()

    def _stable_id(self, prefix: str, *values: str) -> str:
        raw = "\x1f".join(str(value) for value in values)
        return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"

    def _safe_error(self, exc: Exception) -> str:
        raw = str(exc)
        lowered = raw.lower()
        if "insufficient account balance" in lowered or "insufficient_balance" in lowered:
            return "模型服务账户余额不足（HTTP 402），请充值后重试"
        if (
            "invalid api key" in lowered
            or "invalid_key" in lowered
            or "invalid authentication" in lowered
            or "invalid_authentication" in lowered
            or "error code: 401" in lowered
        ):
            return "Kimi 鉴权失败（HTTP 401）：请检查 API Key 是否来自当前区域对应的开放平台"
        if "rate limit" in lowered or "error code: 429" in lowered:
            return "模型服务请求过于频繁（HTTP 429），请稍后重试"
        return re.sub(
            r"(?i)(api[_ -]?key|authorization)[^,;\n]*",
            r"\1=<已隐藏>",
            raw,
        )[:500]
