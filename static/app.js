"use strict";

const $ = (id) => document.getElementById(id);
const money = (n) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(n || 0);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
}

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function emptyRow(cols, text) {
  return `<tr><td colspan="${cols}" class="empty-cell" style="text-align:center;padding:30px;color:#7b8596">${text}</td></tr>`;
}

// ---------------------------------------------------------------- dashboard
function bars(target, items) {
  const el = $(target);
  if (!items.length) {
    el.innerHTML = '<div class="empty-upload">No data yet — upload a file.</div>';
    return;
  }
  const max = Math.max(...items.map((x) => x.value), 1);
  el.innerHTML = items.map((x) => `
    <div class="bar-row">
      <span>${escapeHtml(x.name)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(5, (x.value / max) * 100)}%"></div></div>
      <span class="bar-value">${x.value}</span>
    </div>`).join("");
}

async function loadSummary() {
  const d = await get("/api/summary");
  $("raw").textContent = d.raw_returns;
  $("events").textContent = d.unique_events;
  $("duplicates").textContent = d.duplicates_suppressed;
  $("chargeCount").textContent = d.charges;
  $("chargeTotal").textContent = money(d.charge_total);
  $("eventsNote").textContent = d.needs_review
    ? `After deduplication · ${d.needs_review} need manual review`
    : "After deduplication";
  bars("instrumentChart", d.by_instrument);
  bars("reasonChart", d.by_reason);
  bars("bankChart", d.by_bank);
}

async function loadMeta() {
  const d = await get("/api/meta");
  const bank = $("bank").value, reason = $("reason").value;
  $("bank").innerHTML = '<option value="">All banks</option>' +
    d.banks.map((x) => `<option>${escapeHtml(x)}</option>`).join("");
  $("reason").innerHTML = '<option value="">All reasons</option>' +
    d.reasons.map((x) => `<option value="${escapeHtml(x.canonical_code)}">${escapeHtml(x.display_name)}</option>`).join("");
  $("bank").value = bank;
  $("reason").value = reason;
}

async function loadEvents() {
  const p = new URLSearchParams({
    q: $("search").value, instrument: $("instrument").value,
    bank: $("bank").value, reason: $("reason").value,
  });
  const rows = await get("/api/events?" + p);
  $("eventRows").innerHTML = rows.length ? rows.map((r) => `
    <tr class="clickable" data-id="${r.id}" tabindex="0">
      <td><div class="customer">${escapeHtml(r.customer_name)}</div><div class="sub">${escapeHtml(r.account_no)}</div></td>
      <td><span class="pill ${r.instrument_type === "CHEQUE" ? "cheque" : ""}">${escapeHtml(r.instrument_type)}</span></td>
      <td><b>${escapeHtml(r.primary_ref)}</b><div class="sub">${escapeHtml(r.secondary_ref)}</div></td>
      <td>${escapeHtml(r.sponsor_bank)}</td>
      <td>${escapeHtml(r.reason)}${r.canonical_code === "UNMAPPED" ? ' <span class="pill review">review</span>' : ""}</td>
      <td>${money(r.amount)}</td>
      <td><b>${r.attempt_count}</b>${r.attempt_count > 1 ? '<div class="sub">re-presented</div>' : ""}</td>
      <td>${r.charged ? `<span class="charge">${money(r.charge_amount)}</span>` : '<span class="nocharge">Not charged</span>'}</td>
    </tr>`).join("") : emptyRow(8, "No matching events");
}

async function loadCharges() {
  const rows = await get("/api/charges");
  $("chargeRows").innerHTML = rows.length ? rows.map((r) => `
    <tr class="clickable" data-id="${r.bounce_event_id}" tabindex="0">
      <td><div class="customer">${escapeHtml(r.customer_name)}</div><div class="sub">${escapeHtml(r.account_no)}</div></td>
      <td><span class="pill ${r.instrument_type === "CHEQUE" ? "cheque" : ""}">${escapeHtml(r.instrument_type)}</span></td>
      <td><b>${escapeHtml(r.primary_ref)}</b></td>
      <td>${escapeHtml(r.reason)}</td>
      <td><span class="charge">${money(r.charge_amount)}</span></td>
      <td class="notice-cell">${escapeHtml(r.notice_text)}</td>
    </tr>`).join("") : emptyRow(6, "No charges raised yet");
}

async function loadUploads() {
  const rows = await get("/api/uploads");
  $("uploadRows").innerHTML = rows.length ? rows.map((r) => `<tr>
    <td><b>${escapeHtml(r.file_name)}</b>${r.error_message ? `<div class="sub">${escapeHtml(r.error_message)}</div>` : ""}</td>
    <td>${escapeHtml(r.sponsor_bank || "—")}</td><td>${escapeHtml(r.instrument_type || "—")}</td>
    <td>${r.row_count}</td>
    <td><span class="status-pill ${escapeHtml(String(r.status).toLowerCase())}">${escapeHtml(r.status)}</span></td>
    <td>${new Date(r.uploaded_at).toLocaleString()}</td>
  </tr>`).join("") : emptyRow(6, "No uploads yet");
}

function setStatus(ok) {
  $("statusDot").classList.toggle("down", !ok);
  $("statusText").textContent = ok ? "Database connected" : "Cannot reach the server";
}

async function loadAll() {
  try {
    await Promise.all([loadSummary(), loadMeta(), loadCharges(), loadUploads()]);
    await loadEvents();
    setStatus(true);
    document.querySelector(".banner")?.remove();
  } catch (e) {
    console.error(e);
    setStatus(false);
    if (!document.querySelector(".banner")) {
      document.querySelector(".topbar").insertAdjacentHTML("afterend",
        '<div class="banner">Could not load the dashboard. Make sure the Flask app is running (<code>python app.py</code>).</div>');
    }
  }
}

// ------------------------------------------------------------ detail drawer
async function openEvent(id) {
  const r = await get("/api/events/" + id);
  const refLabel = r.instrument_type === "NACH" ? "UMRN" : "Cheque number";
  $("drawerContent").innerHTML = `
    <div class="detail-kicker">${escapeHtml(r.instrument_type)} bounce event</div>
    <h2>${escapeHtml(r.customer_name)}</h2>
    <p style="color:#687386">${refLabel}: <b>${escapeHtml(r.primary_ref)}</b></p>
    <div class="detail-grid">
      <div class="detail-box"><span>Bank</span><b>${escapeHtml(r.sponsor_bank)}</b></div>
      <div class="detail-box"><span>Reason</span><b>${escapeHtml(r.reason)}</b></div>
      <div class="detail-box"><span>Amount</span><b>${money(r.amount)}</b></div>
      <div class="detail-box"><span>Attempts</span><b>${r.attempt_count}</b></div>
      <div class="detail-box"><span>Charge status</span><b>${r.charge_amount ? money(r.charge_amount) : "Not charged"}</b></div>
      <div class="detail-box"><span>Category</span><b>${escapeHtml(r.category || "—")}</b></div>
    </div>
    <h3>Presentation history</h3>
    ${r.attempts.map((a) => `
      <div class="attempt"><b>Attempt ${a.presentation_seq}</b> · ${escapeHtml(a.return_date)}
        <div class="sub">${escapeHtml(a.sponsor_bank)} · raw code ${escapeHtml(a.raw_code)}</div>
        <div style="margin-top:4px">${escapeHtml(a.raw_description)}</div>
      </div>`).join("")}
    ${r.notice_text ? `<h3>Generated customer notice</h3><div class="notice">${escapeHtml(r.notice_text)}</div>` : ""}`;
  $("drawer").classList.add("open");
  $("drawerBackdrop").classList.remove("hidden");
  $("drawerClose").focus();
}

function closeDrawer() {
  $("drawer").classList.remove("open");
  $("drawerBackdrop").classList.add("hidden");
}

function rowActivate(ev) {
  if (ev.type === "keydown" && ev.key !== "Enter") return;
  const tr = ev.target.closest("tr[data-id]");
  if (tr) openEvent(tr.dataset.id).catch((e) => console.error(e));
}

// ----------------------------------------------------------- upload workflow
let selectedFiles = [];

function renderFileList() {
  $("fileList").innerHTML = selectedFiles.length
    ? selectedFiles.map((f, i) => `<div class="file-chip"><span>${escapeHtml(f.name)}</span>
        <small>${(f.size / 1024).toFixed(1)} KB</small>
        <button type="button" data-remove="${i}" aria-label="Remove ${escapeHtml(f.name)}">×</button></div>`).join("")
    : '<div class="empty-upload">No files selected yet.</div>';
}

function addFiles(files) {
  for (const f of files) {
    if (!selectedFiles.some((x) => x.name === f.name && x.size === f.size && x.lastModified === f.lastModified)) {
      selectedFiles.push(f);
    }
  }
  renderFileList();
}

function showUploadResult(type, html) {
  const el = $("uploadResult");
  el.className = `upload-result ${type}`;
  el.innerHTML = html;
}

async function uploadSelected() {
  if (!selectedFiles.length) {
    showUploadResult("error", "Select at least one file first.");
    return;
  }
  const fd = new FormData();
  selectedFiles.forEach((f) => fd.append("files", f));
  const btn = $("uploadBtn");
  btn.disabled = true;
  btn.textContent = "Processing…";
  try {
    const r = await fetch("/api/uploads", { method: "POST", body: fd });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `Upload failed (${r.status})`);

    const failed = d.results.filter((x) => x.status === "failed");
    const lines = d.results.map((x) => {
      const state = x.status === "success"
        ? `${escapeHtml(x.bank)} ${escapeHtml(x.instrument_type)} · ${x.source_rows} rows read, ${x.rows_added} new`
        : x.status === "duplicate"
          ? '<span class="warn">Already uploaded — nothing changed</span>'
          : `Failed — ${escapeHtml(x.message)}`;
      return `<li><b>${escapeHtml(x.file_name)}</b> — ${state}</li>`;
    }).join("");
    const title = failed.length === d.results.length ? "Upload failed"
      : failed.length ? "Some files need attention" : "Upload complete";
    showUploadResult(failed.length ? "error" : "success", `<strong>${title}</strong><ul>${lines}</ul>`);
    selectedFiles = [];
    $("fileInput").value = "";
    renderFileList();
    await loadAll();
  } catch (e) {
    showUploadResult("error", `<strong>Upload failed</strong><div>${escapeHtml(e.message)}</div>`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Upload & Process";
  }
}

// -------------------------------------------------------------------- wiring
let searchTimer;
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadEvents().catch(console.error), 250); });
["instrument", "bank", "reason"].forEach((id) => $(id).addEventListener("change", () => loadEvents().catch(console.error)));
$("refreshBtn").addEventListener("click", loadAll);

["eventRows", "chargeRows"].forEach((id) => {
  $(id).addEventListener("click", rowActivate);
  $(id).addEventListener("keydown", rowActivate);
});
$("drawerClose").addEventListener("click", closeDrawer);
$("drawerBackdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

const dropZone = $("dropZone"), fileInput = $("fileInput");
fileInput.addEventListener("change", () => addFiles(fileInput.files));
["dragenter", "dragover"].forEach((e) => dropZone.addEventListener(e, (ev) => { ev.preventDefault(); dropZone.classList.add("dragging"); }));
["dragleave", "drop"].forEach((e) => dropZone.addEventListener(e, (ev) => { ev.preventDefault(); dropZone.classList.remove("dragging"); }));
dropZone.addEventListener("drop", (ev) => addFiles(ev.dataTransfer.files));
dropZone.addEventListener("click", (ev) => { if (ev.target.tagName !== "INPUT") fileInput.click(); });
dropZone.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fileInput.click(); } });
$("fileList").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-remove]");
  if (btn) { selectedFiles.splice(Number(btn.dataset.remove), 1); renderFileList(); }
});
$("clearFiles").addEventListener("click", () => {
  selectedFiles = [];
  fileInput.value = "";
  renderFileList();
  $("uploadResult").className = "upload-result hidden";
});
$("uploadBtn").addEventListener("click", uploadSelected);

renderFileList();
loadAll();
