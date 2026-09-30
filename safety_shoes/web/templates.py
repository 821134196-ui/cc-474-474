"""函数式 HTML 模板（无第三方模板引擎）。所有用户内容经 html.escape。"""
from __future__ import annotations

import html
from datetime import date

from .. import config
from ..services import days_to_expire

NAV = [
    ("/", "总览", "home"),
    ("/employees", "员工资格", "employees"),
    ("/inventory", "库存矩阵", "inventory"),
    ("/fitting", "试穿发放", "fitting"),
    ("/due", "到期队列", "due"),
    ("/approvals", "损坏审批", "approvals"),
    ("/returns", "旧鞋处置", "returns"),
    ("/waitlist", "缺码候补", "waitlist"),
    ("/door", "门禁模拟", "door"),
]


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def badge(text, cls="") -> str:
    return f'<span class="badge {cls}">{e(text)}</span>'


def level_badge(level) -> str:
    return f'<span class="badge level" title="岗位防护等级">{e(level)}</span>'


def btn(label, href="#", kind="primary", extra="") -> str:
    return f'<a class="btn {kind} {extra}" href="{e(href)}">{e(label)}</a>'


def card(title: str, body: str, extra_head: str = "") -> str:
    return f'<section class="card"><header><h2>{e(title)}</h2>{extra_head}</header>{body}</section>'


def status_badge(s: str) -> str:
    m = {
        "active": ("有效", "ok"), "renewed": ("已换领", "muted"),
        "damaged": ("损坏收回", "bad"), "scrapped": ("已报废", "bad"),
        "transferred": ("调岗结算", "muted"),
        "requested": ("待主管审批", "warn"), "approved": ("已批准", "ok"),
        "rejected": ("已驳回", "bad"), "completed": ("已完成换领", "ok"),
        "waiting": ("候补中", "warn"), "notified": ("已通知", "ok"),
        "fulfilled": ("已兑现", "muted"), "cancelled": ("已取消", "muted"),
        "pending": ("待处理", "warn"),
        "received": ("已收回", "muted"),
        "cleaning_inspection": ("清洁复检中", "warn"),
        "back_to_stock": ("复检回库", "ok"),
        "disposal_pending": ("待处置", "bad"),
        "disposed": ("已处置", "muted"), "exempt": ("豁免登记", "warn"),
        "dispatched": ("已调出", "ok"),
        "confirmed": ("已确认", "ok"), "adjusted": ("已调码", "warn"),
        "none": ("-", "muted"),
    }
    text, cls = m.get(s, (s, "muted"))
    return badge(text, cls)


def days_chip(expire: str) -> str:
    d = days_to_expire(expire)
    cls = "bad" if d <= 0 else ("warn" if d <= config.DUE_SOON_DAYS else "muted")
    text = "已逾期" if d < 0 else (f"{d} 天后到期" if d > 0 else "今天到期")
    return f'<span class="chip {cls}">{text}</span>'


def flash_box(msg: str | None) -> str:
    if not msg:
        return ""
    kind = "bad" if msg.startswith("✗") else "ok"
    return f'<div class="flash {kind}">{e(msg)}</div>'


def layout(title: str, active: str, body: str, session: dict, flash: str | None = None) -> str:
    nav_items = "".join(
        f'<a href="{url}" class="{"on" if key == active else ""}">{e(label)}</a>'
        for url, label, key in NAV
    )
    actor = e(session.get("actor", ""))
    role = config.ROLE_NAMES.get(session.get("role", ""), "")
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · 劳保鞋发放系统</title>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/app.js" defer></script>
</head><body>
<header class="topbar">
  <div class="brand"><span class="mark">⛓</span> 劳保鞋尺码发放与到期换领</div>
  <nav class="nav">{nav_items}</nav>
  <form class="who" method="post" action="/logout">
    <span>{actor} <em>{e(role)}</em></span><button class="btn ghost sm">切换</button>
  </form>
</header>
<main class="wrap">
{flash_box(flash)}
{body}
</main>
<footer class="foot">演示系统 · SQLite 本地持久化 · 照片存于 data/photos · 门禁与处置回执为模拟适配器</footer>
</body></html>"""


# ---------------------------------------------------------------- 登录

def view_login(actors) -> str:
    opts = "".join(
        f'<button class="btn {"primary" if r=="supervisor" else "steel"}" formaction="/login" name="role" value="{r}">'
        f'以 {e(n)}（{e(config.ROLE_NAMES[r])}）身份进入</button>'
        for r, n in actors)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录 · 劳保鞋系统</title><link rel="stylesheet" href="/static/style.css"></head>
<body class="login-body">
<div class="login-card">
  <div class="brand big"><span class="mark">⛓</span> 劳保鞋发放与到期换领</div>
  <p class="muted-txt">请选择本次操作身份。主管可审批提前换领与旧鞋豁免、调整岗位等级；仓管负责试穿发放与回收，不能修改岗位等级。</p>
  <form method="post" action="/login" class="login-roles">
    {opts}
  </form>
</div></body></html>"""


# ---------------------------------------------------------------- 总览

def view_dashboard(d: dict) -> str:
    def stat(label, val, sub=""):
        return f'<div class="stat"><b>{val}</b><span>{e(label)}</span><small>{e(sub)}</small></div>'
    stats = (
        stat("在册员工", d["emp_count"])
        + stat("有效鞋", d["active_count"])
        + stat(f"{config.DUE_SOON_DAYS}天内到期/逾期", d["due_count"], "需安排到期换领")
        + stat("待审批换领", d["renew_pending"], "损坏/提前磨损")
        + stat("待批豁免", d["exempt_pending"], "旧鞋遗失")
        + stat("缺码候补", d["wait_count"])
        + stat("清洁复检中", d["cleaning_count"])
        + stat("待处置报废鞋", d["scrap_count"])
    )
    rem = "".join(
        f'<li><span class="chip {"bad" if r["kind"]=="due" else "warn"}">'
        f'{ "到期" if r["kind"]=="due" else "候补"}</span> {e(r["message"])}</li>'
        for r in d["reminders"]) or '<li class="muted-txt">暂无提醒</li>'
    due_rows = "".join(
        f'<tr><td>{e(r["emp_no"])}</td><td>{e(r["name"])}</td>'
        f'<td>{level_badge(r["level"])}</td><td>{e(r["model_code"])}</td>'
        f'<td>{e(r["size_left"])}/{e(r["size_right"])}</td>'
        f'<td>{e(r["expire_date"])}</td><td>{days_chip(r["expire_date"])}</td></tr>'
        for r in d["due_rows"]) or '<tr><td colspan="7" class="muted-txt">近期无到期</td></tr>'
    return f"""
<div class="stats">{stats}</div>
<div class="grid-2">
{card("进程内提醒（到期与候补）", f'<ul class="reminders">{rem}</ul>')}
{card("到期队列预览", f'''<table class="tbl">
<thead><tr><th>工号</th><th>姓名</th><th>等级</th><th>鞋款</th><th>尺码</th><th>到期日</th><th></th></tr></thead>
<tbody>{due_rows}</tbody></table>''',
 btn("去到期队列", "/due", "ghost sm"))}
</div>"""


# ---------------------------------------------------------------- 员工

def view_employees(rows: list[dict]) -> str:
    trs = "".join(
        f'''<tr>
<td><a href="/employees/{e(r["emp_no"])}"><b>{e(r["emp_no"])}</b></a></td>
<td>{e(r["name"])}</td><td>{e(r["position"])}</td><td>{level_badge(r["level"])}</td>
<td>{e(r["size_left"] or "—")}/{e(r["size_right"] or "—")}
    <span class="muted-txt">{e(r["width"] or "")}</span></td>
<td>{(e(r["model_code"])+" "+e(r["pair_tag"])) if r["model_code"] else '<span class="muted-txt">未领鞋</span>'}</td>
<td>{(e(r["expire_date"])+" "+days_chip(r["expire_date"])) if r["expire_date"] else ""}</td>
<td>{badge("待调岗结算","warn") if r["transfer_pending"] else ""}</td>
</tr>''' for r in rows)
    return card("员工资格与当前鞋款", f"""
<table class="tbl"><thead><tr>
<th>工号</th><th>姓名</th><th>岗位</th><th>防护等级</th><th>脚型尺码(左/右)</th>
<th>当前有效鞋</th><th>使用期限</th><th></th></tr></thead><tbody>{trs}</tbody></table>""")


def view_employee(d: dict) -> str:
    emp, p, active, eligible, transfer, session = (
        d["emp"], d["profile"], d["active"], d["eligible"], d["transfer"], d["session"])
    is_sup = session.get("role") == "supervisor"

    profile_form = f"""
<form method="post" action="/employees/{e(emp['emp_no'])}/profile" class="inline-form">
  <label>左脚尺码 <select name="size_left">{"".join(f'<option{" selected" if p and p["size_left"]==s else ""}>{s}</option>' for s in config.SIZES)}</select></label>
  <label>右脚尺码 <select name="size_right">{"".join(f'<option{" selected" if p and p["size_right"]==s else ""}>{s}</option>' for s in config.SIZES)}</select></label>
  <label>脚型 <select name="width"><option value="N">窄 N</option><option value="M" selected>标准 M</option><option value="W">宽楦 W</option></select></label>
  <label class="grow">备注 <input name="note" value="{e(p['note'] if p else '')}" placeholder="脚型说明"></label>
  <button class="btn steel">保存尺码档案</button>
</form>"""
    if p:
        # 修正脚型选中
        profile_form = profile_form.replace(f'<option value="{p["width"]}">',
                                            f'<option value="{p["width"]}" selected>')
        profile_form = profile_form.replace("<option value=\"M\" selected>", "<option value=\"M\">")

    pos_form = ""
    if is_sup:
        lv_opts = "".join(f'<option{" selected" if emp["level"]==lv else ""}>{lv}</option>'
                          for lv in config.PROTECTION_LEVELS)
        pos_form = f"""
<div class="subbox">
  <h3>岗位调动 / 防护等级（仅主管）</h3>
  <form method="post" action="/employees/{e(emp['emp_no'])}/position" class="inline-form">
    <label>新岗位 <input name="position" value="{e(emp['position'])}" required></label>
    <label>新防护等级 <select name="level">{lv_opts}</select></label>
    <button class="btn">执行调动</button>
  </form>
  <p class="muted-txt">等级变化会生成调岗记录；原岗位有效鞋须先交回或豁免结算后才能领取新等级鞋。</p>
</div>"""
    else:
        pos_form = ('<div class="subbox locked"><h3>岗位防护等级</h3>'
                    f'<p>{level_badge(emp["level"])} 仓管无权修改岗位等级，如需调岗请联系主管。</p></div>')

    if active:
        active_html = f"""
<table class="kv">
<tr><th>鞋款</th><td>{e(active["model_code"])} · {e(active["pair_tag"])}</td>
<th>尺码(左/右)</th><td>{e(active["size_left"])} / {e(active["size_right"])}</td></tr>
<tr><th>发放日期</th><td>{e(active["issue_date"])}</td>
<th>使用期限至</th><td>{e(active["expire_date"])} {days_chip(active["expire_date"])}</td></tr>
<tr><th>发放仓库</th><td>{e(active["warehouse"])}</th><th>状态</th><td>{status_badge(active["status"])}</td></tr>
</table>"""
    else:
        active_html = '<p class="muted-txt">当前无有效鞋，完成尺码档案后可发起试穿首发。</p>'

    elig = "".join(
        f'<div class="model-chip"><b>{e(m["code"])}</b><span>{e(m["name"])}</span></div>'
        for m in eligible) or '<span class="muted-txt">无适用鞋款</span>'

    actions = []
    if transfer:
        actions.append(f"""
<form method="post" action="/transfers/{e(emp['emp_no'])}/settle" class="settle">
  <h3>跨岗位调动结算：{e(transfer["from_level"])} → {e(transfer["to_level"])}</h3>
  <button name="mode" value="reusable" class="btn">交回（可用→清洁复检）</button>
  <button name="mode" value="scrap" class="btn danger">交回（报废→待处置）</button>
  <button name="mode" value="lost" class="btn ghost">旧鞋遗失（豁免待主管批）</button>
</form>""")
    elif d["profile"]:
        emp_no = emp["emp_no"]
        actions.append(f'<p>{btn("发起试穿 / 换领", f"/fitting?emp_no={emp_no}", "primary")}</p>')

    renewal_html = ""
    if d["renewals"]:
        rows = "".join(
            f'<tr><td>{e(r["renewal_no"])}</td><td>{ {"due":"正常到期","damage":"损坏","early_wear":"提前磨损","transfer":"调岗"}.get(r["reason"],r["reason"]) }</td>'
            f'<td>{status_badge(r["status"])}</td><td>{e(r["approve_note"] or "—")}</td>'
            f'<td><a href="/approvals">查看/处理</a></td></tr>'
            for r in d["renewals"])
        renewal_html = f'<div class="subbox"><h3>换领单</h3><table class="tbl"><tbody>{rows}</tbody></table></div>'

    head_card = card(
        f'{emp["emp_no"]} · {emp["name"]}',
        f"""
      <div class="emp-head"><span class="pos">{e(emp['position'])}</span>{level_badge(emp['level'])}</div>
      <h3>当前鞋款</h3>{active_html}
      <h3>岗位授权可领规格</h3><div class="model-chips">{elig}</div>
      {renewal_html}""")

    claim_card = card(
        "损坏 / 提前磨损换领申请",
        f'''
      <form method="post" action="/damage/new" enctype="multipart/form-data" class="claim-form">
        <input type="hidden" name="emp_no" value="{e(emp['emp_no'])}">
        <label class="grow">类型
          <select name="reason"><option value="damage">损坏</option><option value="early_wear">提前磨损</option></select>
        </label>
        <label class="grow">情况说明 <input name="description" placeholder="损坏部位、使用环境"></label>
        <div class="photo-slots">
          <label class="slot"><span>① 鞋底 sole</span><input type="file" name="photo_sole" accept="image/*" capture="environment"><small>留空则用占位图</small></label>
          <label class="slot"><span>② 鞋头 toe</span><input type="file" name="photo_toe" accept="image/*" capture="environment"><small>留空则用占位图</small></label>
          <label class="slot"><span>③ 鞋帮 upper</span><input type="file" name="photo_upper" accept="image/*" capture="environment"><small>留空则用占位图</small></label>
        </div>
        <button class="btn">提交损坏申请（主管审批）</button>
      </form>''')

    return f"""
<div class="grid-2">
  <div>{head_card}</div>
  <div>
    {card("尺码档案（左右脚差异）", profile_form)}
    {pos_form}
    {"".join(actions)}
    {claim_card}
  </div>
</div>"""


# ---------------------------------------------------------------- 库存矩阵

def view_inventory(d: dict) -> str:
    wh, models, matrix, session = d["wh"], d["models"], d["matrix"], d["session"]
    tabs = "".join(
        f'<a class="tab {"on" if w==wh else ""}" href="/inventory?wh={e(w)}">{e(w)}</a>'
        for w in config.WAREHOUSES)
    sizes = config.SIZES

    # 桌面：二维矩阵表（行=鞋款，列=尺码，格内=剩余数量）
    head = "".join(f"<th>{s}</th>" for s in sizes)
    trs = []
    for m in models:
        cells = "".join(
            f'<td class="cell {"zero" if not q else ""}" title="{m["code"]} {s} 剩余 {q} 双"><b>{q}</b></td>'
            for s in sizes for q in [matrix.get(m["code"], {}).get(s, 0)])
        trs.append(f'<tr><th class="rowh"><b>{e(m["code"])}</b><small>{e(m["name"])} · {e(m["level"])}</small></th>{cells}</tr>')
    desktop = f"""
<div class="matrix-desktop">
<table class="matrix"><thead><tr><th>鞋款 ＼ 尺码</th>{head}</tr></thead>
<tbody>{"".join(trs)}</tbody></table>
<p class="muted-txt">格内数字为该鞋款该尺码全部批次剩余数量合计；0 或缺货显示灰格。</p>
</div>"""

    # 移动：每款一张卡，尺码格自动换行，无横向滚动
    cards = []
    for m in models:
        chips = "".join(
            f'<span class="mcell {"zero" if not q else ""}"><i>{s}</i><b>{q}</b></span>'
            for s in sizes for q in [matrix.get(m["code"], {}).get(s, 0)])
        cards.append(f'<div class="mcard"><h3><b>{e(m["code"])}</b> {e(m["name"])}</h3>'
                     f'<div class="mgrid">{chips}</div></div>')
    mobile = f'<div class="matrix-mobile">{"".join(cards)}</div>'

    restock = ""
    if session.get("role") in ("supervisor", "storekeeper"):
        model_opts = "".join(f'<option value="{e(m["code"])}">{e(m["code"])} {e(m["name"])}</option>' for m in models)
        size_opts = "".join(f"<option>{s}</option>" for s in sizes)
        restock = f"""
{card("入库 / 补齐缺码", f'''
<form method="post" action="/inventory/restock" class="inline-form">
  <label>仓库 <select name="warehouse">{ "".join(f'<option{" selected" if w==wh else ""}>{w}</option>' for w in config.WAREHOUSES)}</select></label>
  <label>鞋款 <select name="model_code">{model_opts}</select></label>
  <label>尺码 <select name="size">{size_opts}</select></label>
  <label>数量 <input type="number" name="qty" value="2" min="1" style="width:5rem"></label>
  <button class="btn hi">入库（候补自动提醒）</button>
</form>''')}"""

    return f"""
<div class="tabs">{tabs}</div>
{desktop}{mobile}
{restock}"""


# ---------------------------------------------------------------- 试穿

def view_fitting_start(d: dict) -> str:
    emps = d["emps"]
    emp_opts = "".join(
        f'<option value="{e(r["emp_no"])}" {"selected" if d.get("emp_no")==r["emp_no"] else ""}>'
        f'{e(r["emp_no"])} {e(r["name"])} · {e(r["position"])} · {r["level"]}</option>'
        for r in emps)
    detail = ""
    if d.get("emp"):
        emp, p, eligible, active, renewal = d["emp"], d["profile"], d["eligible"], d["active"], d.get("renewal")
        if not p:
            detail = '<div class="flash bad">✗ 该员工缺少尺码档案，请先在员工页建档。</div>'
        else:
            mopts = "".join(f'<option value="{e(m["code"])}">{e(m["code"])} {e(m["name"])}</option>' for m in eligible)
            warn = ""
            renewal_field = ""
            if d.get("blocked"):
                warn = f'<div class="flash bad">✗ {e(d["blocked"])}</div>'
            if active and renewal:
                warn += ('<div class="flash ok">该员工已有有效鞋，存在已批准换领单 '
                         f'{e(renewal["renewal_no"])}，旧鞋回收条件已满足，可试穿新鞋。</div>')
                renewal_field = f'<input type="hidden" name="renewal_id" value="{renewal["id"]}">'
            elif active:
                warn += ('<div class="flash bad">✗ 该员工在此防护等级已有一双有效鞋，'
                         '须先在到期队列或损坏审批中完成换领批准与旧鞋回收。</div>')
            if d.get("transfer"):
                warn += '<div class="flash bad">✗ 跨岗位调动旧鞋未结算，不能试穿。</div>'
            detail = f"""
{warn}
{card(f'试穿登记 · {emp["emp_no"]} {emp["name"]}', f'''
<p class="muted-txt">岗位 {e(emp['position'])}，防护等级 {level_badge(emp['level'])}；
档案尺码 左 <b>{e(p['size_left'])}</b> / 右 <b>{e(p['size_right'])}</b>（{e(p['width'])} 脚型）{("；"+e(p['note'])) if p['note'] else ""}</p>
<form method="post" action="/fitting/start" class="inline-form">
  <input type="hidden" name="emp_no" value="{e(emp['emp_no'])}">{renewal_field}
  <label>鞋款（岗位授权规格） <select name="model_code">{mopts}</select></label>
  <label>试穿左脚 <select name="size_left">{"".join(f'<option{" selected" if p["size_left"]==s else ""}>{s}</option>' for s in config.SIZES)}</select></label>
  <label>试穿右脚 <select name="size_right">{"".join(f'<option{" selected" if p["size_right"]==s else ""}>{s}</option>' for s in config.SIZES)}</select></label>
  <button class="btn primary">开始试穿</button>
</form>''')}"""
    return f"""
{card("选择员工", f'''
<form method="get" action="/fitting" class="inline-form">
  <label>员工 <select name="emp_no">{emp_opts}</select></label>
  <button class="btn steel">查看可领规格</button>
</form>''')}
{detail}
{card("今日试穿单", d["fitting_list"])}"""


def view_fitting_detail(d: dict) -> str:
    f, emp = d["f"], d["emp"]
    tried = ("<p class='muted-txt'>调码历史：" + e(f["tried_sizes"]).replace(";", " → ") + "</p>") if f["tried_sizes"] else ""
    sl_opts = "".join(f'<option{" selected" if f["final_size_left"] == s else ""}>{s}</option>'
                      for s in config.SIZES)
    sr_opts = "".join(f'<option{" selected" if f["final_size_right"] == s else ""}>{s}</option>'
                      for s in config.SIZES)
    wh_opts = "".join(f"<option>{w}</option>" for w in config.WAREHOUSES)
    fid = f["id"]
    body = f"""
<p>鞋款 <b>{e(f['model_code'])}</b>，防护等级 {level_badge(f['level'])}，
当前状态 {status_badge(f['status'])}</p>
<p>待确认尺码：左 <b>{e(f['final_size_left'])}</b> / 右 <b>{e(f['final_size_right'])}</b></p>
{tried}
<form method="post" action="/fitting/{fid}/adjust" class="inline-form">
  <label>调码-左脚 <select name="size_left">{sl_opts}</select></label>
  <label>调码-右脚 <select name="size_right">{sr_opts}</select></label>
  <button class="btn ghost">试穿调码</button>
</form>
<form method="post" action="/fitting/{fid}/confirm" class="inline-form">
  <label>发放仓库 <select name="warehouse">{wh_opts}</select></label>
  <button class="btn hi">试穿确认：锁定尺码并扣减库存</button>
</form>
<p class="muted-txt">两仓库并发确认同一试穿单时，数据库写锁保证仅一次成功；库存不足整体回滚。</p>"""
    return card(f'试穿单 #{fid} · {emp["name"]}（{emp["emp_no"]}）', body)


def fitting_list_html(rows: list[dict]) -> str:
    trs = "".join(
        f'<tr><td>#{r["id"]}</td><td>{e(r["emp_no"])}</td><td>{e(r["model_code"])}</td>'
        f'<td>{e(r["final_size_left"])}/{e(r["final_size_right"])}</td>'
        f'<td>{status_badge(r["status"])}</td><td><a href="/fittings/{r["id"]}">打开</a></td></tr>'
        for r in rows) or '<tr><td colspan="6" class="muted-txt">今日无试穿单</td></tr>'
    return f'<table class="tbl"><thead><tr><th>#</th><th>工号</th><th>鞋款</th><th>尺码</th><th>状态</th><th></th></tr></thead><tbody>{trs}</tbody></table>'


# ---------------------------------------------------------------- 到期队列

def view_due(rows: list[dict]) -> str:
    trs = "".join(
        f'''<tr><td>{e(r["emp_no"])}</td><td>{e(r["name"])}</td><td>{e(r["position"])}</td>
<td>{level_badge(r["level"])}</td><td>{e(r["model_code"])} {e(r["pair_tag"])}</td>
<td>{e(r["size_left"])}/{e(r["size_right"])}</td><td>{e(r["issue_date"])}</td>
<td>{e(r["expire_date"])}</td><td>{days_chip(r["expire_date"])}</td>
<td>{status_badge(r["renewal_status"]) if r["renewal_status"] else ""}</td>
<td>{f'<form method="post" action="/renewals/due"><input type="hidden" name="emp_no" value="{e(r["emp_no"])}"><button class="btn sm">登记到期换领</button></form>'
            if not r["renewal_status"] else ""}</td></tr>'''
        for r in rows)
    return f"""
{card(f"到期队列（{config.DUE_SOON_DAYS} 天窗口，含逾期）", f'''
<table class="tbl"><thead><tr><th>工号</th><th>姓名</th><th>岗位</th><th>等级</th><th>鞋款</th>
<th>尺码</th><th>发放日</th><th>到期日</th><th>剩余</th><th>换领单</th><th></th></tr></thead>
<tbody>{trs}</tbody></table>
<p class="muted-txt">到期换领登记后即为已批准状态；员工须先交回旧鞋（或主管批准遗失豁免），才能试穿领取新鞋。</p>''')}"""


# ---------------------------------------------------------------- 审批 / 回收

def view_approvals(d: dict) -> str:
    is_sup = d["session"].get("role") == "supervisor"
    ren = []
    for r in d["renewals"]:
        photos = ""
        if r["photo_sole"]:
            slots = [("鞋底", "photo_sole"), ("鞋头", "photo_toe"), ("鞋帮", "photo_upper")]
            photos = '<div class="photo-three">' + "".join(
                f'<figure><img src="/photos/{e(r[k])}" alt="{label}" loading="lazy">'
                f'<figcaption>{label} · {e(r[k])}</figcaption></figure>'
                for label, k in slots) + "</div>"
        act = ""
        if r["status"] == "requested":
            if is_sup:
                act = f"""<form method="post" action="/renewals/{r['id']}/approve" class="approve-row">
                  <input name="note" placeholder="审批意见（可选）">
                  <button name="approved" value="1" class="btn hi">主管批准</button>
                  <button name="approved" value="0" class="btn danger">驳回</button></form>"""
            else:
                act = '<p class="muted-txt">等待主管审批，仓管无权批准</p>'
        ren.append(f"""<div class="claim">
<h3>{e(r['renewal_no'])} · {e(r['emp_no'])} {e(r['name'])}
<span class="muted-txt">{"损坏" if r['reason']=='damage' else "提前磨损"}</span>
{status_badge(r['status'])}</h3>
<p class="muted-txt">旧鞋 {e(r['pair_tag'])}（{e(r['model_code'])}，期限至 {e(r['expire_date'])}）</p>
<p>{e(r['claim_desc'] or '')}</p>{photos}{act}
<p class="muted-txt">审批意见：{e(r['approve_note']) or '—'} · 主管：{e(r['supervisor']) or '—'}</p>
</div>""")

    # 豁免审批
    ex_rows = []
    for r in d["exemptions"]:
        act = ""
        if r["approve_status"] == "pending":
            act = (f'<form method="post" action="/returns/{r["id"]}/exemption" class="approve-row">'
                   '<button name="approved" value="1" class="btn hi">批准豁免</button>'
                   '<button name="approved" value="0" class="btn danger">不批准（须交回）</button></form>'
                   if is_sup else '<span class="muted-txt">待主管审批</span>')
        ex_rows.append(f'<tr><td>{e(r["pair_tag"])}</td><td>{e(r["emp_no"])}</td>'
                       f'<td>{"旧鞋遗失" if r["exemption_reason"]=="lost" else "现场销毁"}</td>'
                       f'<td>{status_badge(r["approve_status"])}</td><td>{e(r["approver"]) or "—"}</td><td>{act}</td></tr>')
    ex_tbl = f"""<table class="tbl"><thead><tr><th>鞋号</th><th>工号</th><th>豁免原因</th><th>状态</th><th>审批人</th><th></th></tr></thead>
<tbody>{"".join(ex_rows) or '<tr><td colspan="6" class="muted-txt">暂无豁免记录</td></tr>'}</tbody></table>"""

    return f"""
{card("损坏 / 提前磨损换领审批（三张固定照片位）", "".join(ren) or '<p class="muted-txt">暂无损坏申请</p>')}
{card("旧鞋遗失豁免审批", ex_tbl)}"""


def view_returns(d: dict) -> str:
    def rows(rs):
        return "".join(
            f'<tr><td>{e(r["pair_tag"])}</td><td>{e(r["emp_no"])}</td>'
            f'<td>{e(r["model_code"])} {e(r["size_left"])}/{e(r["size_right"])}</td>'
            f'<td>{("可用" if r["condition"]=="reusable" else "报废" if r["condition"]=="scrap" else ("遗失豁免" if r["exemption"] else "—"))}</td>'
            f'<td>{status_badge(r["status"])}</td><td>{e(r["warehouse"])}</td></tr>' for r in rs)

    cleaning_acts = "".join(
        f'<tr><td>{e(r["pair_tag"])}</td><td>{e(r["emp_no"])}</td><td>{e(r["model_code"])}</td>'
        f'<td><form method="post" action="/returns/{r["id"]}/inspect" class="approve-row">'
        '<button name="passed" value="1" class="btn sm">复检通过·回库</button>'
        '<button name="passed" value="0" class="btn sm danger">不通过·转报废</button></form></td></tr>'
        for r in d["cleaning"]) or '<tr><td colspan="4" class="muted-txt">清洁复检队列空</td></tr>'

    scrap_by_wh: dict[str, list] = {}
    for r in d["scrap"]:
        scrap_by_wh.setdefault(r["warehouse"], []).append(r)
    scrap_forms = []
    for wh_name in config.WAREHOUSES:
        items = scrap_by_wh.get(wh_name, [])
        body = "".join(
            f'<tr><td>{e(r["id"])}</td><td>{e(r["pair_tag"])}</td><td>{e(r["emp_no"])}</td>'
            f'<td><input type="checkbox" name="ids" value="{r["id"]}" checked></td></tr>'
            for r in items) or '<tr><td colspan="4" class="muted-txt">该仓库待处置队列空</td></tr>'
        scrap_forms.append(f"""<div class="subbox">
<h3>{e(wh_name)}</h3>
<form method="post" action="/disposal/create">
<input type="hidden" name="warehouse" value="{e(wh_name)}">
<table class="tbl"><thead><tr><th>回收#</th><th>鞋号</th><th>工号</th><th>入批</th></tr></thead>
<tbody>{body}</tbody></table>
<button class="btn danger">建立 {e(wh_name)} 处置批次</button>
</form></div>""")

    batches = []
    for b in d["batches"]:
        dispatch = ""
        if b["status"] == "pending":
            dispatch = (f'<form method="post" action="/disposal/{b["id"]}/dispatch">'
                        '<button class="btn sm">调出并取处置回执</button></form>')
        batches.append(f'<tr><td>{e(b["batch_no"])}</td><td>{e(b["warehouse"])}</td>'
                       f'<td>{b["item_count"]}</td><td>{status_badge(b["status"])}</td>'
                       f'<td>{e(b["receipt_no"]) or "—"}</td><td>{dispatch}</td></tr>')
    batches = "".join(batches) or '<tr><td colspan="6" class="muted-txt">暂无批次</td></tr>'

    return f"""
<div class="grid-2">
{card("全部回收记录", f'<table class="tbl"><thead><tr><th>鞋号</th><th>工号</th><th>鞋款尺码</th><th>品相</th><th>状态</th><th>仓库</th></tr></thead><tbody>{rows(d["all"])}</tbody></table>')}
{card("清洁复检（可用旧鞋）", f'<table class="tbl"><thead><tr><th>鞋号</th><th>工号</th><th>鞋款</th><th>复检</th></tr></thead><tbody>{cleaning_acts}</tbody></table>')}
</div>
<div class="grid-2">
{card("报废鞋 → 待处置批次（按仓库）", "".join(scrap_forms))}
{card("处置批次与回执", f'<table class="tbl"><thead><tr><th>批次</th><th>仓库</th><th>数量</th><th>状态</th><th>回执</th><th></th></tr></thead><tbody>{batches}</tbody></table>')}
</div>"""


# ---------------------------------------------------------------- 候补

def view_waitlist(rows: list[dict], emps: list[dict], models: list[dict]) -> str:
    trs_list = []
    for r in rows:
        cancel = ""
        if r["status"] == "waiting":
            cancel = (f'<form method="post" action="/waitlist/{r["id"]}/cancel">'
                      '<button class="btn sm ghost">取消候补</button></form>')
        trs_list.append(
            f'<tr><td>#{r["id"]}</td><td>{e(r["emp_no"])} {e(r["name"])}</td>'
            f'<td>{level_badge(r["level"])}</td><td>{e(r["model_code"])}</td>'
            f'<td>{e(r["size_left"])}/{e(r["size_right"])}</td><td>{status_badge(r["status"])}</td>'
            f'<td>{(e(r["notified_at"]) or "—")}</td><td>{cancel}</td></tr>')
    trs = "".join(trs_list) or '<tr><td colspan="8" class="muted-txt">候补名单为空</td></tr>'
    emp_opts = "".join(f'<option value="{e(r["emp_no"])}">{e(r["emp_no"])} {e(r["name"])}</option>' for r in emps)
    m_opts = "".join(f'<option value="{e(m["code"])}">{e(m["code"])}</option>' for m in models)
    s_opts = "".join(f"<option>{s}</option>" for s in config.SIZES)
    return f"""
{card("缺码候补名单", f'''
<table class="tbl"><thead><tr><th>#</th><th>员工</th><th>等级</th><th>鞋款</th><th>尺码</th><th>状态</th><th>通知时间</th><th></th></tr></thead>
<tbody>{trs}</tbody></table>''')}
{card("登记候补（试穿缺码时）", f'''
<form method="post" action="/waitlist/join" class="inline-form">
  <label>员工 <select name="emp_no">{emp_opts}</select></label>
  <label>鞋款 <select name="model_code">{m_opts}</select></label>
  <label>左脚 <select name="size_left">{s_opts}</select></label>
  <label>右脚 <select name="size_right">{s_opts}</select></label>
  <button class="btn">登记候补</button>
</form><p class="muted-txt">任意仓库入库补齐尺码后，后台提醒任务自动生成到货提醒并通知员工。</p>''')}"""


# ---------------------------------------------------------------- 门禁

def view_door(result: dict | None) -> str:
    out = ""
    if result:
        cls = "ok" if result.get("ok") else "bad"
        out = f"""<div class="door-result {cls}">
<h3>{e(result.get("message",""))}</h3>
{("<p>防护等级 " + level_badge(result["level"]) + f' · 当前持鞋：{"是" if result["has_shoes"] else "否"}</p>') if result.get("ok") else ""}
{f'<p class="warn-txt">⚠ {e(result["warning"])}</p>' if result.get("warning") else ""}
</div>"""
    return card("门禁工号模拟适配器", f"""
<form method="post" action="/door/swipe" class="inline-form">
  <label>刷入工号 <input name="emp_no" value="E1001" required></label>
  <button class="btn primary">刷工号进厂</button>
</form>{out}
<p class="muted-txt">真实环境对接门禁控制器；此处模拟返回员工资格、持鞋状态与调岗结算提醒。</p>""")


# ---------------------------------------------------------------- 发放单详情

def view_issue(issue: dict, returns: list[dict]) -> str:
    rhtml = "".join(
        f'<tr><td>#{r["id"]}</td><td>{"已交回" if r["returned"] else "豁免"}</td>'
        f'<td>{e(r["condition"] or r["exemption_reason"])}</td>'
        f'<td>{status_badge(r["status"])}</td><td>{status_badge(r["approve_status"])}</td></tr>'
        for r in returns) or '<tr><td colspan="5" class="muted-txt">无回收记录</td></tr>'
    issue_card = card(f'发放单 {issue["issue_no"]}', f"""
<table class="kv">
<tr><th>员工</th><td>{e(issue['emp_no'])} {e(issue['name'])}</td>
<th>岗位</th><td>{e(issue['position'])} {level_badge(issue['level'])}</td></tr>
<tr><th>鞋款</th><td>{e(issue['model_code'])}</td><th>鞋号</th><td>{e(issue['pair_tag'])}</td></tr>
<tr><th>尺码</th><td>左 {e(issue['size_left'])} / 右 {e(issue['size_right'])}</td>
<th>仓库</th><td>{e(issue['warehouse'])}</td></tr>
<tr><th>发放日期</th><td>{e(issue['issue_date'])}</td>
<th>使用期限</th><td>{e(issue['expire_date'])} {days_chip(issue['expire_date'])}</td></tr>
</table>""")
    return f"""
{issue_card}
{card("旧鞋回收链路", f'<table class="tbl"><thead><tr><th>#</th><th>方式</th><th>品相/原因</th><th>处置状态</th><th>豁免审批</th></tr></thead><tbody>{rhtml}</tbody></table>')}"""
