import base64
import json
import http.client
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import warnings
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock


TEMP_DATA = tempfile.TemporaryDirectory()
ROOT = Path(__file__).resolve().parents[1]
os.environ["DATA_DIR"] = TEMP_DATA.name
sys.path.insert(0, str(ROOT / "backend"))

import main as luca


def save_chapter(book_id, chapter_id, title, content):
    chapter_dir = Path(luca.BOOKS_DIR) / book_id / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    luca.save_json(
        str(chapter_dir / f"{chapter_id}.json"),
        {
            "id": chapter_id,
            "title": title,
            "content": content,
            "updated": time.time(),
        },
    )


def save_lore(work_id, lore_id, title, content):
    lore_dir = Path(luca.WORKS_DIR) / work_id / "lore"
    lore_dir.mkdir(parents=True, exist_ok=True)
    luca.save_json(
        str(lore_dir / f"{lore_id}.json"),
        {
            "id": lore_id,
            "title": title,
            "kind": "concept",
            "content": content,
            "updated": time.time(),
        },
    )


class LucaMergeTests(unittest.TestCase):
    def setUp(self):
        for path in (
            Path(luca.BOOKS_DIR),
            Path(luca.WORKS_DIR),
            Path(luca.MESSAGES_DIR),
        ):
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)

    def build_target_and_branch(self):
        work, first = luca._create_work(
            "目标标题",
            "目标第一卷",
            create_first_chapter=False,
        )
        work_id = work["id"]
        first_id = first["id"]
        first_meta = luca.get_book_meta(first_id)
        first_meta["book_uid"] = "coo_" + "a" * 96
        first_meta["chapter_order"] = ["ch_shared", "ch_target"]
        luca.save_json(
            str(Path(luca.BOOKS_DIR) / first_id / "meta.json"),
            first_meta,
        )
        save_chapter(first_id, "ch_shared", "共享章", "目标分支内容")
        save_chapter(first_id, "ch_target", "目标独有章", "目标独有")

        target_only = luca._create_child_book(
            work_id,
            "目标独有卷",
            create_first_chapter=False,
        )
        target_only_meta = luca.get_book_meta(target_only["id"])
        target_only_meta["book_uid"] = "coo_" + "c" * 96
        target_only_meta["chapter_order"] = ["ch_target_book"]
        luca.save_json(
            str(Path(luca.BOOKS_DIR) / target_only["id"] / "meta.json"),
            target_only_meta,
        )
        save_chapter(
            target_only["id"],
            "ch_target_book",
            "目标独有卷章节",
            "保留",
        )
        save_lore(work_id, "lore_shared", "共享设定", "目标设定")
        save_lore(work_id, "lore_target", "目标设定", "目标独有设定")
        work = luca.get_work_meta(work_id)
        work["work_uid"] = "coo_" + "f" * 96
        work["description"] = "目标简介"
        work["reading_order"] = [
            {"type": "chapter", "book": first_id, "chapter": "ch_shared"},
            {"type": "chapter", "book": first_id, "chapter": "ch_target"},
            {"type": "lore", "ref": "lore_target"},
            {
                "type": "chapter",
                "book": target_only["id"],
                "chapter": "ch_target_book",
            },
        ]
        luca.save_work_meta(work_id, work)
        target_raw = luca._build_coo_zip(work_id, "Target Author")

        branch_id, _, _ = luca._import_coo_zip(target_raw)
        branch = luca.get_work_meta(branch_id)
        branch["title"] = "导入分支标题"
        branch["description"] = "导入分支简介"
        luca.save_work_meta(branch_id, branch)
        branch_first = branch["book_ids"][0]
        branch_first_meta = luca.get_book_meta(branch_first)
        branch_first_meta["title"] = "导入第一卷"
        branch_first_meta["chapter_order"] = ["ch_shared", "ch_source"]
        luca.save_json(
            str(Path(luca.BOOKS_DIR) / branch_first / "meta.json"),
            branch_first_meta,
        )
        save_chapter(branch_first, "ch_shared", "共享章", "导入分支内容")
        save_chapter(branch_first, "ch_source", "导入独有章", "导入独有")
        source_only = luca._create_child_book(
            branch_id,
            "导入独有卷",
            create_first_chapter=False,
        )
        source_only_meta = luca.get_book_meta(source_only["id"])
        source_only_meta["book_uid"] = "coo_" + "d" * 96
        source_only_meta["chapter_order"] = ["ch_source_book"]
        luca.save_json(
            str(Path(luca.BOOKS_DIR) / source_only["id"] / "meta.json"),
            source_only_meta,
        )
        save_chapter(
            source_only["id"],
            "ch_source_book",
            "导入独有卷章节",
            "导入卷",
        )
        save_lore(branch_id, "lore_shared", "共享设定", "导入设定")
        save_lore(branch_id, "lore_source", "导入设定", "导入独有设定")
        branch = luca.get_work_meta(branch_id)
        branch["book_ids"] = [branch_first, source_only["id"]]
        branch["reading_order"] = [
            {"type": "lore", "ref": "lore_shared"},
            {"type": "chapter", "book": branch_first, "chapter": "ch_shared"},
            {"type": "chapter", "book": branch_first, "chapter": "ch_source"},
            {
                "type": "chapter",
                "book": source_only["id"],
                "chapter": "ch_source_book",
            },
        ]
        luca.save_work_meta(branch_id, branch)
        branch_raw = luca._build_coo_zip(branch_id, "Source Author")
        return work_id, first_id, target_only["id"], branch_raw

    def test_merge_preserves_unique_content_and_resets_generated_database(self):
        work_id, first_id, target_only_id, branch_raw = (
            self.build_target_and_branch()
        )
        shared = Path(luca.get_work_kb_dir(work_id))
        (shared / "kb.db").write_bytes(b"generated")
        summary_dir = (
            Path(luca.BOOKS_DIR) / first_id / "chapter_summaries"
        )
        summary_dir.mkdir(parents=True)
        (summary_dir / "ch_shared.md").write_text("generated", encoding="utf-8")

        imported_id, _, _ = luca._import_coo_zip(branch_raw)
        detail = luca._merge_imported_work(work_id, imported_id)
        work = detail["work"]
        self.assertEqual(work["title"], "导入分支标题")
        self.assertEqual(work["description"], "导入分支简介")
        self.assertTrue(work["needs_readthrough"])
        self.assertEqual(work["book_ids"][-1], target_only_id)

        first = luca.get_book_meta(first_id)
        self.assertEqual(first["title"], "导入第一卷")
        self.assertEqual(
            luca._read_chapter_file(first_id, "ch_shared")["content"],
            "导入分支内容",
        )
        self.assertEqual(
            luca._read_chapter_file(first_id, "ch_target")["content"],
            "目标独有",
        )
        self.assertEqual(
            luca._read_chapter_file(first_id, "ch_source")["content"],
            "导入独有",
        )
        lore = {
            item["id"]: item for item in luca._work_lore_items(work_id)
        }
        self.assertEqual(lore["lore_shared"]["content"], "导入设定")
        self.assertEqual(lore["lore_target"]["content"], "目标独有设定")
        self.assertEqual(lore["lore_source"]["content"], "导入独有设定")
        self.assertFalse((shared / "kb.db").exists())
        self.assertFalse(summary_dir.exists())

        merged_raw = luca._build_coo_zip(work_id, "Merge Author")
        report = luca.coo_provenance.verify_coo_bytes(merged_raw)
        self.assertTrue(report["ok"], report["reason"])
        self.assertEqual(report["history"][-1]["event_type"], "merge")
        with zipfile.ZipFile(BytesIO(merged_raw), "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(
                manifest["provenance"]["merge_sources_path"],
                "META-INF/coo-merge-sources.json",
            )
            sources = json.loads(
                archive.read("META-INF/coo-merge-sources.json")
            )
            self.assertTrue(sources[-1]["last_event_hash"])
        self.assertNotIn(
            "pending_history_event",
            luca.get_work_meta(work_id),
        )

    def test_different_work_uid_is_rejected(self):
        first, _ = luca._create_work("A", "A", create_first_chapter=False)
        second, _ = luca._create_work("B", "B", create_first_chapter=False)
        with self.assertRaisesRegex(ValueError, "work_uid"):
            luca._merge_imported_work(first["id"], second["id"])

    def test_failed_merge_restores_target_files(self):
        work_id, _, _, branch_raw = self.build_target_and_branch()

        def snapshot(path):
            root = Path(path)
            return {
                item.relative_to(root).as_posix(): item.read_bytes()
                for item in root.rglob("*")
                if item.is_file()
            }

        target = luca.get_work_meta(work_id)
        before_work = snapshot(Path(luca.WORKS_DIR) / work_id)
        before_books = {
            book_id: snapshot(Path(luca.BOOKS_DIR) / book_id)
            for book_id in target["book_ids"]
        }
        source_work_id, _, _ = luca._import_coo_zip(branch_raw)
        original_save_work_meta = luca.save_work_meta

        def fail_at_final_target_save(candidate_work_id, meta):
            if (
                candidate_work_id == work_id
                and meta.get("needs_readthrough")
            ):
                raise OSError("injected merge write failure")
            return original_save_work_meta(candidate_work_id, meta)

        with mock.patch.object(
            luca,
            "save_work_meta",
            side_effect=fail_at_final_target_save,
        ):
            with self.assertRaisesRegex(OSError, "injected"):
                luca._merge_imported_work(work_id, source_work_id)

        self.assertEqual(
            snapshot(Path(luca.WORKS_DIR) / work_id),
            before_work,
        )
        for book_id, expected in before_books.items():
            self.assertEqual(
                snapshot(Path(luca.BOOKS_DIR) / book_id),
                expected,
            )

    def test_readthrough_completion_clears_required_flag(self):
        work, _ = luca._create_work("A", "A", create_first_chapter=False)
        work["needs_readthrough"] = True
        luca.save_work_meta(work["id"], work)
        with mock.patch.object(
            luca.kb_pipeline,
            "do_readthrough_work",
        ), mock.patch.object(
            luca.kb_storage,
            "get_rt_state",
            return_value={"status": "done"},
        ):
            luca._do_work_readthrough_wrapper(
                work["id"],
                {"base_url": "http://localhost", "model": "test"},
            )
        refreshed = luca.get_work_meta(work["id"])
        self.assertFalse(refreshed["needs_readthrough"])
        self.assertGreater(refreshed["readthrough_at"], 0)

    def test_work_readthrough_rebuilds_database_and_vectors(self):
        work, book = luca._create_work(
            "Readthrough Work",
            "First Volume",
            create_first_chapter=False,
        )
        book_meta = luca.get_book_meta(book["id"])
        book_meta["chapter_order"] = ["chapter_one"]
        luca.save_json(
            str(Path(luca.BOOKS_DIR) / book["id"] / "meta.json"),
            book_meta,
        )
        save_chapter(
            book["id"],
            "chapter_one",
            "Chapter One",
            "Alice enters the old city.",
        )
        work = luca.get_work_meta(work["id"])
        work["reading_order"] = [
            {
                "type": "chapter",
                "book": book["id"],
                "chapter": "chapter_one",
            }
        ]
        work["needs_readthrough"] = True
        luca.save_work_meta(work["id"], work)

        structured = {
            "summary": "Alice entered the old city.",
            "entities": [
                {
                    "canonical_name": "Alice",
                    "type": "人物",
                    "aliases_in_chapter": [],
                    "facts": [
                        {
                            "fact": "Entered the old city",
                            "snippet": "Alice enters the old city.",
                        }
                    ],
                }
            ],
            "events": [],
            "foreshadowing_new": [],
            "foreshadowing_resolved": [],
            "rules": [],
        }

        class FakeEmbedding:
            backend_id = "test:readthrough"

            def embed(self, texts):
                return [[1.0, float(index + 1)] for index, _ in enumerate(texts)]

        with mock.patch.object(
            luca.kb_pipeline,
            "ai_read_chapter_structured",
            return_value=structured,
        ), mock.patch.object(
            luca.kb_pipeline,
            "get_embedding_backend",
            return_value=FakeEmbedding(),
        ):
            luca._do_work_readthrough_wrapper(
                work["id"],
                {"model": "test", "model_context_length": 32768},
            )

        refreshed = luca.get_work_meta(work["id"])
        self.assertFalse(refreshed["needs_readthrough"])
        self.assertEqual(
            luca.kb_storage.get_rt_state(work["id"])["status"],
            "done",
        )
        self.assertTrue(Path(luca.kb_storage.get_kb_path(work["id"])).exists())
        self.assertGreater(
            luca.kb_storage.embed_collection_count(work["id"]),
            0,
        )
        entities = luca.kb_storage.list_entities(work["id"])
        self.assertEqual(entities[0]["canonical_name"], "Alice")

    def test_remote_url_validation(self):
        self.assertEqual(
            luca._normalize_coobox_server_url(
                "https://example.test/coobox/"
            ),
            "https://example.test/coobox",
        )
        for value in (
            "ftp://example.test",
            "https://user:pass@example.test",
            "https://example.test/?token=secret",
            "not-a-url",
        ):
            with self.assertRaises(ValueError):
                luca._normalize_coobox_server_url(value)

    def test_duplicate_zip_path_and_legacy_password_are_rejected_or_scrubbed(self):
        work, book = luca._create_work(
            "A",
            "A",
            create_first_chapter=False,
        )
        raw = luca._build_coo_zip(work["id"], "Author")
        duplicate = BytesIO(raw)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "a") as archive:
                archive.writestr("manifest.json", b"{}")
        with self.assertRaisesRegex(ValueError, "重复路径"):
            luca._import_coo_zip(duplicate.getvalue())

        meta = luca.get_book_meta(book["id"])
        meta["coo_password"] = "legacy-secret"
        luca.save_json(
            str(Path(luca.BOOKS_DIR) / book["id"] / "meta.json"),
            meta,
        )
        luca._ensure_work_index()
        self.assertNotIn("coo_password", luca.get_book_meta(book["id"]))

    def test_removed_series_and_child_coo_http_endpoints_are_blocked(self):
        work, book = luca._create_work(
            "A",
            "A",
            create_first_chapter=False,
        )
        target_raw = luca._build_coo_zip(work["id"], "Author")
        branch_id, _, _ = luca._import_coo_zip(target_raw)
        branch = luca.get_work_meta(branch_id)
        branch["title"] = "HTTP 合并标题"
        luca.save_work_meta(branch_id, branch)
        branch_raw = luca._build_coo_zip(branch_id, "Branch Author")
        luca.save_json(
            luca.USERS_FILE,
            {
                "tester": {
                    "password": luca.hash_password("password"),
                    "created": time.time(),
                }
            },
        )
        token = luca.make_session("tester")
        server = luca._QuietThreadingHTTPServer(
            ("127.0.0.1", 0),
            luca.Handler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            headers = {
                "Content-Type": "application/json",
                "Content-Length": "2",
                "Cookie": "session=" + token,
                "X-Luca-Client": "tests",
            }
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/api/series/create", body="{}", headers=headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request(
                "POST",
                f"/api/book/{book['id']}/coo-remote",
                body="{}",
                headers=headers,
            )
            response = conn.getresponse()
            self.assertEqual(response.status, 410)
            response.read()
            conn.close()

            merge_body = json.dumps(
                {
                    "data": base64.b64encode(branch_raw).decode("ascii")
                }
            )
            merge_headers = dict(headers)
            merge_headers["Content-Length"] = str(len(merge_body.encode()))
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            conn.request(
                "POST",
                f"/api/work/{work['id']}/merge-coo",
                body=merge_body,
                headers=merge_headers,
            )
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["needs_readthrough"])
            conn.close()
            self.assertEqual(
                luca.get_work_meta(work["id"])["title"],
                "HTTP 合并标题",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

    def test_import_discards_generated_databases_and_marks_for_readthrough(self):
        work, _ = luca._create_work(
            "A",
            "A",
            create_first_chapter=False,
        )
        shared = Path(luca.get_work_kb_dir(work["id"]))
        (shared / "kb.db").write_bytes(b"untrusted-db")
        vector = shared / ".vector_db"
        vector.mkdir()
        (vector / "chroma.sqlite3").write_bytes(b"untrusted-vector-db")
        raw = luca._build_coo_zip(work["id"], "Author")
        imported_id, imported, _ = luca._import_coo_zip(raw)
        imported_shared = Path(luca.get_work_kb_dir(imported_id))
        self.assertFalse((imported_shared / "kb.db").exists())
        self.assertFalse((imported_shared / ".vector_db").exists())
        self.assertTrue(imported["needs_readthrough"])

    def test_sqlite_vector_store_query_filter_prune_and_clear(self):
        work, _ = luca._create_work(
            "Vector Work",
            "Volume",
            create_first_chapter=False,
        )
        work_id = work["id"]

        class FakeEmbedding:
            backend_id = "test:2d"

            def embed(self, texts):
                values = {
                    "alpha": [1.0, 0.0],
                    "beta": [0.0, 1.0],
                }
                return [values[text] for text in texts]

        storage = luca.kb_storage
        storage.embed_upsert_many(
            work_id,
            ["alpha", "beta"],
            ["Alpha document", "Beta document"],
            [[1.0, 0.0], [0.0, 1.0]],
            [
                {"source_type": "entity", "kind": "人物"},
                {"source_type": "entity", "kind": "地点"},
            ],
        )
        for chunk_id in ("alpha", "beta"):
            storage.register_embedding_chunk(
                work_id,
                chunk_id,
                "entity",
                chunk_id,
                "hash-" + chunk_id,
                "test:2d",
            )

        hits = storage.embed_query(
            work_id,
            "alpha",
            FakeEmbedding(),
            top_k=2,
        )
        self.assertEqual([item["id"] for item in hits], ["alpha", "beta"])
        filtered = storage.embed_query(
            work_id,
            "alpha",
            FakeEmbedding(),
            where={"kind": "地点"},
        )
        self.assertEqual([item["id"] for item in filtered], ["beta"])
        self.assertEqual(storage.embed_collection_count(work_id), 2)

        storage.prune_vector_entries(work_id, {"alpha"})
        self.assertEqual(storage.embed_collection_count(work_id), 1)
        self.assertIsNone(storage.get_embedding_chunk(work_id, "beta"))

        legacy = Path(luca.get_work_kb_dir(work_id)) / ".vector_db"
        legacy.mkdir()
        (legacy / "legacy.sqlite3").write_bytes(b"legacy")
        storage.embed_clear(work_id)
        self.assertEqual(storage.embed_collection_count(work_id), 0)
        self.assertFalse(legacy.exists())

    def test_legacy_series_is_migrated_to_one_work(self):
        first = {"id": "book_legacy_first"}
        second = {"id": "book_legacy_second"}
        for book, title in ((first, "First"), (second, "Second")):
            book_dir = Path(luca.BOOKS_DIR) / book["id"]
            (book_dir / "chapters").mkdir(parents=True)
            luca.save_json(
                str(book_dir / "meta.json"),
                {
                    "id": book["id"],
                    "title": title,
                    "chapter_order": [],
                    "created": 1,
                    "updated": 2,
                },
            )
        for book, chapter_id in ((first, "a"), (second, "b")):
            meta = luca.get_book_meta(book["id"])
            meta["chapter_order"] = [chapter_id]
            luca.save_json(
                str(Path(luca.BOOKS_DIR) / book["id"] / "meta.json"),
                meta,
            )
            save_chapter(book["id"], chapter_id, chapter_id.upper(), "text")

        legacy_id = "series_legacy_test"
        legacy_dir = Path(luca.BOOKS_DIR) / legacy_id
        legacy_dir.mkdir()
        luca.save_json(
            str(legacy_dir / "meta.json"),
            {
                "id": legacy_id,
                "type": "series",
                "title": "Legacy Work",
                "series_books": [first["id"], second["id"]],
                "created": 1,
                "updated": 2,
            },
        )
        (legacy_dir / "cover").write_bytes(b"legacy-cover")

        luca._ensure_work_index()
        migrated = [
            luca.get_work_meta(work_id)
            for work_id in os.listdir(luca.WORKS_DIR)
        ]
        migrated = [
            work for work in migrated
            if work and work.get("legacy_group_id") == legacy_id
        ]
        self.assertEqual(len(migrated), 1)
        work = migrated[0]
        self.assertEqual(work["book_ids"], [first["id"], second["id"]])
        self.assertEqual(
            work["reading_order"],
            [
                {"type": "chapter", "book": first["id"], "chapter": "a"},
                {"type": "chapter", "book": second["id"], "chapter": "b"},
            ],
        )
        self.assertEqual(
            (Path(luca.WORKS_DIR) / work["id"] / "cover").read_bytes(),
            b"legacy-cover",
        )
        self.assertFalse(legacy_dir.exists())
        self.assertEqual(
            luca.get_book_meta(first["id"])["work_id"],
            work["id"],
        )
        self.assertEqual(
            luca.get_book_meta(second["id"])["work_id"],
            work["id"],
        )


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        TEMP_DATA.cleanup()
