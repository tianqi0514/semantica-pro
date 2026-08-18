import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from document_extraction_module.src.chunker import split_text
from document_extraction_module.src.batch import combine_results
from document_extraction_module.src.exporters import to_csv_text
from document_extraction_module.src.extractor import DocumentExtractionService
from document_extraction_module.src.models import ExtractionConfig
from document_extraction_module.src.settings_store import LLMSettingsStore
from document_extraction_module.src.storage import JobStore


ROOT = Path(__file__).resolve().parents[1]


class ChunkerTests(unittest.TestCase):
    def test_chunks_preserve_text_offsets(self):
        text = "第一段内容。\n\n第二段内容很长。\n\n第三段内容。"
        chunks = split_text(text, chunk_size=12, overlap=2)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(text[chunk.start:chunk.end], chunk.text)


class ExportTests(unittest.TestCase):
    def test_csv_contains_nodes_before_edges(self):
        result = {
            "entities": [{"id": "e1", "type": "ORG", "text": "甲公司", "confidence": 0.9, "metadata": {}}],
            "relationships": [{"id": "r1", "source": "e1", "target": "e1", "type": "RELATED", "weight": 0.8, "metadata": {}}],
        }
        rows = list(csv.DictReader(io.StringIO(to_csv_text(result).lstrip("\ufeff"))))
        self.assertEqual([row["kind"] for row in rows], ["node", "edge"])


class SecretHandlingTests(unittest.TestCase):
    def test_kimi_defaults_use_openai_compatible_provider(self):
        data = json.loads(
            (ROOT / "config" / "procurement-compliance.json").read_text(encoding="utf-8")
        )
        config = ExtractionConfig.model_validate(data)
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.model, "kimi-k3")
        self.assertEqual(config.base_url, "https://api.moonshot.cn/v1")

    def test_xiaomi_defaults_and_api_key_exclusion(self):
        data = json.loads(
            (ROOT / "config" / "procurement-compliance.json").read_text(encoding="utf-8")
        )
        data.update(
            {
                "method": "llm",
                "provider": "xiaomi",
                "model": "gpt-4.1-mini",
                "base_url": None,
                "api_key": "test-secret-that-must-not-be-exported",
            }
        )
        config = ExtractionConfig.model_validate(data)
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.model, "mimo-v2.5")
        self.assertEqual(config.base_url, "https://api.xiaomimimo.com/v1")
        self.assertNotIn("api_key", config.model_dump())
        self.assertNotIn("test-secret", config.model_dump_json())

    def test_provider_errors_are_localized_without_key_echo(self):
        service = DocumentExtractionService()
        balance = service._safe_error(RuntimeError("Insufficient account balance: sk-secret"))
        invalid = service._safe_error(RuntimeError("Invalid API Key: sk-secret"))
        self.assertIn("余额不足", balance)
        self.assertIn("鉴权失败", invalid)
        self.assertNotIn("sk-secret", balance + invalid)


class RegexPipelineTests(unittest.TestCase):
    def test_procurement_sample_extracts_graph(self):
        data = json.loads(
            (ROOT / "config" / "procurement-compliance.json").read_text(encoding="utf-8")
        )
        data["method"] = "regex"
        config = ExtractionConfig.model_validate(data)
        sample = ROOT / "samples" / "采购合规审查样例.txt"
        result = DocumentExtractionService().extract(sample, sample.name, config)
        self.assertGreaterEqual(result["statistics"]["entities"], 5)
        self.assertGreaterEqual(result["statistics"]["relationships"], 3)
        values = {(item["type"], item["text"]) for item in result["entities"]}
        self.assertIn(("DATE", "2026年8月12日"), values)
        self.assertNotIn(("ORGANIZATION", "李明是上海云启科技有限公司"), values)
        exported = json.loads(json.dumps(result, ensure_ascii=False))
        self.assertIn("entities", exported)
        self.assertIn("relationships", exported)

    def test_extractor_reports_real_chunk_progress(self):
        data = json.loads(
            (ROOT / "config" / "procurement-compliance.json").read_text(encoding="utf-8")
        )
        data["method"] = "regex"
        data["chunk_size"] = 500
        data["chunk_overlap"] = 20
        config = ExtractionConfig.model_validate(data)
        sample = ROOT / "samples" / "采购合规审查样例.txt"
        updates = []
        DocumentExtractionService().extract(sample, sample.name, config, updates.append)
        self.assertGreaterEqual(len(updates), 2)
        stages = [update.get("stage") for update in updates]
        self.assertEqual(stages[0], "loading_document")
        self.assertIn("splitting_text", stages)
        self.assertIn("extracting_entities", stages)
        self.assertIn("extracting_relations", stages)
        self.assertIn("merging_results", stages)
        self.assertEqual(stages[-1], "document_completed")
        self.assertEqual(updates[-1]["chunks_completed"], updates[-1]["chunks_total"])


class BatchResultTests(unittest.TestCase):
    def test_batch_combines_documents_and_deduplicates_graph(self):
        first = {
            "run": {"scenario": "测试", "method": "regex"},
            "documents": [{"id": "d1", "name": "a.txt"}],
            "entities": [{"id": "e1", "type": "ORG", "text": "甲公司", "confidence": 0.8, "metadata": {"source_file": "a.txt", "document_id": "d1", "evidence": [{"text": "甲公司"}]}}],
            "relationships": [],
            "statistics": {"chunks": 1, "chunks_processed": 1},
            "warnings": [],
        }
        second = {
            "run": {"scenario": "测试", "method": "regex"},
            "documents": [{"id": "d2", "name": "b.txt"}],
            "entities": [{"id": "e1", "type": "ORG", "text": "甲公司", "confidence": 0.9, "metadata": {"source_file": "b.txt", "document_id": "d2", "evidence": [{"text": "甲公司再次出现"}]}}],
            "relationships": [],
            "statistics": {"chunks": 1, "chunks_processed": 1},
            "warnings": [],
        }
        items = [
            {"index": 1, "source_name": "a.txt", "status": "completed"},
            {"index": 2, "source_name": "b.txt", "status": "completed"},
        ]
        combined = combine_results([("a.txt", first), ("b.txt", second)], items)
        self.assertEqual(combined["statistics"]["documents"], 2)
        self.assertEqual(combined["statistics"]["entities"], 1)
        self.assertEqual(combined["entities"][0]["confidence"], 0.9)
        self.assertEqual(combined["entities"][0]["metadata"]["source_files"], ["a.txt", "b.txt"])


class SettingsStoreTests(unittest.TestCase):
    def test_default_and_encrypted_key_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "settings.db"
            store = LLMSettingsStore(database, root / ".settings.key")
            self.assertEqual(store.public_settings()["model"], "kimi-k3")
            self.assertFalse(store.public_settings()["has_api_key"])

            secret = "test-kimi-key-never-plaintext"
            public = store.update(
                provider="kimi",
                display_name="Kimi K3",
                model="kimi-k3",
                base_url="https://api.moonshot.ai/v1",
                api_key=secret,
            )
            self.assertTrue(public["has_api_key"])
            self.assertNotIn("api_key", public)
            self.assertEqual(store.runtime_settings()["api_key"], secret)

            with sqlite3.connect(database) as connection:
                encrypted = connection.execute(
                    "SELECT encrypted_api_key FROM llm_settings WHERE id = 1"
                ).fetchone()[0]
            self.assertNotIn(secret.encode("utf-8"), bytes(encrypted))

            store.update(
                provider="kimi",
                display_name="Kimi K3",
                model="kimi-k3",
                base_url="https://api.moonshot.ai/v1",
                api_key=None,
            )
            self.assertEqual(store.runtime_settings()["api_key"], secret)

            store.update(
                provider="kimi",
                display_name="Kimi K3",
                model="kimi-k3",
                base_url="https://api.moonshot.ai/v1",
                clear_api_key=True,
            )
            self.assertIsNone(store.runtime_settings()["api_key"])


class JobStoreTests(unittest.TestCase):
    def test_status_is_persisted_listed_and_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job_id, _ = store.create()
            status = {
                "job_id": job_id,
                "status": "processing",
                "phase": "extracting",
                "total": 1,
                "completed": 0,
                "failed": 0,
                "percent": 0,
                "started_at": "2026-08-18T00:00:00+00:00",
                "updated_at": "2026-08-18T00:00:00+00:00",
                "items": [{"index": 1, "source_name": "a.txt", "status": "processing"}],
            }
            store.save_status(job_id, status)
            self.assertEqual(store.load_status(job_id)["status"], "processing")
            self.assertEqual(store.list_statuses(active_only=True)[0]["job_id"], job_id)
            self.assertEqual(store.recover_incomplete_jobs(), 1)
            recovered = store.load_status(job_id)
            self.assertEqual(recovered["status"], "failed")
            self.assertEqual(recovered["percent"], 100)
            self.assertIn("重启", recovered["items"][0]["error"])


if __name__ == "__main__":
    unittest.main()
