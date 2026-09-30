"""HTTP 应用层（标准库 http.server）。

路由把表单动作转交给 ShoeService；会话用签名 Cookie 之外的简单服务端 session id
（演示系统，内存 session 表）。
"""
from __future__ import annotations

import cgi
import http.cookies
import io
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config
from ..adapters.door import MockDoorAdapter
from ..db import get_connection
from ..services import PermissionDenied, ServiceError, ShoeService, days_to_expire
from ..seed import PHOTO_SVG, PHOTO_SLOT_LABELS, save_photo
from . import templates as T


class App:
    def __init__(self, db_path=None, photo_dir=None):
        self.db_path = db_path or config.DB_PATH
        self.photo_dir = photo_dir or config.PHOTO_DIR
        self.sessions: dict[str, dict] = {}

    # ---- 会话 -----------------------------------------------------------

    def new_session(self, role: str) -> str:
        sid = uuid.uuid4().hex
        actor = "M01 周主管" if role == "supervisor" else "K01 钱仓管"
        self.sessions[sid] = {"role": role, "actor": actor}
        return sid

    def flash_next(self, sid: str, msg: str | None):
        if sid and sid in self.sessions:
            self.sessions[sid]["flash"] = msg

    def svc(self):
        return ShoeService(self.db_path)

    # ---- 主入口 ---------------------------------------------------------

    def handle(self, method: str, path: str, headers, body: bytes):
        """返回 (status, headers_dict, body_bytes)。"""
        url = urlparse(path)
        route = url.path
        qs = parse_qs(url.query)

        cookie = http.cookies.SimpleCookie(headers.get("Cookie", ""))
        sid = cookie.get("sid")
        session = self.sessions.get(sid.value) if sid else None

        if route.startswith("/static/"):
            return self.static(route)
        if route.startswith("/photos/"):
            return self.photo(route)

        if not session:
            if route == "/login" and method == "POST":
                form = parse_qs(body.decode("utf-8"))
                role = form.get("role", ["storekeeper"])[0]
                if role not in config.ROLES:
                    role = "storekeeper"
                nsid = self.new_session(role)
                return self.redirect("/", {"Set-Cookie": f"sid={nsid}; HttpOnly; Path=/"})
            actors = [("supervisor", "M01 周主管"), ("storekeeper", "K01 钱仓管")]
            return self.html(T.view_login(actors))

        if route == "/logout" and method == "POST":
            if sid:
                self.sessions.pop(sid.value, None)
            return self.redirect("/login", {"Set-Cookie": "sid=; Max-Age=0; Path=/"})

        flash = None
        try:
            resp = self.route(method, route, qs, body, headers, session)
            if isinstance(resp, tuple) and len(resp) == 2 and resp[0] == "redirect":
                return self.redirect(resp[1])
            return self.html(resp)
        except PermissionDenied as exc:
            flash = f"✗ 权限不足：{exc}"
        except ServiceError as exc:
            flash = f"✗ {exc}"
        except ValueError as exc:
            flash = f"✗ 输入有误：{exc}"

        # POST 失败：闪存消息后回来源页（避免重复提交，也不把 POST 路由当 GET 渲染）
        if method == "POST":
            self.flash_next(sid.value if sid else "", flash)
            back = headers.get("Referer", "/")
            return self.redirect(back)
        return self.html(T.layout("出错", "home",
                                  f'<div class="flash bad">{T.e(flash)}</div>', session),
                          flash=flash)

    # ---- 路由 -----------------------------------------------------------

    def route(self, method, route, qs, body, headers, session):
        actor = session["actor"]
        role = session["role"]

        if route == "/" and method == "GET":
            return self.page_dashboard(session)
        if route == "/employees" and method == "GET":
            return self.page_employees(session)
        m = re.fullmatch(r"/employees/(E\d+)", route)
        if m and method == "GET":
            return self.page_employee(m.group(1), session)
        m = re.fullmatch(r"/employees/(E\d+)/profile", route)
        if m and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.upsert_profile(actor, role, m.group(1),
                                 f["size_left"], f["size_right"], f.get("width", "M"),
                                 f.get("note", ""))
            return ("redirect", f"/employees/{m.group(1)}")
        m = re.fullmatch(r"/employees/(E\d+)/position", route)
        if m and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.change_position(actor, role, m.group(1), f["position"], f["level"])
            return ("redirect", f"/employees/{m.group(1)}")

        if route == "/inventory" and method == "GET":
            return self.page_inventory(qs.get("wh", ["A库"])[0], session)
        if route == "/inventory/restock" and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.restock(actor, role, f["warehouse"], f["model_code"], f["size"], int(f["qty"]))
            return ("redirect", f"/inventory?wh={f['warehouse']}")

        if route == "/fitting" and method == "GET":
            return self.page_fitting(qs.get("emp_no", [None])[0], session)
        if route == "/fitting/start" and method == "POST":
            f = self.form(body)
            rid = f.get("renewal_id")
            with self.svc() as s:
                fid = s.start_fitting(actor, role, f["emp_no"], f["model_code"],
                                      f["size_left"], f["size_right"],
                                      int(rid) if rid else None)
            return ("redirect", f"/fittings/{fid}")
        m = re.fullmatch(r"/fittings/(\d+)", route)
        if m and method == "GET":
            return self.page_fitting_detail(int(m.group(1)), session)
        m = re.fullmatch(r"/fittings/(\d+)/adjust", route)
        if m and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.adjust_fitting(actor, role, int(m.group(1)), f["size_left"], f["size_right"])
            return ("redirect", f"/fittings/{m.group(1)}")
        m = re.fullmatch(r"/fittings/(\d+)/confirm", route)
        if m and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                iid = s.confirm_fitting(actor, role, int(m.group(1)), f["warehouse"])
            return ("redirect", f"/issues/{iid}")

        if route == "/due" and method == "GET":
            return self.page_due(session)
        if route == "/renewals/due" and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                rid = s.create_due_renewal(actor, role, f["emp_no"])
                issue = s.active_issue(f["emp_no"])
            # 到期单批准后进入旧鞋回收环节：交回即可试新鞋
            return ("redirect", f"/returns/collect?issue_id={issue['id']}&renewal_id={rid}")

        if route == "/approvals" and method == "GET":
            return self.page_approvals(session)
        m = re.fullmatch(r"/renewals/(\d+)/approve", route)
        if m and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.approve_renewal(actor, role, int(m.group(1)),
                                  f.get("approved") == "1", f.get("note", ""))
            return ("redirect", "/approvals")
        if route == "/damage/new" and method == "POST":
            return self.post_damage(body, headers, session)

        if route == "/returns" and method == "GET":
            return self.page_returns(session)
        if route == "/returns/collect" and method == "GET":
            return self.page_collect(qs, session)
        if route == "/returns/collect" and method == "POST":
            return self.post_collect(body, session)
        m = re.fullmatch(r"/returns/(\d+)/inspect", route)
        if m and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.cleaning_inspect(actor, role, int(m.group(1)), f.get("passed") == "1")
            return ("redirect", "/returns")
        m = re.fullmatch(r"/returns/(\d+)/exemption", route)
        if m and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.approve_exemption(actor, role, int(m.group(1)), f.get("approved") == "1")
            return ("redirect", "/approvals")

        if route == "/disposal/create" and method == "POST":
            f = self.form_multi(body)
            ids = [int(x) for x in f.get("ids", [])]
            warehouse = f.get("warehouse", ["A库"])[0]
            with self.svc() as s:
                s.create_disposal_batch(actor, role, warehouse, ids)
            return ("redirect", "/returns")
        m = re.fullmatch(r"/disposal/(\d+)/dispatch", route)
        if m and method == "POST":
            with self.svc() as s:
                s.dispatch_disposal(actor, role, int(m.group(1)))
            return ("redirect", "/returns")

        if route == "/waitlist" and method == "GET":
            return self.page_waitlist(session)
        if route == "/waitlist/join" and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                s.join_waitlist(actor, role, f["emp_no"], f["model_code"],
                                f["size_left"], f["size_right"])
            return ("redirect", "/waitlist")
        m = re.fullmatch(r"/waitlist/(\d+)/cancel", route)
        if m and method == "POST":
            with self.svc() as s:
                s.cancel_waitlist(actor, role, int(m.group(1)))
            return ("redirect", "/waitlist")

        m = re.fullmatch(r"/transfers/(E\d+)/settle", route)
        if m and method == "POST":
            f = self.form(body)
            mode = f.get("mode", "reusable")
            with self.svc() as s:
                if mode == "reusable":
                    s.settle_transfer(actor, role, m.group(1), True, condition="reusable")
                elif mode == "scrap":
                    s.settle_transfer(actor, role, m.group(1), True, condition="scrap")
                else:
                    s.settle_transfer(actor, role, m.group(1), False, exemption_reason="lost")
            return ("redirect", f"/employees/{m.group(1)}")

        if route == "/door" and method == "GET":
            return self.html_page(T.view_door(None), "门禁模拟", "door", session)
        if route == "/door/swipe" and method == "POST":
            f = self.form(body)
            with self.svc() as s:
                result = MockDoorAdapter(s).swipe(f["emp_no"])
            return self.html_page(T.view_door(result), "门禁模拟", "door", session)

        m = re.fullmatch(r"/issues/(\d+)", route)
        if m and method == "GET":
            with self.svc() as s:
                issue = s.get_issue(int(m.group(1)))
                rets = s.list_returns()
                rets = [r for r in rets if r["issue_id"] == int(m.group(1))]
            return self.html_page(T.view_issue(issue, rets),
                                  f"发放单 {issue['issue_no']}", "fitting", session)

        return self.html_page('<div class="flash bad">✗ 页面不存在</div>', "404", "home", session)

    # ---- 页面数据装配 ---------------------------------------------------

    def html_page(self, inner, title, nav_key, session, flash=None):
        if flash is None:
            flash = session.pop("flash", None)
        return T.layout(title, nav_key, inner, session, flash)

    def page_dashboard(self, session):
        with self.svc() as s:
            emps = s.list_employees()
            actives = s.list_active_issues()
            due_rows = [r for r in actives
                        if days_to_expire(r["expire_date"]) <= config.DUE_SOON_DAYS]
            renew_pending = len(s.list_renewals("requested"))
            exemptions = [r for r in s.list_returns()
                          if r["exemption"] == 1 and r["approve_status"] == "pending"]
            wait = s.list_waitlist("waiting")
            cleaning = s.list_returns("cleaning_inspection")
            scrap = s.list_returns("disposal_pending")
            reminders = s.list_reminders(20)
        d = {
            "emp_count": len(emps), "active_count": len(actives),
            "due_count": len(due_rows), "renew_pending": renew_pending,
            "exempt_pending": len(exemptions), "wait_count": len(wait),
            "cleaning_count": len(cleaning), "scrap_count": len(scrap),
            "reminders": reminders, "due_rows": due_rows,
        }
        return self.html_page(T.view_dashboard(d), "总览", "home", session)

    def page_employees(self, session):
        with self.svc() as s:
            emps = s.list_employees()
            rows = []
            for e in emps:
                p = s.get_profile(e["emp_no"])
                a = s.active_issue(e["emp_no"])
                rows.append({
                    **e,
                    "size_left": p["size_left"] if p else None,
                    "size_right": p["size_right"] if p else None,
                    "width": p["width"] if p else "",
                    "model_code": a["model_code"] if a else "",
                    "pair_tag": a["pair_tag"] if a else "",
                    "expire_date": a["expire_date"] if a else "",
                    "transfer_pending": s.pending_transfer(e["emp_no"]),
                })
        return self.html_page(T.view_employees(rows), "员工资格", "employees", session)

    def page_employee(self, emp_no, session):
        with self.svc() as s:
            d = {
                "emp": s.get_employee(emp_no),
                "profile": s.get_profile(emp_no),
                "active": s.active_issue(emp_no),
                "eligible": s.eligible_models(s.get_employee(emp_no)["level"]),
                "transfer": s.pending_transfer(emp_no),
                "renewals": [r for r in s.list_renewals() if r["emp_no"] == emp_no][:5],
                "session": session,
            }
        return self.html_page(T.view_employee(d), f"员工 {emp_no}", "employees", session)

    def page_inventory(self, wh, session):
        with self.svc() as s:
            d = {"wh": wh, "models": s.list_models(),
                 "matrix": s.inventory_matrix(wh), "session": session}
        return self.html_page(T.view_inventory(d), "库存矩阵", "inventory", session)

    def page_fitting(self, emp_no, session):
        with self.svc() as s:
            emps = s.list_employees()
            d = {"emps": emps, "emp_no": emp_no, "session": session}
            if emp_no:
                emp = s.get_employee(emp_no)
                p = s.get_profile(emp_no)
                active = s.active_issue(emp_no)
                transfer = s.pending_transfer(emp_no)
                blocked = None
                renewal = None
                if transfer:
                    blocked = "岗位调动旧鞋未结算"
                elif active:
                    renewal = s.conn.execute(
                        "SELECT * FROM renewals WHERE old_issue_id=? AND status='approved' ORDER BY id DESC LIMIT 1",
                        (active["id"],)).fetchone()
                    renewal = dict(renewal) if renewal else None
                    if renewal:
                        ret = s.conn.execute(
                            "SELECT * FROM returns WHERE issue_id=? ORDER BY id DESC LIMIT 1",
                            (active["id"],)).fetchone()
                        ok = ret and (ret["returned"] == 1
                                      or (ret["exemption"] == 1 and ret["approve_status"] == "approved"))
                        if not ok:
                            blocked = "换领已批准，但旧鞋未交回且豁免未批准"
                d.update(emp=emp, profile=p,
                         eligible=s.eligible_models(emp["level"]),
                         active=active, renewal=renewal,
                         blocked=blocked, transfer=transfer)
            recent = [dict(r) for r in s.conn.execute(
                """SELECT * FROM fittings WHERE date(created_at)=date('now','localtime')
                   ORDER BY id DESC LIMIT 10""")]
            d["fitting_list"] = T.fitting_list_html(recent)
        return self.html_page(T.view_fitting_start(d), "试穿发放", "fitting", session)

    def page_fitting_detail(self, fid, session):
        with self.svc() as s:
            f = s.get_fitting(fid)
            emp = s.get_employee(f["emp_no"])
        return self.html_page(T.view_fitting_detail({"f": f, "emp": emp}),
                              f"试穿单 #{fid}", "fitting", session)

    def page_due(self, session):
        with self.svc() as s:
            rows = []
            for i in s.list_active_issues():
                d = days_to_expire(i["expire_date"])
                if d > config.DUE_SOON_DAYS:
                    continue
                rn = s.conn.execute(
                    "SELECT status FROM renewals WHERE old_issue_id=? AND status IN ('requested','approved','completed') ORDER BY id DESC LIMIT 1",
                    (i["id"],)).fetchone()
                rows.append({**i, "renewal_status": rn["status"] if rn else ""})
        return self.html_page(T.view_due(rows), "到期队列", "due", session)

    def page_approvals(self, session):
        with self.svc() as s:
            d = {"renewals": [r for r in s.list_renewals()
                              if r["reason"] in ("damage", "early_wear")],
                 "exemptions": [r for r in s.list_returns() if r["exemption"] == 1],
                 "session": session}
        return self.html_page(T.view_approvals(d), "损坏审批", "approvals", session)

    def page_returns(self, session):
        with self.svc() as s:
            d = {"all": s.list_returns(),
                 "cleaning": s.list_returns("cleaning_inspection"),
                 "scrap": s.list_returns("disposal_pending"),
                 "batches": s.list_disposal_batches()}
        return self.html_page(T.view_returns(d), "旧鞋处置", "returns", session)

    def page_collect(self, qs, session):
        issue_id = int(qs["issue_id"][0])
        renewal_id = int(qs.get("renewal_id", [0])[0]) or None
        with self.svc() as s:
            issue = s.get_issue(issue_id)
        body = f"""
{T.card(f"旧鞋回收 · {issue['pair_tag']}（{issue['emp_no']} {issue['name']}）", f'''
<p class="muted-txt">领取新鞋前必须交回旧鞋，或登记遗失豁免（主管审批）。
鞋款 {issue['model_code']}，尺码 {issue['size_left']}/{issue['size_right']}，仓库 {issue['warehouse']}。</p>
<form method="post" action="/returns/collect" class="collect">
  <input type="hidden" name="issue_id" value="{issue_id}">
  <input type="hidden" name="renewal_id" value="{renewal_id or ""}">
  <div class="collect-opts">
    <button name="mode" value="reusable" class="btn primary">交回旧鞋：仍可使用（进入清洁复检）</button>
    <button name="mode" value="scrap" class="btn danger">交回旧鞋：已损坏报废（进入待处置批次）</button>
    <button name="mode" value="lost" class="btn ghost">旧鞋遗失：登记豁免（主管审批后才能领新鞋）</button>
  </div>
</form>''')}"""
        return self.html_page(body, "旧鞋回收", "returns", session)

    def post_collect(self, body, session):
        f = self.form(body)
        actor, role = session["actor"], session["role"]
        issue_id = int(f["issue_id"])
        renewal_id = int(f["renewal_id"]) if f.get("renewal_id") else None
        mode = f.get("mode", "reusable")
        with self.svc() as s:
            if mode == "reusable":
                s.register_return(actor, role, issue_id, True, condition="reusable",
                                  renewal_id=renewal_id)
            elif mode == "scrap":
                s.register_return(actor, role, issue_id, True, condition="scrap",
                                  renewal_id=renewal_id)
            else:
                s.register_return(actor, role, issue_id, False, exemption_reason="lost",
                                  renewal_id=renewal_id)
                if role != "supervisor":
                    return ("redirect", "/approvals")
        # 交回完成 -> 回试穿页继续领新鞋
        with self.svc() as s:
            issue = s.get_issue(issue_id)
        return ("redirect", f"/fitting?emp_no={issue['emp_no']}")

    def page_waitlist(self, session):
        with self.svc() as s:
            rows = s.list_waitlist()
            emps = s.list_employees()
            models = s.list_models()
        return self.html_page(T.view_waitlist(rows, emps, models), "缺码候补", "waitlist", session)

    # ---- 损坏申请（含上传） ---------------------------------------------

    def post_damage(self, body, headers, session):
        ctype = headers.get("Content-Type", "")
        env = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype,
               "CONTENT_LENGTH": str(len(body))}
        fs = cgi.FieldStorage(fp=io.BytesIO(body), environ=env, keep_blank_values=True)

        def gv(k, default=""):
            return fs[k].value if k in fs else default

        emp_no = gv("emp_no")
        reason = gv("reason", "damage")
        desc = gv("description", "")
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        photos = {}
        for slot in ("sole", "toe", "upper"):
            item = fs[f"photo_{slot}"] if f"photo_{slot}" in fs else None
            fname = ""
            if item is not None and getattr(item, "filename", "") and item.file:
                data = item.file.read()
                if data:
                    ext = Path(item.filename).suffix.lower()
                    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
                        ext = ".img"
                    fname = f"claim-{emp_no}-{slot}-{uuid.uuid4().hex[:8]}{ext}"
                    (self.photo_dir / fname).write_bytes(data)
            if not fname:
                fname = save_photo(emp_no, slot)  # 占位图
            photos[slot] = fname
        with self.svc() as s:
            s.create_damage_renewal(session["actor"], session["role"], emp_no,
                                    reason, desc, photos)
        return ("redirect", "/approvals")

    # ---- 表单 / 静态 ----------------------------------------------------

    def form(self, body: bytes) -> dict:
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}

    def form_multi(self, body: bytes):
        return parse_qs(body.decode("utf-8"))

    def static(self, route):
        name = route.split("/")[-1]
        p = Path(__file__).parent / "static" / name
        if not p.is_file():
            return 404, {}, b"not found"
        ctype = "text/css; charset=utf-8" if name.endswith(".css") else "application/octet-stream"
        return 200, {"Content-Type": ctype}, p.read_bytes()

    def photo(self, route):
        name = Path(route.split("/")[-1]).name
        p = self.photo_dir / name
        if not p.is_file():
            # 回落到占位图
            return 200, {"Content-Type": "image/svg+xml; charset=utf-8"}, \
                PHOTO_SVG.format(label="照片", emp="", slot="").encode()
        ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".gif": "image/gif", ".webp": "image/webp",
                 ".svg": "image/svg+xml"}.get(p.suffix.lower(), "application/octet-stream")
        return 200, {"Content-Type": ctype}, p.read_bytes()

    def html(self, content, flash=None, status=200, extra_headers=None):
        h = {"Content-Type": "text/html; charset=utf-8"}
        if extra_headers:
            h.update(extra_headers)
        return status, h, content.encode("utf-8")

    def redirect(self, location, extra=None):
        # Location 头只能含 latin-1：中文查询参数需百分号编码
        from urllib.parse import quote
        safe = quote(location, safe="/?=&%")
        h = {"Location": safe}
        if extra:
            h.update(extra)
        return 303, h, b""
