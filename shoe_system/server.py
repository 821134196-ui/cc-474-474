"""HTTP 服务：JSON API + 静态页面 + 照片上传（仅标准库）。

角色由请求头 X-Actor-Name / X-Actor-Role 传入（仓管 keeper / 主管 supervisor / admin）。
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import services as svc
from .adapters import BadgeAdapter
from .db import connect

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
      ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
      ".ico": "image/x-icon", ".json": "application/json"}


def rows(seq) -> list[dict]:
    return [dict(r) for r in seq]


class App:
    def __init__(self, db_path: str, photo_dir: str):
        self.db_path = db_path
        self.photo_dir = Path(photo_dir)
        self.photo_dir.mkdir(parents=True, exist_ok=True)

    def conn(self):
        return connect(self.db_path)


# ---------------- multipart 解析（最小实现） ----------------

def parse_multipart(body: bytes, content_type: str) -> list[tuple[str, str, bytes, str | None]]:
    """返回 [(field_name, value_or_filename, data, filename)]。"""
    m = re.search(r"boundary=(?:" + r'"([^"]+)"|([\w\-]+))', content_type)
    if not m:
        return []
    boundary = (m.group(1) or m.group(2)).encode()
    delim = b"--" + boundary
    parts = []
    for chunk in body.split(delim):
        if not chunk or chunk in (b"--", b"--\r\n") or chunk == b"--" + b"\r\n":
            continue
        chunk = chunk.strip(b"\r\n")
        if b"\r\n\r\n" not in chunk:
            continue
        header_blob, data = chunk.split(b"\r\n\r\n", 1)
        headers = header_blob.decode("utf-8", "replace")
        cd = re.search(r'Content-Disposition: form-data; name="([^"]+)"(?:;\s*filename="([^"]*)")?',
                       headers)
        if not cd:
            continue
        name, filename = cd.group(1), cd.group(2)
        data = data.rstrip(b"\r\n")
        if filename is not None:
            parts.append((name, filename, data, filename))
        else:
            parts.append((name, data.decode("utf-8"), None, None))
    return parts


class Handler(BaseHTTPRequestHandler):
    app: App = None
    server_version = "ShoeSystem/1.0"

    # ---- 基础收发 ----
    def log_message(self, fmt, *args):
        pass  # 安静运行；业务操作有审计日志

    def _send_json(self, obj, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, msg: str, status: int = 400):
        self._send_json({"ok": False, "error": msg}, status)

    def _actor(self, body_fields: dict | None = None) -> dict:
        name = self.headers.get("X-Actor-Name") or (body_fields or {}).get("actor_name") or "未登录"
        role = self.headers.get("X-Actor-Role") or (body_fields or {}).get("actor_role") or "keeper"
        if role not in (svc.ROLE_KEEPER, svc.ROLE_SUPERVISOR, svc.ROLE_ADMIN):
            role = svc.ROLE_KEEPER
        return {"name": name, "role": role}

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            d = json.loads(raw.decode("utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            raise ValueError("请求体不是合法 JSON")

    def _read_form(self) -> tuple[dict, dict[str, tuple[str, bytes]]]:
        """返回 fields, files{field: (filename, bytes)}。"""
        length = int(self.headers.get("Content-Length", 0))
        ctype = self.headers.get("Content-Type", "")
        body = self.rfile.read(length) if length else b""
        fields, files = {}, {}
        if "multipart/form-data" in ctype:
            for name, value, data, filename in parse_multipart(body, ctype):
                if filename is None:
                    fields[name] = value
                elif data:
                    files[name] = (filename, data)
        else:
            try:
                fields = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                fields = {}
        return fields, files

    # ---- 静态资源 ----
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                return self._api_get(path)
            if path.startswith("/photos/"):
                return self._serve_photo(path[len("/photos/"):])
            return self._serve_static(path)
        except svc.BusinessError as e:
            return self._err(str(e))
        except Exception as e:
            return self._err(f"服务器错误：{e}", 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            return self._api_post(path)
        except svc.BusinessError as e:
            return self._err(str(e))
        except ValueError as e:
            return self._err(str(e))
        except Exception as e:
            return self._err(f"服务器错误：{e}", 500)

    def _serve_static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (WEB_DIR / rel).resolve()
        if not str(f).startswith(str(WEB_DIR)) or not f.is_file():
            f = WEB_DIR / "index.html"
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CT.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_photo(self, name: str):
        f = (self.app.photo_dir / name).resolve()
        if not str(f).startswith(str(self.app.photo_dir.resolve())) or not f.is_file():
            return self._err("照片不存在", 404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- GET API ----
    def _api_get(self, path: str):
        c = self.app.conn()
        try:
            if path == "/api/state":
                return self._send_json({"ok": True, "data": self._full_state(c)})
            if path == "/api/employees":
                return self._send_json({"ok": True, "data": rows(svc.list_employees(c))})
            m = re.fullmatch(r"/api/employees/([\w\-]+)", path)
            if m:
                eid = m.group(1)
                emp = svc.get_employee(c, eid)
                prof = svc.latest_profile(c, eid)
                data = {
                    "employee": dict(emp),
                    "profile": dict(prof) if prof else None,
                    "eligible": svc.eligible_specs(c, eid),
                    "shoes": rows(svc.list_issuances(c, eid)),
                    "fittings": rows(c.execute(
                        "SELECT * FROM fittings WHERE employee_id=? ORDER BY id DESC", (eid,))),
                    "badge": BadgeAdapter.swipecard(emp["badge_no"]),
                }
                return self._send_json({"ok": True, "data": data})
            m = re.fullmatch(r"/api/renewals/(\d+)", path)
            if m:
                r, photos, iss = svc.get_renewal(c, int(m.group(1)))
                return self._send_json({"ok": True, "data": {
                    "renewal": r, "photos": photos, "issuance": iss}})
            return self._err("未知接口", 404)
        finally:
            c.close()

    def _full_state(self, c) -> dict:
        return {
            "today": date.today().strftime("%Y-%m-%d"),
            "employees": rows(svc.list_employees(c)),
            "matrix": svc.inventory_matrix(c),
            "issuances": rows(svc.list_issuances(c, status="active")),
            "due": rows(svc.due_queue(c)),
            "waitlist": rows(svc.list_waitlist(c)),
            "renewals": rows(svc.list_renewals(c)),
            "returns": rows(svc.list_returns(c)),
            "disposals": svc.list_disposals(c),
            "reminders": rows(svc.list_reminders(c)),
        }

    # ---- POST API ----
    def _api_post(self, path: str):
        # 含文件上传的接口先取 form
        c = self.app.conn()
        try:
            if path == "/api/fittings":
                b = self._read_json(); actor = self._actor(b)
                fid = svc.record_fitting(
                    c, b["employee_id"], b["model_code"], float(b["tried_size"]),
                    b.get("left_fit", ""), b.get("right_fit", ""),
                    b["result"], b.get("note", ""), actor)
                return self._send_json({"ok": True, "id": fid})

            if path == "/api/issues":
                b = self._read_json()
                iid = svc.issue_shoe(
                    c, b["employee_id"], b["model_code"], float(b["size"]),
                    b["warehouse"], self._actor(b))
                return self._send_json({"ok": True, "id": iid})

            if path == "/api/receive":
                b = self._read_json()
                bid = svc.receive_stock(
                    c, b["model_code"], float(b["size"]), b["warehouse"],
                    int(b["qty"]), self._actor(b))
                return self._send_json({"ok": True, "id": bid})

            if path == "/api/profile":
                b = self._read_json()
                pid = svc.upsert_profile(
                    c, b["employee_id"], float(b["left_size"]), float(b["right_size"]),
                    b.get("width", "M"), b.get("note", ""), self._actor(b))
                return self._send_json({"ok": True, "id": pid})

            if path == "/api/transfers":
                b = self._read_json()
                rid = svc.transfer_post(
                    c, b["employee_id"], b["to_post"], b["to_grade"], self._actor(b))
                return self._send_json({"ok": True, "renewal_id": rid})

            if path == "/api/renewals":
                fields, files = self._read_form()
                actor = self._actor(fields)
                photo_paths = {}
                for slot in ("sole", "toe", "upper"):
                    if slot in files:
                        filename, data = files[slot]
                        ext = Path(filename).suffix.lower() or ".png"
                        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                            ext = ".png"
                        name = f"damage_{fields['employee_id']}_{slot}_{uuid.uuid4().hex[:8]}{ext}"
                        (self.app.photo_dir / name).write_bytes(data)
                        photo_paths[slot] = name
                rid = svc.create_renewal(
                    c, int(fields["issuance_id"]), fields["kind"],
                    fields.get("reason", ""), photo_paths or None, actor)
                return self._send_json({"ok": True, "id": rid})

            m = re.fullmatch(r"/api/renewals/(\d+)/decide", path)
            if m:
                b = self._read_json()
                svc.decide_renewal(
                    c, int(m.group(1)), bool(b.get("approve")), self._actor(b),
                    grant_exemption=bool(b.get("grant_exemption")),
                    exemption_reason=b.get("exemption_reason", ""))
                return self._send_json({"ok": True})

            m = re.fullmatch(r"/api/renewals/(\d+)/pickup", path)
            if m:
                b = self._read_json()
                new_id = svc.pickup_renewal(
                    c, int(m.group(1)), b["model_code"], float(b["size"]),
                    b["warehouse"], b["old_condition"], self._actor(b))
                return self._send_json({"ok": True, "id": new_id})

            m = re.fullmatch(r"/api/returns/(\d+)/inspect", path)
            if m:
                b = self._read_json()
                svc.inspect_return(
                    c, int(m.group(1)), bool(b.get("passed")),
                    b.get("note", ""), self._actor(b))
                return self._send_json({"ok": True})

            m = re.fullmatch(r"/api/disposals/(\d+)/execute", path)
            if m:
                receipt = svc.execute_disposal(c, int(m.group(1)), self._actor(self._read_json()))
                return self._send_json({"ok": True, "receipt_no": receipt})

            if path == "/api/reminders/tick":
                result = svc.generate_reminders(c)
                return self._send_json({"ok": True, "data": result})

            return self._err("未知接口", 404)
        finally:
            c.close()


def serve(db_path: str, photo_dir: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    Handler.app = App(db_path, photo_dir)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"劳保鞋发放换领系统：http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
