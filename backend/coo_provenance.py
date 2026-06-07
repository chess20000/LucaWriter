"""COO v2 留名 + 防篡改校验。

走 git 模型（详见 COO.md §7-§8）：
- 留名 = 每条历史事件的 author 必填、自填、不验证、可重名。
- 防偷改 = 哈希链：每条事件含前一条的 event_hash。
- 不做签名：v2 已砍掉 Ed25519 与 META-INF/coo-keys.json。

build_history_event(identity, changed_files, previous_event_hash, event_type) → event dict
write_coo_with_history(raw_zip_bytes, identity, event_type) → new zip bytes
verify_coo_bytes(raw) → report
"""

import hashlib
import json
import os
import secrets
import time
import zipfile
from io import BytesIO


HISTORY_PATH = "META-INF/coo-history.jsonl"
# v2 载荷比对时排除的控制路径只有历史链本身（v1 的 coo-keys.json 已废弃）。
CONTROL_PATHS = {HISTORY_PATH}
PROVENANCE_VERSION = "coo-provenance-v2"


def _canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _event_for_hash(event):
    """返回去掉 event_hash 字段后的事件 dict，用于计算 event_hash（契约 §8.1）。"""
    clean = dict(event)
    clean.pop("event_hash", None)
    return clean


def _payload_file_hashes(zf):
    """返回包内所有载荷文件的 {path, sha256, size} 列表，按 path 升序。"""
    result = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name in CONTROL_PATHS or name.startswith("__MACOSX/"):
            continue
        raw = zf.read(info.filename)
        result.append({"path": name, "sha256": _sha256(raw), "size": len(raw)})
    return sorted(result, key=lambda item: item["path"])


def _read_history(zf):
    try:
        raw = zf.read(HISTORY_PATH).decode("utf-8")
    except KeyError:
        return []
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def default_user_name():
    return (
        os.environ.get("COO_USER_NAME")
        or os.environ.get("USERNAME")
        or os.environ.get("USER")
        or "unknown"
    )


def load_or_create_identity(path, client_name, client_version="", client_id_prefix="client", user_name=None):
    """加载或创建客户端身份（v2：无需密钥，只需 client_id + author 名）。

    返回 dict：{client_id, client_name, client_version, user_name, created_at}
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {
            "client_id": f"{client_id_prefix}_{secrets.token_hex(16)}",
            "client_name": client_name,
            "client_version": client_version,
            "user_name": user_name or default_user_name(),
            "created_at": time.time(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    if user_name:
        data["user_name"] = user_name
    data["client_name"] = client_name
    data["client_version"] = client_version
    return data


def build_history_event(identity, changed_files, previous_event_hash, event_type="export"):
    """构建一条历史事件（不含 event_hash，由调用方计算后回填）。

    返回 (event, event_hash)。
    """
    event = {
        "format_version": PROVENANCE_VERSION,
        "event_id": "evt_" + secrets.token_hex(16),
        "event_type": event_type,
        "author": identity.get("user_name") or default_user_name(),
        "client_name": identity.get("client_name", ""),
        "client_version": identity.get("client_version", ""),
        "client_id": identity.get("client_id", ""),
        "created_at": time.time(),
        "changed_files": changed_files,
        "previous_event_hash": previous_event_hash,
    }
    canonical = _canonical(_event_for_hash(event))
    event_hash = _sha256(canonical)
    event["event_hash"] = event_hash
    return event, event_hash


def _changed_files_equal(a, b):
    """比较两份 changed_files 列表是否完全一致（path/sha256/size 逐项相等）。"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x.get("path") != y.get("path") or x.get("sha256") != y.get("sha256") or x.get("size") != y.get("size"):
            return False
    return True


def write_coo_with_history(raw_zip_bytes, identity, event_type="export"):
    """给一个已打包好的 .coo ZIP 追加历史链并返回新 ZIP 字节。

    raw_zip_bytes: 不含 META-INF/ 的干净 ZIP 内容。
    identity: load_or_create_identity 返回的身份。
    event_type: "export" / "edit" 等。

    如果当前载荷与上一条事件的 changed_files 完全一致，则不追加新事件，
    直接返回原 ZIP 字节（避免无修改的重复导出产生冗余历史记录）。
    """
    src = zipfile.ZipFile(BytesIO(raw_zip_bytes), "r")
    history = _read_history(src)
    changed_files = _payload_file_hashes(src)

    # 如果文件没有任何变化，不追加重复事件
    if history:
        last_changed = history[-1].get("changed_files", [])
        if _changed_files_equal(last_changed, changed_files):
            src.close()
            return raw_zip_bytes

    previous_event_hash = history[-1].get("event_hash", "") if history else ""

    event, _ = build_history_event(identity, changed_files, previous_event_hash, event_type)
    history.append(event)

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            name = info.filename.replace("\\", "/")
            if name in CONTROL_PATHS:
                continue
            if info.is_dir():
                dst.writestr(name.rstrip("/") + "/", b"")
            else:
                dst.writestr(name, src.read(info.filename))
        # 写入历史链
        history_text = "\n".join(
            json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for e in history
        ) + "\n"
        dst.writestr(HISTORY_PATH, history_text)
    src.close()
    return out.getvalue()


def verify_coo_bytes(raw):
    report = {
        "ok": False,
        "reason": "",
        "history": [],
        "manifest": None,
        "current_files": [],
    }
    try:
        zf = zipfile.ZipFile(BytesIO(raw), "r")
    except zipfile.BadZipFile:
        report["reason"] = "不是有效 ZIP"
        return report
    try:
        # 条件 1：manifest 存在、可解析、是 coo、版本为 2
        try:
            report["manifest"] = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception:
            report["reason"] = "缺少或无法读取 manifest.json"
            return report
        if report["manifest"].get("format_name") != "coo":
            report["reason"] = "manifest.format_name 不是 coo"
            return report
        try:
            version = int(report["manifest"].get("format_version", 0))
        except (TypeError, ValueError):
            version = 0
        if version != 2:
            report["reason"] = "不支持的 COO 版本（仅支持 v2）"
            return report

        history = _read_history(zf)
        report["history"] = history
        report["current_files"] = _payload_file_hashes(zf)

        # 条件 2：至少一条事件
        if not history:
            report["reason"] = "缺少 COO 修改历史"
            return report

        previous = ""
        for idx, event in enumerate(history):
            canonical = _canonical(_event_for_hash(event))
            event_hash = _sha256(canonical)
            # 条件 3：event_hash 与内容自洽
            if event.get("event_hash") != event_hash:
                report["reason"] = f"第 {idx + 1} 条历史哈希不匹配"
                return report
            # 条件 4：previous_event_hash 链不断（首条为空）
            if event.get("previous_event_hash", "") != previous:
                report["reason"] = f"第 {idx + 1} 条历史链断裂"
                return report
            # 条件 5：author 必填（留名）
            if not str(event.get("author", "")).strip():
                report["reason"] = f"第 {idx + 1} 条历史缺少 author（必须留名）"
                return report
            previous = event_hash

        # 条件 6：最后一条事件的 changed_files == 当前包实际载荷（排除控制路径）
        last_files = sorted(
            history[-1].get("changed_files", []),
            key=lambda item: item.get("path", ""),
        )
        if last_files != report["current_files"]:
            report["reason"] = "当前文件和最后一条历史记录不一致"
            return report

        report["ok"] = True
        report["reason"] = "通过篡改校验"
        return report
    finally:
        zf.close()
