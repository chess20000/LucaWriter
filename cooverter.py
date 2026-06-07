#!/usr/bin/env python
import json
import os
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import main  # noqa: E402
import coo_provenance  # noqa: E402


VERSION = "0.1.0"
PEN_NAME = "Cooverter"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def _safe_name(value, fallback):
    return main._coo_safe_name(value, fallback)


def _cover_arcname(raw):
    return main._coo_cover_arcname(raw)


def _identity():
    home = Path.home() / ".cooverter" / "identity.json"
    return coo_provenance.load_or_create_identity(
        str(home),
        client_name="cooverter",
        client_version=VERSION,
        client_id_prefix="cooverter",
        user_name=PEN_NAME,
    )


def _normalize_parse_result(result, filename):
    cover_data = None
    book_title = Path(filename).stem
    err = None
    if len(result) == 4:
        chapters, book_title, err, cover_data = result
    elif len(result) == 3:
        chapters, book_title, err = result
    elif len(result) == 2:
        chapters, err = result
    else:
        raise ValueError("解析器返回值无法识别")
    if err:
        raise ValueError(err)
    if not chapters:
        raise ValueError("未解析出章节")
    return chapters, book_title or Path(filename).stem, cover_data


def _build_coo_from_source(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"文件不存在: {path}")
    ext = path.suffix.lower()
    if ext not in main.IMPORT_PARSERS:
        raise ValueError("仅支持 TXT / MD / DOCX / PDF / EPUB")
    raw = path.read_bytes()
    chapters, book_title, cover_data = _normalize_parse_result(main.IMPORT_PARSERS[ext](raw, path.name), path.name)
    work_uid = "coo_" + os.urandom(48).hex()
    book_id = "01_" + _safe_name(book_title, "book")
    exported_at = time.time()

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── 书目录: books/01_Title/chapters/ ──
        book_dir = f"books/{book_id}/"
        chapter_items = []
        for idx, chapter in enumerate(chapters, start=1):
            cid = str(chapter.get("id") or f"ch_{idx:05d}")
            safe = _safe_name(cid, f"ch_{idx:05d}")
            arc = f"{book_dir}chapters/{idx:05d}_{safe}.json"
            title = str(chapter.get("title") or f"第 {idx} 章")
            content = str(chapter.get("content") or "")
            payload = {
                "id": cid,
                "title": title,
                "content": content,
                "updated": exported_at,
            }
            zf.writestr(arc, json.dumps(payload, ensure_ascii=False, indent=2))
            chapter_items.append({
                "id": cid,
                "title": title,
                "order": idx,
                "path": arc,
                "summary_path": "",
                "word_count": len(content),
                "updated": exported_at,
            })

        # ── 封面 ──
        cover_arc = ""
        if cover_data:
            cover_arc = _cover_arcname(cover_data)
            zf.writestr(f"assets/{cover_arc}", cover_data)
            cover_arc = f"assets/{cover_arc}"

        # ── 顶层 manifest.json (v2) ──
        manifest = {
            "format_name": "coo",
            "format_version": 2,
            "work_uid": work_uid,
            "exported_at": exported_at,
            "producer": {
                "app_name": "cooverter",
                "app_version": VERSION,
            },
            "work": {
                "title": book_title,
                "author": PEN_NAME,
                "description": "",
                "language": "zh-CN",
                "created": exported_at,
                "updated": exported_at,
                "cover_file": cover_arc,
            },
            "books": [
                {
                    "id": book_id,
                    "title": book_title,
                    "order": 1,
                    "path": book_dir,
                    "cover_file": "",
                    "chapters": chapter_items,
                    "ai": {},
                }
            ],
            "lore": [],
            "reading_order": [],
            "shared": {
                "ai": {
                    "characters_path": "",
                    "world_settings_path": "",
                    "timeline_path": "",
                    "core_memory_path": "",
                    "kb_path": "",
                    "vector_db_path": "",
                }
            },
            "contains": {
                "books": True,
                "lore": False,
                "reading_order": True,
                "summaries": False,
                "knowledge_db": False,
                "vector_db": False,
                "chat_history": False,
                "personal_settings": False,
            },
            "provenance": {
                "history_path": coo_provenance.HISTORY_PATH,
            },
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return coo_provenance.write_coo_with_history(buf.getvalue(), _identity(), event_type="export")


def convert(path):
    path = Path(path).resolve()
    output = path.with_suffix(".coo")
    output.write_bytes(_build_coo_from_source(path))
    print(str(output))


def expose(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    report = coo_provenance.verify_coo_bytes(raw)
    if report["ok"]:
        print(f"{GREEN}PASS 篡改校验通过{RESET}")
    else:
        print(f"{RED}FAIL 篡改校验失败：{report['reason']}{RESET}")
    public_report = {
        "manifest": report.get("manifest"),
        "provenance": {
            "ok": report["ok"],
            "reason": report["reason"],
            "history": report.get("history", []),
            "current_files": report.get("current_files", []),
        },
    }
    print(json.dumps(public_report, ensure_ascii=False, indent=2, sort_keys=True))


def main_cli(argv):
    if len(argv) == 2 and argv[1] not in ("-h", "--help"):
        convert(argv[1])
        return 0
    if len(argv) == 3 and argv[1] == "expose":
        expose(argv[2])
        return 0
    print("Usage:")
    print("  cooverter <path>")
    print("  cooverter expose <path.coo>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv))
