import base64
import hashlib
import json
import os
import secrets
import time
import zipfile
from io import BytesIO

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


HISTORY_PATH = "META-INF/coo-history.jsonl"
KEYS_PATH = "META-INF/coo-keys.json"
CONTROL_PATHS = {HISTORY_PATH, KEYS_PATH}
PROVENANCE_VERSION = "coo-provenance-v1"


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _b64d(data):
    return base64.b64decode(data.encode("ascii"))


def _canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _event_unsigned(event):
    clean = dict(event)
    clean.pop("event_hash", None)
    clean.pop("signature", None)
    return clean


def _payload_file_hashes(zf):
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
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _read_keys(zf):
    try:
        return json.loads(zf.read(KEYS_PATH).decode("utf-8"))
    except KeyError:
        return {"keys": {}}


def default_user_name():
    return (
        os.environ.get("COO_USER_NAME")
        or os.environ.get("COOBOX_USER_NAME")
        or os.environ.get("USERNAME")
        or os.environ.get("USER")
        or "unknown"
    )


def load_or_create_identity(path, client_name, client_version="", client_id_prefix="client", user_name=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        private_key = ed25519.Ed25519PrivateKey.generate()
        private_raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        data = {
            "client_id": f"{client_id_prefix}_{secrets.token_hex(16)}",
            "client_name": client_name,
            "client_version": client_version,
            "user_name": user_name or default_user_name(),
            "private_key": _b64(private_raw),
            "public_key": _b64(public_raw),
            "created_at": time.time(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    if user_name:
        data["user_name"] = user_name
    data["client_name"] = client_name
    data["client_version"] = client_version
    public_raw = _b64d(data["public_key"])
    data["public_key_id"] = "key_" + _sha256(public_raw)[:32]
    return data


def sign_coo_bytes(raw, identity, event_type="export"):
    src = zipfile.ZipFile(BytesIO(raw), "r")
    history = _read_history(src)
    keys_doc = _read_keys(src)
    keys = keys_doc.setdefault("keys", {})
    changed_files = _payload_file_hashes(src)
    previous_event_hash = history[-1].get("event_hash", "") if history else ""

    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(_b64d(identity["private_key"]))
    public_key_id = identity["public_key_id"]
    keys[public_key_id] = {
        "public_key": identity["public_key"],
        "client_id": identity.get("client_id", ""),
        "client_name": identity.get("client_name", ""),
        "client_version": identity.get("client_version", ""),
        "user_name": identity.get("user_name", ""),
        "created_at": identity.get("created_at", 0),
    }

    event = {
        "format_version": PROVENANCE_VERSION,
        "event_id": "evt_" + secrets.token_hex(16),
        "event_type": event_type,
        "user_name": identity.get("user_name") or default_user_name(),
        "client_name": identity.get("client_name", ""),
        "client_version": identity.get("client_version", ""),
        "client_id": identity.get("client_id", ""),
        "created_at": time.time(),
        "changed_files": changed_files,
        "previous_event_hash": previous_event_hash,
        "signature_alg": "Ed25519",
        "public_key_id": public_key_id,
    }
    unsigned = _canonical(event)
    event["event_hash"] = _sha256(unsigned)
    event["signature"] = _b64(private_key.sign(unsigned))
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
        dst.writestr(HISTORY_PATH, "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in history) + "\n")
        dst.writestr(KEYS_PATH, json.dumps(keys_doc, ensure_ascii=False, sort_keys=True, indent=2))
    src.close()
    return out.getvalue()


def verify_coo_bytes(raw):
    report = {
        "ok": False,
        "reason": "",
        "history": [],
        "keys": {},
        "manifest": None,
        "current_files": [],
    }
    try:
        zf = zipfile.ZipFile(BytesIO(raw), "r")
    except zipfile.BadZipFile:
        report["reason"] = "不是有效 ZIP"
        return report
    try:
        try:
            report["manifest"] = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception:
            report["reason"] = "缺少或无法读取 manifest.json"
            return report
        if report["manifest"].get("format_name") != "coo":
            report["reason"] = "manifest.format_name 不是 coo"
            return report
        history = _read_history(zf)
        keys_doc = _read_keys(zf)
        report["history"] = history
        report["keys"] = keys_doc.get("keys", {})
        report["current_files"] = _payload_file_hashes(zf)
        if not history:
            report["reason"] = "缺少 COO 修改历史"
            return report
        previous = ""
        for idx, event in enumerate(history):
            unsigned = _canonical(_event_unsigned(event))
            event_hash = _sha256(unsigned)
            if event.get("event_hash") != event_hash:
                report["reason"] = f"第 {idx + 1} 条历史哈希不匹配"
                return report
            if event.get("previous_event_hash", "") != previous:
                report["reason"] = f"第 {idx + 1} 条历史链断裂"
                return report
            key = report["keys"].get(event.get("public_key_id", ""))
            if not key:
                report["reason"] = f"第 {idx + 1} 条历史缺少公钥"
                return report
            try:
                public_key = ed25519.Ed25519PublicKey.from_public_bytes(_b64d(key["public_key"]))
                public_key.verify(_b64d(event.get("signature", "")), unsigned)
            except Exception:
                report["reason"] = f"第 {idx + 1} 条历史签名无效"
                return report
            previous = event_hash
        last_files = sorted(history[-1].get("changed_files", []), key=lambda item: item.get("path", ""))
        current_files = report["current_files"]
        if last_files != current_files:
            report["reason"] = "当前文件和最后一条签名记录不一致"
            return report
        report["ok"] = True
        report["reason"] = "通过篡改校验"
        return report
    finally:
        zf.close()
