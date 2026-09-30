/* 劳保鞋发放换领系统前端（原生 JS，无依赖） */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const fmtSize = (s) => (Math.abs(s - Math.round(s)) < 0.01 ? String(Math.round(s)) : String(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ACTOR_NAMES = { keeper: "仓管·刘库", supervisor: "主管·陈厂", admin: "管理员" };
const GRADE_BADGE = { SB: "基础", S1: "防静电", S1P: "防穿刺", S3: "防水防刺" };

const state = { tab: "dash", data: null, selectedEmp: null, role: "keeper" };

/* ---------------- API ---------------- */

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json",
               "X-Actor-Name": ACTOR_NAMES[state.role], "X-Actor-Role": state.role },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const j = await res.json();
  if (!j.ok) throw new Error(j.error || "请求失败");
  return j;
}

async function apiForm(path, form) {
  form.append("actor_name", ACTOR_NAMES[state.role]);
  form.append("actor_role", state.role);
  const res = await fetch(path, {
    method: "POST",
    headers: { "X-Actor-Name": ACTOR_NAMES[state.role], "X-Actor-Role": state.role },
    body: form,
  });
  const j = await res.json();
  if (!j.ok) throw new Error(j.error || "请求失败");
  return j;
}

async function refresh() {
  const j = await api("/api/state");
  state.data = j.data;
  render();
}

function toast(msg, ok = true) {
  const el = document.createElement("div");
  el.className = "toast " + (ok ? "ok" : "err");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

async function guard(fn) {
  try { await fn(); } catch (e) { toast(e.message, false); }
}

/* ---------------- 页面框架 ---------------- */

function render() {
  const app = $("#app");
  const tabs = {
    dash: renderDash, employees: renderEmployees, inventory: renderInventory,
    due: renderDue, renewals: renderRenewals, returns: renderReturns,
  };
  app.innerHTML = "";
  if (state.tab === "employees" && state.selectedEmp) {
    app.appendChild(employeeDetailView(state.selectedEmp));
  } else {
    app.appendChild(tabs[state.tab]());
  }
  window.scrollTo({ top: 0 });
}

function card(title, extra = "") {
  const el = document.createElement("section");
  el.className = "card";
  el.innerHTML = `<h2>${title}</h2>`;
  if (extra) el.insertAdjacentHTML("beforeend", extra);
  return el;
}

function badge(txt, cls) {
  return `<span class="badge ${cls}">${esc(txt)}</span>`;
}

function statusPill(s) {
  const [t, c] = ({
    active: ["有效", "ok"], returned: ["已交回", "gray"], exemption: ["豁免交回", "warn"],
    scrapped: ["已报废", "bad"], pending: ["待审批", "warn"], approved: ["已批准", "steel"],
    auto: ["到期可换", "hi"], rejected: ["已驳回", "bad"], completed: ["已领取", "ok"],
    waiting: ["候补排队", "warn"], notified: ["已通知到货", "steel"], filled: ["已补发", "ok"],
    cancelled: ["已取消", "gray"],
  }[s] || [s, "gray"]);
  return badge(t, c);
}

/* ---------------- 总览 ---------------- */

function renderDash() {
  const d = state.data;
  const box = document.createElement("div");
  const activeCount = d.issuances.length;
  const overdue = d.due.filter((x) => x.overdue).length;
  const pendingDamage = d.renewals.filter((r) => r.status === "pending").length;
  const waiting = d.waitlist.filter((w) => w.status === "waiting").length;
  const pendingRecon = d.returns.filter((r) => r.recon_status === "pending").length;

  box.innerHTML = `
    <div class="stats">
      <div class="stat"><div class="num">${d.employees.length}</div><div class="lbl">在岗员工</div></div>
      <div class="stat"><div class="num">${activeCount}</div><div class="lbl">有效劳保鞋</div></div>
      <div class="stat ${overdue ? "alert" : ""}"><div class="num">${overdue}</div><div class="lbl">已到期未换</div></div>
      <div class="stat ${pendingDamage ? "alert" : ""}"><div class="num">${pendingDamage}</div><div class="lbl">待主管审批换领</div></div>
      <div class="stat ${waiting ? "alert" : ""}"><div class="num">${waiting}</div><div class="lbl">缺码候补</div></div>
      <div class="stat"><div class="num">${pendingRecon}</div><div class="lbl">待清洁复检</div></div>
    </div>`;

  const rcard = card("系统提醒 <span class='small muted'>（进程内任务每 60 秒生成，到期 / 候补到货）</span>");
  if (!d.reminders.length) {
    rcard.insertAdjacentHTML("beforeend", `<div class="muted small">暂无提醒</div>`);
  } else {
    const list = document.createElement("div");
    list.className = "list";
    d.reminders.slice(0, 8).forEach((r) => {
      const line = document.createElement("div");
      line.className = "reminder-line";
      line.innerHTML = `${badge(r.kind === "due" ? "到期" : "候补", r.kind === "due" ? "warn" : "steel")}
        <span>${esc(r.message)}</span><span class="spacer"></span><span class="small muted">${esc(r.created_at)}</span>`;
      list.appendChild(line);
    });
    rcard.appendChild(list);
  }
  const tip = card("演示路径");
  tip.insertAdjacentHTML("beforeend", `
    <div class="notice">
      ① <b>缺码候补</b>：员工 E1004 孙安全 的 GUARD-SB 41 码两仓无货——在库存矩阵给 41 码入库后，下一轮提醒自动通知。<br>
      ② <b>跨岗位调动</b>：E1006 吴进步 已从冲压 S1 调到铸造 S3（主管操作），调岗换领单在“损坏/换领审核”页待领取。<br>
      ③ <b>提前换领</b>：E1005 周大勇 鞋底/鞋头/鞋帮三照片申请提前换领，切到“主管·陈厂”审批，可勾选旧鞋遗失豁免。
    </div>
    <div class="small muted">门禁工号由模拟门禁适配器同步与核验；报废处置出厂后由模拟厂商出具回执号。</div>`);
  box.append(rcard, tip);
  return box;
}

/* ---------------- 员工 ---------------- */

function renderEmployees() {
  const box = document.createElement("div");
  const c = card("员工资格与尺码档案");
  const list = document.createElement("div");
  list.className = "emp-list";
  state.data.employees.forEach((e) => {
    const shoe = state.data.issuances.find((i) => i.employee_id === e.id);
    const item = document.createElement("div");
    item.className = "emp-item";
    item.innerHTML = `
      <div class="row"><span class="name">${esc(e.id)} ${esc(e.name)}</span>
        ${badge(e.grade_code, "grade")}</div>
      <div class="meta">${esc(e.post)} · ${esc(e.grade_name)} · 期限 ${e.lifespan_months} 个月</div>
      <div class="meta">门禁工号：${esc(e.badge_no)}</div>
      <div class="meta">当前鞋款：${shoe ? esc(shoe.model_name) + " " + fmtSize(shoe.size) + " 码" : "<span class='badge warn'>未发放</span>"}</div>`;
    item.onclick = () => { state.selectedEmp = e.id; render(); };
    list.appendChild(item);
  });
  c.appendChild(list);
  box.appendChild(c);
  return box;
}

function employeeDetailView(eid) {
  const d = state.data;
  const emp = d.employees.find((x) => x.id === eid);
  const box = document.createElement("div");
  if (!emp) { state.selectedEmp = null; return renderEmployees(); }

  const shoes = d.issuances.filter((i) => i.employee_id === eid);
  const current = shoes.find((s) => s.status === "active");
  const head = card("");
  head.innerHTML = `
    <div class="emp-head">
      <button class="btn ghost sm" id="backEmp">← 返回列表</button>
      <span class="name">${esc(emp.id)} ${esc(emp.name)}</span>
      <span class="post">${esc(emp.post)}</span>
      <span class="spacer"></span>
      <div class="grade-block">
        <div class="g-code">${esc(emp.grade_code)}</div>
        <div class="g-name">${esc(emp.grade_name)} · 使用期限 ${emp.lifespan_months} 个月</div>
      </div>
    </div>`;
  $("#backEmp", head).onclick = () => { state.selectedEmp = null; render(); };
  box.appendChild(head);

  /* 资格与尺码（左右脚差异） */
  const pcard = card("岗位授权与脚型尺码");
  pcard.insertAdjacentHTML("beforeend", `
    <div id="empLoad" class="muted small">加载档案…</div>`);
  box.appendChild(pcard);
  fetch(`/api/employees/${eid}`).then((r) => r.json()).then((j) => {
    if (!j.ok) { $("#empLoad", pcard).textContent = j.error; return; }
    const { profile, eligible, badge: badgeInfo } = j.data;
    const profHtml = profile ? `
      <div class="kv">
        <div><div class="k">左脚 EUR</div><div class="v">${fmtSize(profile.left_size)}</div></div>
        <div><div class="k">右脚 EUR</div><div class="v">${fmtSize(profile.right_size)}${profile.left_size !== profile.right_size ? " <span class='badge hi'>左右差码</span>" : ""}</div></div>
        <div><div class="k">脚宽</div><div class="v">${{ N: "窄", M: "中", W: "宽" }[profile.width]}</div></div>
        <div><div class="k">门禁核验</div><div class="v"><span class="badge ${badgeInfo.ok ? "ok" : "bad"}">${badgeInfo.ok ? "刷卡通过" : "拒绝"}</span></div></div>
      </div>
      ${profile.note ? `<div class="small muted" style="margin-top:6px">${esc(profile.note)}</div>` : ""}`
      : `<div class="notice">尚无脚型尺码档案，请先测量建档（左右脚分别记录）。</div>`;
    $("#empLoad", pcard).outerHTML = profHtml;

    /* 可领规格 + 试穿发放 */
    const fcard = card("试穿确认与发放（确认后锁定尺码并扣减库存）");
    const modelOpts = eligible.models.map((m) =>
      `<option value="${m.code}" ${m.lock_size ? `data-lock="${m.lock_size}"` : ""}>${esc(m.code)} ${esc(m.name)}</option>`).join("");
    fcard.insertAdjacentHTML("beforeend", `
      <div class="form-grid">
        <label class="fld">授权鞋款<select id="fitModel">${modelOpts}</select></label>
        <label class="fld">试穿尺码<select id="fitSize"></select></label>
        <label class="fld">左脚感受<select id="fitL"><option>合适<option>松<option>紧</select></label>
        <label class="fld">右脚感受<select id="fitR"><option>合适<option>松<option>紧</select></label>
        <label class="fld">试穿结果<select id="fitResult">
          <option value="ok">合适（锁定可发）</option>
          <option value="adjust">需调码（重新试穿）</option>
          <option value="reject">不合脚（否决）</option></select></label>
        <label class="fld" style="justify-content:end">
          <button class="btn ghost" id="btnFit">登记试穿</button></label>
      </div>
      <div class="row" style="margin-top:12px">
        <label class="fld" style="min-width:150px">发放仓库
          <select id="issueWh"><option>A仓</option><option>B仓</option></select></label>
        <button class="btn hi" id="btnIssue" style="margin-top:14px">试穿确认 → 发放并扣库存</button>
        <span class="small muted">同一员工同一防护等级仅允许一双有效鞋</span>
      </div>
      <div id="fitHint" class="small muted" style="margin-top:8px"></div>`);
    box.appendChild(fcard);

    const fitModel = $("#fitModel", fcard);
    const fitSize = $("#fitSize", fcard);
    const fillSizes = () => {
      const m = eligible.models.find((x) => x.code === fitModel.value);
      fitSize.innerHTML = m.produced.map((s) =>
        `<option value="${s}" ${s === m.lock_size ? "selected" : ""}>${fmtSize(s)}${s === m.lock_size ? "（脚型推荐）" : ""}</option>`).join("");
    };
    fitModel.onchange = fillSizes; fillSizes();

    $("#btnFit", fcard).onclick = () => guard(async () => {
      await api("/api/fittings", {
        employee_id: eid, model_code: fitModel.value, tried_size: parseFloat(fitSize.value),
        left_fit: $("#fitL", fcard).value, right_fit: $("#fitR", fcard).value,
        result: $("#fitResult", fcard).value,
        note: `左脚:${$("#fitL", fcard).value}/右脚:${$("#fitR", fcard).value}`,
      });
      toast("试穿已登记（最新结果为“合适”时锁定该尺码）");
      await refresh();
    });

    $("#btnIssue", fcard).onclick = () => guard(async () => {
      await api("/api/issues", {
        employee_id: eid, model_code: fitModel.value,
        size: parseFloat(fitSize.value), warehouse: $("#issueWh", fcard).value,
      });
      toast("发放成功，库存已扣减");
      await refresh();
    });

    /* 当前有效鞋 + 换领入口 */
    const scard = current ? card("当前有效鞋与换领") : card("鞋款记录");
    if (current) {
      scard.appendChild(currentShoeBlock(current, eid));
    } else {
      scard.insertAdjacentHTML("beforeend",
        `<div class="notice">该员工当前防护等级没有有效鞋，完成试穿后即可发放。</div>`);
    }
    const history = shoes;
    if (history.length) {
      const h = document.createElement("div");
      h.innerHTML = `<h3>历史发放链路</h3>`;
      const lst = document.createElement("div");
      lst.className = "list";
      history.forEach((s) => {
        const it = document.createElement("div");
        it.className = "item-card";
        it.innerHTML = `<div class="top">#${s.id} ${badge(s.grade_code, "grade")}
          <b>${esc(s.model_name)}</b> ${fmtSize(s.size)} 码 ${esc(s.size_detail || "")}
          ${statusPill(s.status)}</div>
          <div class="body">${esc(s.warehouse)}发放 ${esc(s.issued_at)} → 到期 ${esc(s.due_date)}
          （期限 ${s.lifespan_months} 个月）${s.replaced_id ? ` · 换领自 #${s.replaced_id}` : ""}
          ${s.old_returned_at ? ` · 旧鞋${s.status === "exemption" ? "豁免" : "交回"} ${esc(s.old_returned_at)}` : ""}</div>`;
        lst.appendChild(it);
      });
      h.appendChild(lst);
      scard.appendChild(h);
    }
    box.appendChild(scard);

    /* 跨岗位调动：仅主管/admin */
    const tcard = card("跨岗位调动");
    tcard.insertAdjacentHTML("beforeend", `
      <div class="row">
        <label class="fld" style="min-width:160px">调入岗位<input id="toPost" placeholder="如：铸造工"></label>
        <label class="fld" style="min-width:150px">新防护等级<select id="toGrade">
          <option value="SB">SB 基本防砸</option><option value="S1">S1 防砸防静电</option>
          <option value="S1P">S1P 防砸防穿刺</option><option value="S3">S3 防水防穿刺</option>
        </select></label>
        <button class="btn" id="btnTransfer" style="margin-top:14px">主管办理调动</button>
      </div>
      <div class="small muted" style="margin-top:6px">调动后旧等级鞋生成调岗换领单，领新鞋时交回旧鞋。仓管角色办理会被拒绝。</div>`);
    $("#toGrade", tcard).value = emp.grade_code === "S1" ? "S3" : "S1";
    $("#btnTransfer", tcard).onclick = () => guard(async () => {
      await api("/api/transfers", {
        employee_id: eid, to_post: $("#toPost", tcard).value.trim() || "新岗位",
        to_grade: $("#toGrade", tcard).value,
      });
      toast("调动完成，调岗换领单已生成");
      await refresh();
    });
    box.appendChild(tcard);
  });

  return box;
}

function currentShoeBlock(s, eid) {
  const el = document.createElement("div");
  el.className = "current-shoe";
  el.innerHTML = `
    <div class="kv">
      <div><div class="k">防护等级 / 鞋款</div><div class="v">${badge(s.grade_code, "grade")} ${esc(s.model_name)}</div></div>
      <div><div class="k">锁定尺码</div><div class="v">${fmtSize(s.size)} 码 ${esc(s.size_detail || "")}</div></div>
      <div><div class="k">发放日期</div><div class="v">${esc(s.issued_at)} · ${esc(s.warehouse)}</div></div>
      <div><div class="k">使用期限 / 到期日</div><div class="v">${s.lifespan_months} 个月 → <b>${esc(s.due_date)}</b></div></div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn ghost sm" data-act="expiry">正常到期申请换领</button>
      <button class="btn danger sm" data-act="damage">损坏提前换领（三照片）</button>
    </div>`;
  $$("button[data-act]", el).forEach((btn) => {
    btn.onclick = () => btn.dataset.act === "expiry"
      ? expiryModal(s) : damageModal(s);
  });
  return el;
}

async function expiryModal(s) {
  if (!confirm(`确认为 #${s.id} 提交正常到期换领？（未到 ${s.due_date} 会被拒绝）`)) return;
  await guard(async () => {
    const form = new FormData();
    form.append("issuance_id", s.id); form.append("kind", "expiry");
    form.append("reason", "正常到期换领");
    await apiForm("/api/renewals", form);
    toast("到期换领单已生成，可到审核页领取新鞋");
    await refresh();
  });
}

function damageModal(s) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal">
      <h2>损坏提前换领 · #${s.id} ${esc(s.model_name)}</h2>
      <div class="notice">主管审批；必须提交 <b>鞋底、鞋头、鞋帮</b> 三个固定照片位的磨损照片。</div>
      <label class="fld" style="margin-bottom:10px">损坏情况
        <textarea id="dmgReason" rows="2" placeholder="如：鞋底磨平、鞋头钢头外露、鞋帮撕裂"></textarea></label>
      <div class="photo-slots">
        ${["sole", "toe", "upper"].map((slot) => {
          const name = { sole: "鞋底 sole", toe: "鞋头 toe", upper: "鞋帮 upper" }[slot];
          return `<div class="photo-slot" data-slot="${slot}">
            <div class="slot-name">${name}</div>
            <div class="empty">未上传</div>
            <input type="file" accept="image/*" data-file="${slot}">
          </div>`;
        }).join("")}
      </div>
      <div class="foot">
        <button class="btn ghost" id="dmgCancel">取消</button>
        <button class="btn hi" id="dmgSubmit">提交主管审批</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  $$("input[type=file]", mask).forEach((inp) => {
    inp.onchange = () => {
      const f = inp.files[0];
      const slot = inp.closest(".photo-slot");
      const empty = $(".empty", slot);
      if (f) {
        empty.outerHTML = `<img alt="预览" src="${URL.createObjectURL(f)}">`;
      }
    };
  });
  const close = () => mask.remove();
  $("#dmgCancel", mask).onclick = close;
  mask.onclick = (e) => { if (e.target === mask) close(); };
  $("#dmgSubmit", mask).onclick = () => guard(async () => {
    const form = new FormData();
    form.append("issuance_id", s.id); form.append("kind", "damage");
    form.append("reason", $("#dmgReason", mask).value.trim() || "提前磨损");
    let filled = 0;
    $$("input[type=file]", mask).forEach((inp) => {
      if (inp.files[0]) { form.append(inp.dataset.file, inp.files[0]); filled++; }
    });
    if (filled < 3) throw new Error("鞋底、鞋头、鞋帮三张照片必须全部上传");
    await apiForm("/api/renewals", form);
    close();
    toast("损坏换领已提交，等待主管审批");
    await refresh();
  });
}

/* ---------------- 库存矩阵 ---------------- */

function renderInventory() {
  const box = document.createElement("div");
  const c = card("库存矩阵（鞋款 × 尺码，格内为剩余可发数量）");
  c.insertAdjacentHTML("beforeend", `
    <div class="row" style="margin-bottom:10px">
      <label class="fld" style="min-width:170px">入库鞋款<select id="rcvModel"></select></label>
      <label class="fld" style="min-width:110px">尺码<select id="rcvSize"></select></label>
      <label class="fld" style="min-width:110px">仓库<select id="rcvWh"><option>A仓</option><option>B仓</option></select></label>
      <label class="fld" style="min-width:100px">数量<input id="rcvQty" type="number" value="3" min="1"></label>
      <button class="btn" id="btnRcv" style="margin-top:14px">到货入库</button>
      <span class="small muted spacer">入库后下一轮提醒自动通知该规格候补员工</span>
    </div>`);
  const rcvModel = $("#rcvModel", c);
  Object.entries(state.data.matrix).forEach(([code, m]) => {
    rcvModel.insertAdjacentHTML("beforeend", `<option value="${code}">${code} ${esc(m.name)}</option>`);
  });
  const rcvSize = $("#rcvSize", c);
  const fillRcvSizes = () => {
    const m = state.data.matrix[rcvModel.value];
    rcvSize.innerHTML = m.sizes.map((s) => `<option value="${s}">${fmtSize(s)}</option>`).join("");
    const wl = state.data.waitlist.find((w) => w.status === "waiting" && w.model_code === rcvModel.value);
    if (wl) rcvSize.value = wl.size;
  };
  rcvModel.onchange = fillRcvSizes; fillRcvSizes();
  $("#btnRcv", c).onclick = () => guard(async () => {
    await api("/api/receive", {
      model_code: rcvModel.value, size: parseFloat(rcvSize.value),
      warehouse: $("#rcvWh", c).value, qty: parseInt($("#rcvQty", c).value, 10),
    });
    toast("入库成功");
    await api("/api/reminders/tick", {});
    await refresh();
  });

  Object.entries(state.data.matrix).forEach(([code, m]) => {
    const blk = document.createElement("div");
    blk.className = "model-block";
    blk.innerHTML = `
      <div class="model-title">${esc(code)} ${esc(m.name)} ${badge(m.grade, "grade")}
        <span class="small muted">${badge("A仓/B仓", "gray")} 分仓余量见格内小字</span></div>
      <div class="matrix"></div>`;
    const grid = $(".matrix", blk);
    m.sizes.forEach((s) => {
      const cell = m.grid[s];
      const a = cell.warehouses["A仓"]?.available ?? 0;
      const b = cell.warehouses["B仓"]?.available ?? 0;
      const low = cell.available > 0 && cell.available <= 2;
      const el = document.createElement("div");
      el.className = "cell " + (cell.available === 0 ? "zero" : low ? "low" : "");
      el.innerHTML = `<div class="size">${fmtSize(s)} 码</div>
        <div class="qty">${cell.available}</div>
        <div class="wh">A:${a} / B:${b}</div>`;
      el.title = `${code} ${fmtSize(s)} 码：A仓余 ${a}，B仓余 ${b}`;
      grid.appendChild(el);
    });
    c.appendChild(blk);
  });
  box.appendChild(c);

  /* 候补队列 */
  const wl = state.data.waitlist;
  if (wl.length) {
    const wcard = card("缺码候补队列");
    const lst = document.createElement("div");
    lst.className = "list";
    wl.forEach((w) => {
      const it = document.createElement("div");
      it.className = "item-card";
      it.innerHTML = `<div class="top">#${w.id} <b>${esc(w.employee_id)} ${esc(w.employee_name)}</b>
        ${esc(w.model_name)} ${fmtSize(w.size)} 码 ${statusPill(w.status)}</div>
        <div class="body">登记于 ${esc(w.created_at)}${w.notified_at ? " · 到货通知 " + esc(w.notified_at) : ""}</div>`;
      lst.appendChild(it);
    });
    wcard.appendChild(lst);
    box.appendChild(wcard);
  }
  return box;
}

/* ---------------- 到期队列 ---------------- */

function renderDue() {
  const c = card("到期换领队列（未来 30 天 + 已逾期）");
  const d = state.data;
  if (!d.due.length) {
    c.insertAdjacentHTML("beforeend", `<div class="muted">近期没有到期鞋款。</div>`);
    return c;
  }
  const lst = document.createElement("div");
  lst.className = "list";
  d.due.forEach((s) => {
    const it = document.createElement("div");
    it.className = "item-card" + (s.overdue ? " overdue" : "");
    it.innerHTML = `
      <div class="top">
        <b>${esc(s.employee_id)} ${esc(s.employee_name)}</b>
        ${badge(s.grade_code, "grade")} ${esc(s.model_name)} ${fmtSize(s.size)} 码
        ${s.overdue ? badge("已逾期 " + esc(s.due_date), "bad") : badge("到期 " + esc(s.due_date), "warn")}
      </div>
      <div class="body">${esc(s.post)} · ${esc(s.warehouse)}发放于 ${esc(s.issued_at)}，期限 ${s.lifespan_months} 个月</div>
      <div class="ops"><button class="btn sm">正常到期申请换领</button>
        <button class="btn ghost sm">查看员工</button></div>`;
    $$("button", it)[0].onclick = () => guard(async () => {
      const form = new FormData();
      form.append("issuance_id", s.id); form.append("kind", "expiry");
      form.append("reason", "到期队列换领");
      await apiForm("/api/renewals", form);
      toast("到期换领单已生成");
      await refresh();
    });
    $$("button", it)[1].onclick = () => { state.tab = "employees"; state.selectedEmp = s.employee_id; render(); };
    lst.appendChild(it);
  });
  c.appendChild(lst);
  return c;
}

/* ---------------- 损坏 / 换领审核 ---------------- */

function renderRenewals() {
  const box = document.createElement("div");
  const c = card("换领审核与领取（正常到期 / 损坏提前 / 调岗换领）");
  const isSuper = state.role !== "keeper";
  if (!isSuper) {
    c.insertAdjacentHTML("beforeend",
      `<div class="notice">当前为<b>仓管</b>角色：可办理交旧领新，但提前换领审批与旧鞋遗失豁免需切换为<b>主管·陈厂</b>。</div>`);
  }
  const rs = state.data.renewals;
  if (!rs.length) { c.insertAdjacentHTML("beforeend", `<div class="muted">暂无换领单。</div>`); box.appendChild(c); return box; }
  const lst = document.createElement("div");
  lst.className = "list";
  rs.forEach((r) => lst.appendChild(renewalItem(r, isSuper)));
  c.appendChild(lst);
  box.appendChild(c);
  return box;
}

function renewalItem(r, isSuper) {
  const it = document.createElement("div");
  const kindMap = { expiry: ["到期换领", "steel"], damage: ["损坏提前", "damage"], transfer: ["调岗换领", "hi"] };
  const [ktext, kcls] = kindMap[r.kind] || [r.kind, "gray"];
  it.className = "item-card " + (r.kind === "damage" && r.status === "pending" ? "damage" : "");
  it.innerHTML = `
    <div class="top">换领单 #${r.id}
      ${badge(ktext, kcls === "damage" ? "warn" : kcls)}
      <b>${esc(r.employee_id)} ${esc(r.employee_name)}</b>
      ${esc(r.model_name)} ${fmtSize(r.size)} 码 ${statusPill(r.status)}
      ${r.old_shoe_action === "exemption" ? badge("旧鞋豁免:" + (r.exemption_reason === "lost" ? "遗失" : "其他"), "warn") : ""}
    </div>
    <div class="body">${esc(r.reason || "—")}<br>
      <span class="small muted">${r.supervisor ? "审批人：" + esc(r.supervisor) : ""} ${r.decided_at ? "· " + esc(r.decided_at) : ""}</span></div>
    <div class="ops" data-ops></div>`;
  const ops = $("[data-ops]", it);

  if (r.kind === "damage" && r.status === "pending") {
    const view = document.createElement("button");
    view.className = "btn ghost sm"; view.textContent = `查看磨损照片(${r.photo_count})`;
    view.onclick = () => photoReviewModal(r.id);
    ops.appendChild(view);
    if (isSuper) {
      const ap = document.createElement("button");
      ap.className = "btn sm"; ap.textContent = "主管批准";
      ap.onclick = () => decideModal(r.id, true);
      const rj = document.createElement("button");
      rj.className = "btn danger sm"; rj.textContent = "驳回";
      rj.onclick = () => guard(async () => {
        await api(`/api/renewals/${r.id}/decide`, { approve: false });
        toast("已驳回"); await refresh();
      });
      ops.append(ap, rj);
    } else {
      ops.insertAdjacentHTML("beforeend", `<span class="small muted" style="align-self:center">仓管无审批权</span>`);
    }
  }
  if (r.status === "approved" || r.status === "auto") {
    const pk = document.createElement("button");
    pk.className = "btn hi sm"; pk.textContent = "领取新鞋（交旧 / 豁免）";
    pk.onclick = () => pickupModal(r);
    ops.appendChild(pk);
  }
  if (r.status === "completed") {
    ops.innerHTML = `<span class="small muted">新发放单 #${r.new_issuance_id} · 旧鞋${r.old_shoe_action === "exemption" ? "已豁免交回" : "已交回"}</span>`;
  }
  if (r.status === "rejected") {
    ops.innerHTML = `<span class="small muted">申请已驳回，可重新提交</span>`;
  }
  return it;
}

function decideModal(rid, approve) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal">
      <h2>主管审批换领单 #${rid}</h2>
      <div class="checks">
        <label><input type="checkbox" id="exempt"> 旧鞋无法交回，授予豁免</label>
        <label style="padding-left:26px">豁免原因
          <select id="exemptReason" disabled><option value="lost">旧鞋遗失</option><option value="other">其他（备注）</option></select></label>
      </div>
      <div class="foot">
        <button class="btn ghost" id="c">取消</button>
        <button class="btn" id="ok">确认批准</button>
      </div></div>`;
  document.body.appendChild(mask);
  const ex = $("#exempt", mask), rs = $("#exemptReason", mask);
  ex.onchange = () => { rs.disabled = !ex.checked; };
  $("#c", mask).onclick = () => mask.remove();
  $("#ok", mask).onclick = () => guard(async () => {
    await api(`/api/renewals/${rid}/decide`, {
      approve: true,
      grant_exemption: ex.checked,
      exemption_reason: ex.checked ? rs.value : "",
    });
    mask.remove(); toast("已批准" + (ex.checked ? "并登记旧鞋豁免" : "")); await refresh();
  });
}

function photoReviewModal(rid) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `<div class="modal"><h2>磨损照片审核 · 换领单 #${rid}</h2>
    <div class="muted small">加载中…</div>
    <div class="foot"><button class="btn ghost">关闭</button></div></div>`;
  document.body.appendChild(mask);
  $(".foot button", mask).onclick = () => mask.remove();
  mask.onclick = (e) => { if (e.target === mask) mask.remove(); };
  fetch(`/api/renewals/${rid}`).then((r) => r.json()).then((j) => {
    const { renewal, photos, issuance } = j.data;
    const bySlot = Object.fromEntries(photos.map((p) => [p.slot, p.file_path]));
    $(".muted", mask).remove();
    const slots = document.createElement("div");
    slots.className = "photo-slots";
    slots.innerHTML = ["sole", "toe", "upper"].map((slot) => {
      const name = { sole: "鞋底", toe: "鞋头", upper: "鞋帮" }[slot];
      const f = bySlot[slot];
      return `<div class="photo-slot"><div class="slot-name">${name}</div>
        ${f ? `<img src="/photos/${encodeURIComponent(f)}" alt="${name}">` : `<div class="empty">缺失</div>`}
      </div>`;
    }).join("");
    mask.querySelector(".modal").insertBefore(slots, $(".foot", mask));
    mask.querySelector(".modal").insertAdjacentHTML("beforeend",
      `<div class="body small muted" style="margin-top:8px">${esc(renewal.reason)}</div>`);
  });
}

function pickupModal(r) {
  const exempt = r.old_shoe_action === "exemption";
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal">
      <h2>领取新鞋 · 换领单 #${r.id}</h2>
      ${exempt
        ? `<div class="notice">主管已登记<b>${r.exemption_reason === "lost" ? "旧鞋遗失" : "其他原因"}</b>豁免，本次无需交回旧鞋。</div>`
        : `<div class="notice">领取新鞋前必须交回旧鞋：可再用鞋进入<b>清洁复检</b>，报废鞋进入<b>待处置批次</b>。</div>`}
      <div class="form-grid">
        <label class="fld">新鞋鞋款<select id="pkModel"></select></label>
        <label class="fld">新鞋尺码<select id="pkSize"></select></label>
        <label class="fld">发放仓库<select id="pkWh"><option>A仓</option><option>B仓</option></select></label>
        ${exempt ? "" : `<label class="fld">旧鞋状况<select id="pkCond">
          <option value="reusable">可再用 → 清洁复检</option>
          <option value="scrap">报废 → 待处置批次</option></select></label>`}
      </div>
      <div class="foot"><button class="btn ghost" id="c">取消</button>
        <button class="btn hi" id="ok">确认领取</button></div>
    </div>`;
  document.body.appendChild(mask);
  const modelSel = $("#pkModel", mask);
  Object.entries(state.data.matrix).forEach(([code, m]) => {
    modelSel.insertAdjacentHTML("beforeend", `<option value="${code}">${code} ${esc(m.name)}</option>`);
  });
  // 默认选员工当前防护等级的授权鞋款（调岗换领单上记录的是旧鞋款，不能直接用）
  const empNow = state.data.employees.find((e) => e.id === r.employee_id);
  const gradeModel = empNow && Object.entries(state.data.matrix)
    .find(([, m]) => m.grade === empNow.grade_code)?.[0];
  modelSel.value = gradeModel || r.model_code;
  const sizeSel = $("#pkSize", mask);
  const fill = () => {
    const m = state.data.matrix[modelSel.value];
    sizeSel.innerHTML = m.sizes.map((s) =>
      `<option value="${s}" ${s === r.size ? "selected" : ""}>${fmtSize(s)}</option>`).join("");
  };
  modelSel.onchange = fill; fill();
  $("#c", mask).onclick = () => mask.remove();
  $("#ok", mask).onclick = () => guard(async () => {
    await api(`/api/renewals/${r.id}/pickup`, {
      model_code: modelSel.value, size: parseFloat(sizeSel.value),
      warehouse: $("#pkWh", mask).value,
      old_condition: exempt ? "reusable" : $("#pkCond", mask).value,
    });
    mask.remove(); toast("领取完成，链路已更新"); await refresh();
  });
}

/* ---------------- 旧鞋处置 ---------------- */

function renderReturns() {
  const box = document.createElement("div");
  const c1 = card("旧鞋回收与清洁复检");
  const rs = state.data.returns;
  if (!rs.length) {
    c1.insertAdjacentHTML("beforeend", `<div class="muted">暂无回收记录。</div>`);
  } else {
    const lst = document.createElement("div");
    lst.className = "list";
    rs.forEach((r) => {
      const it = document.createElement("div");
      it.className = "item-card";
      const cond = r.condition === "reusable"
        ? (r.recon_status === "passed" ? badge("复检合格可再用", "ok")
           : r.recon_status === "failed" ? badge("复检不合格转报废", "bad")
           : badge("可再用·待复检", "steel"))
        : badge("报废·待处置", "bad");
      it.innerHTML = `
        <div class="top">回收单 #${r.id} <b>${esc(r.employee_name)}</b>
        ${esc(r.model_name)} ${fmtSize(r.size)} 码 ${cond}</div>
        <div class="body">收于 ${esc(r.received_at)} · ${esc(r.warehouse)}
        ${r.note ? " · " + esc(r.note) : ""}</div>
        <div class="ops" data-ops></div>`;
      const ops = $("[data-ops]", it);
      if (r.recon_status === "pending") {
        const ok = document.createElement("button");
        ok.className = "btn sm"; ok.textContent = "复检合格";
        ok.onclick = () => guard(async () => {
          await api(`/api/returns/${r.id}/inspect`, { passed: true, note: "清洁消毒后合格" });
          toast("复检合格"); await refresh();
        });
        const bad = document.createElement("button");
        bad.className = "btn danger sm"; bad.textContent = "复检不合格（转报废批次）";
        bad.onclick = () => guard(async () => {
          await api(`/api/returns/${r.id}/inspect`, { passed: false, note: "清洁后仍不达标" });
          toast("已转入待处置批次"); await refresh();
        });
        ops.append(ok, bad);
      }
      lst.appendChild(it);
    });
    c1.appendChild(lst);
  }
  box.appendChild(c1);

  const c2 = card("报废处置批次");
  const dl = document.createElement("div");
  dl.className = "list";
  state.data.disposals.forEach((d) => {
    const it = document.createElement("div");
    it.className = "item-card";
    it.innerHTML = `
      <div class="top">${badge(d.status === "done" ? "已处置" : "待处置", d.status === "done" ? "ok" : "warn")}
        <b>${esc(d.batch_no)}</b> <span class="muted small">${d.items.length} 双</span></div>
      <div class="body">${d.items.map((i) => esc(i.model_code) + " " + fmtSize(i.size)).join("、") || "空批次"}
      ${d.receipt_no ? `<br>处置厂商：${esc(d.vendor)} · 回执号 <b>${esc(d.receipt_no)}</b>（${esc(d.done_at || "")}）` : ""}</div>
      <div class="ops" data-ops></div>`;
    if (d.status === "pending" && d.items.length) {
      const btn = document.createElement("button");
      btn.className = "btn hi sm"; btn.textContent = "联系厂商处置出厂";
      btn.onclick = () => guard(async () => {
        const j = await api(`/api/disposals/${d.id}/execute`, {});
        toast(`处置完成，回执号 ${j.receipt_no}`); await refresh();
      });
      $("[data-ops]", it).appendChild(btn);
    }
    dl.appendChild(it);
  });
  c2.appendChild(dl);
  box.appendChild(c2);
  return box;
}

/* ---------------- 启动 ---------------- */

$$("#nav button").forEach((b) => {
  b.onclick = () => {
    state.tab = b.dataset.tab;
    state.selectedEmp = null;
    $$("#nav button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  };
});
$("#actorRole").onchange = async (e) => {
  state.role = e.target.value;
  await refresh();
};

(async function init() {
  try {
    await refresh();
    setInterval(() => refresh().catch(() => {}), 45000);
  } catch (e) {
    $("#app").innerHTML = `<div class="card"><h2>无法连接服务</h2>
      <div class="notice">${esc(e.message)}。请运行 <b>./run.sh</b> 启动系统。</div></div>`;
  }
})();
