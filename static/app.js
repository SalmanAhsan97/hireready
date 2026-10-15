const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const state = { resumeText: "", job: null, analysis: null, kit: null };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\[([^\]]+)\]/g, '<span class="ph">[$1]</span>');

function markdown(md) {
  let html = "", inList = false;
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    if (inList && !bullet) { html += "</ul>"; inList = false; }
    const h = line.match(/^(#{1,3})\s+(.*)/);
    if (h) html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`;
    else if (bullet) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(bullet[1])}</li>`; }
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 1800);
}

function setStep(n) {
  $$(".steps li").forEach((li) => {
    const s = +li.dataset.step;
    li.classList.toggle("active", s === n);
    li.classList.toggle("done", s < n);
  });
}

function showError(msg) {
  const e = $("#form-error");
  e.textContent = msg; e.hidden = !msg;
}

const store = {
  get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};
const accessHeaders = () => {
  const code = $("#access_code").value.trim();
  return code ? { "X-Access-Code": code } : {};
};
function needAccessCode() {
  $("#access-wrap").hidden = false;
  $("#access_code").focus();
}

function apiError(body) {
  const d = body.detail;
  const err = new Error(typeof d === "string" ? d : d?.message || "Something went wrong.");
  err.code = d?.code;
  return err;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), ...accessHeaders() } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw apiError(body);
  return body;
}

// multipart POST with real upload progress (fetch can't report it)
function postForm(path, fd, onUpload) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", path);
    Object.entries(accessHeaders()).forEach(([k, v]) => x.setRequestHeader(k, v));
    x.upload.onprogress = (e) => e.lengthComputable && onUpload(e.loaded / e.total);
    x.upload.onload = () => onUpload(1);
    x.onload = () => {
      let body = {};
      try { body = JSON.parse(x.responseText); } catch {}
      x.status >= 200 && x.status < 300 ? resolve(body) : reject(apiError(body));
    };
    x.onerror = () => reject(new Error("Network error — check your connection and try again."));
    x.send(fd);
  });
}

// Staged loader: real upload progress first, then timed stages that never claim to finish before the server does.
const loader = {
  start(stages, expectedSec, { upload = false } = {}) {
    clearInterval(this.timer);
    Object.assign(this, { stages, expected: expectedSec * 1000, i: 0, pct: 0, t0: Date.now(), tProc: upload ? null : Date.now(), upload });
    const el = $("#loading");
    el.hidden = false;
    el.classList.toggle("uploading", upload);
    el.classList.toggle("processing", !upload);
    $("#stages").innerHTML = stages.map((s) => `<li>${esc(s)}</li>`).join("");
    this.paint();
    this.timer = setInterval(() => this.tick(), 200);
  },
  setUpload(frac) {
    if (!this.upload || this.tProc) return;
    this.pct = Math.max(this.pct, frac * 12);
    if (frac >= 1) {
      this.tProc = Date.now();
      $("#loading").classList.replace("uploading", "processing");
      this.next();
    }
    this.paint();
  },
  tick() {
    if (this.tProc) {
      const t = Date.now() - this.tProc;
      const base = this.upload ? 12 : 0;
      this.pct = Math.max(this.pct, base + (95 - base) * (1 - Math.exp(-t / (this.expected / 2))));
      // advance through the middle stages on a schedule, holding on the last one until the response arrives
      const want = Math.min(this.stages.length - 1, (this.upload ? 1 : 0) + Math.floor(t / (this.expected / this.stages.length)));
      while (this.i < want) this.next();
    }
    this.paint();
  },
  next() { if (this.i < this.stages.length - 1) this.i++; this.paint(); },
  paint() {
    $$("#stages li").forEach((li, k) => {
      li.classList.toggle("done", k < this.i);
      li.classList.toggle("active", k === this.i);
    });
    $("#loading-text").textContent = this.stages[this.i] + "…";
    $("#progress-fill").style.width = this.pct.toFixed(1) + "%";
    $("#progress-label").textContent = Math.floor(this.pct) + "%";
    const sec = Math.floor((Date.now() - this.t0) / 1000);
    $("#progress-elapsed").textContent = sec >= 3 ? `${sec}s` : "";
  },
  async finish() {
    clearInterval(this.timer);
    this.i = this.stages.length;
    this.pct = 100;
    this.paint();
    $("#loading-text").textContent = "Done";
    await new Promise((r) => setTimeout(r, 450));
    this.stop();
  },
  stop() { clearInterval(this.timer); $("#loading").hidden = true; },
};

// ---- Upload ----
const drop = $("#drop"), fileInput = $("#resume");
function setFile(f) {
  if (!f) return;
  const dt = new DataTransfer(); dt.items.add(f); fileInput.files = dt.files;
  drop.classList.remove("has-file");
  $("#drop-icon").textContent = "⬆";
  $("#drop-text").innerHTML = `<strong>${esc(f.name)}</strong><br><small>Loading ${(f.size / 1024).toFixed(0)} KB…</small>`;
  const bar = $("#file-progress"), fill = bar.firstElementChild;
  bar.hidden = false; fill.style.width = "0%";
  const t0 = Date.now(), reader = new FileReader();
  reader.onprogress = (e) => e.lengthComputable && (fill.style.width = (85 * e.loaded / e.total).toFixed(0) + "%");
  reader.onloadend = () => setTimeout(() => {
    fill.style.width = "100%";
    setTimeout(() => {
      bar.hidden = true;
      drop.classList.add("has-file");
      $("#drop-icon").textContent = "✓";
      $("#drop-text").innerHTML = `<strong>${esc(f.name)}</strong><br><small>${(f.size / 1024).toFixed(0)} KB · ready · click to change</small>`;
    }, 250);
  }, Math.max(0, 500 - (Date.now() - t0)));  // keep it visible long enough to read
  reader.readAsArrayBuffer(f);
}
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

$("#analyze-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  const url = $("#job_url").value.trim(), text = $("#job_text").value.trim();
  if (!fileInput.files[0]) return showError("Add your resume first.");
  const withJob = !!(url || text);

  const fd = new FormData();
  fd.append("resume", fileInput.files[0]);
  fd.append("job_url", url);
  fd.append("job_text", text);

  $("#analyze-btn").disabled = true;
  $("#results").hidden = $("#kit").hidden = true;
  $("#input-card").after($("#loading"));
  const stages = withJob
    ? ["Uploading your resume", "Reading your resume", ...(url && !text ? ["Fetching the job posting"] : []), "Matching each requirement", "Calculating your score", "Writing your edits"]
    : ["Uploading your resume", "Reading your resume", "Spotting skills & keywords", "Calculating your score", "Writing your edits"];
  loader.start(stages, withJob ? 50 : 45, { upload: true });
  let ok = false;
  try {
    const data = await postForm("/api/analyze", fd, (f) => loader.setUpload(f));
    await loader.finish();
    ok = true;
    store.set("hireready_access_code", $("#access_code").value.trim());
    state.resumeText = data.resume_text; state.job = data.job; state.analysis = data.analysis; state.kit = null;
    renderAnalysis();
  } catch (err) {
    showError(err.message);
    if (err.code === "access_code") needAccessCode();
    if (err.code === "job_fetch_failed") { $("#paste-box").open = true; $("#job_text").focus(); }
  } finally {
    if (!ok) loader.stop();
    $("#analyze-btn").disabled = false;
  }
});

// ---- Results ----
function listInto(sel, items, empty) {
  $(sel).innerHTML = items.length ? items.map((x) => `<li>${esc(x)}</li>`).join("") : `<li class="empty">${empty}</li>`;
}

function renderAnalysis() {
  const a = state.analysis, job = state.job;
  setStep(2);
  $("#results").hidden = false;

  const score = Math.max(0, Math.min(100, a.overall_score));
  const color = score >= 80 ? "var(--good)" : score >= 65 ? "var(--good)" : score >= 45 ? "var(--warn)" : "var(--bad)";
  const fg = $("#ring-fg");
  fg.style.stroke = color;
  requestAnimationFrame(() => (fg.style.strokeDashoffset = 326.7 * (1 - score / 100)));
  let n = 0;
  const tick = setInterval(() => { n = Math.min(score, n + 2); $("#score-num").textContent = n; if (n >= score) clearInterval(tick); }, 16);

  const resumeOnly = a.mode === "resume";
  const title = a.job_title || job.title, company = a.company || job.company;
  $("#job-line").textContent = resumeOnly
    ? `General resume score${title ? " · best fit: " + title : ""}`
    : [title, company].filter(Boolean).join(" · ");
  $("#breakdown-note").textContent = resumeOnly
    ? "Calculated from your resume alone — the same resume always gets the same score. Add a job link for a match score."
    : "Calculated from the requirement check below — the same resume and job always get the same score.";
  $("#kw-have-title").textContent = resumeOnly ? `Expected for ${title || "this role"} — you have` : "In your resume";
  $("#kw-miss-title").textContent = resumeOnly ? "Expected — missing" : "Missing from your resume";
  $("#cta-title").textContent = resumeOnly ? "Applying for a specific job?" : "Ready to apply?";
  $("#cta-text").textContent = resumeOnly
    ? "Add the LinkedIn job link to see how you match that role and get a tailored resume, cover letter and outreach."
    : "Get a tailored resume, cover letter, recruiter outreach and interview prep for this exact role.";
  $("#kit-btn").textContent = resumeOnly ? "Add a job link" : "Build my application kit";
  const src = $("#job-source"), sources = job.sources || [];
  src.hidden = !sources.length;
  src.textContent = sources.length ? "Job details from " + sources.join(" + ") : "";
  const st = $("#standing");
  st.textContent = a.standing;
  st.className = "standing s-" + a.standing.split(" ")[0];
  $("#verdict").textContent = a.verdict;
  const cap = $("#cap-note"), capReason = a.coverage?.cap_reason;
  cap.hidden = !capReason;
  cap.textContent = capReason ? `${capReason} (would be ${a.coverage.uncapped_score} otherwise).` : "";
  const link = $("#job-link");
  link.hidden = !job.url; link.href = job.url || "#";

  $("#categories").innerHTML = a.categories.map((c) => `
    <div class="cat">
      <div class="cat-head"><span>${esc(c.name)} <small>· ${c.weight}%</small></span><span>${c.score}</span></div>
      <div class="bar"><div data-w="${c.score}"></div></div>
      ${c.detail ? `<p class="cat-detail">${esc(c.detail)}</p>` : ""}
      <p>${esc(c.rationale)}</p>
    </div>`).join("");
  requestAnimationFrame(() => $$(".bar > div").forEach((b) => (b.style.width = b.dataset.w + "%")));

  renderRequirements(a);

  $("#kw-matched").innerHTML = a.matched_keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("") || "<small>None found</small>";
  $("#kw-missing").innerHTML = a.missing_keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("") || "<small>Nothing major missing</small>";
  listInto("#strengths", a.strengths, "—");
  listInto("#gaps", a.gaps, "No major gaps");
  listInto("#red-flags", a.red_flags, "None spotted");

  $("#changes").innerHTML = a.changes.map((c, i) => `
    <div class="change">
      <div class="change-head"><span class="prio ${c.priority}">${c.priority}</span><span class="change-section">${esc(c.section)}</span></div>
      <div class="change-issue">${esc(c.issue)}</div>
      <div class="diff">
        ${c.current ? `<div class="old">${esc(c.current)}</div>` : ""}
        <div class="new">${inline(c.suggested)}<button class="btn ghost small copy" data-change="${i}">Copy</button></div>
      </div>
      <p class="why">${esc(c.why)}</p>
    </div>`).join("");

  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

const STATUS = { met: ["✓", "Met"], partial: ["◐", "Partly"], missing: ["✕", "Missing"] };

function coverageMeter(label, total, met, partial, pct) {
  if (!total) return `<div class="meter"><div class="meter-head"><span>${label}</span><span>none listed</span></div></div>`;
  return `<div class="meter">
    <div class="meter-head"><span>${label}</span><span><strong>${met}/${total}</strong> met${partial ? ` · ${partial} partly` : ""}</span></div>
    <div class="seg">${[["met", met], ["partial", partial], ["missing", total - met - partial]]
      .map(([k, n]) => (n ? `<div class="${k}" style="flex:${n}"></div>` : "")).join("")}</div>
    <small>${pct}% coverage</small>
  </div>`;
}

function renderRequirements(a) {
  const reqs = a.requirements || [], cov = a.coverage;
  const card = $("#requirements").closest(".card");
  card.hidden = !reqs.length || !cov;
  if (card.hidden) return;
  $("#coverage").innerHTML =
    coverageMeter("Must-haves", cov.must_total, cov.must_met, cov.must_partial, cov.must_pct) +
    coverageMeter("Nice-to-haves", cov.nice_total, cov.nice_met, cov.nice_partial, cov.nice_pct);
  const group = (imp, title) => {
    const rows = reqs.filter((r) => r.importance === imp);
    if (!rows.length) return "";
    return `<h3 class="sub">${title}</h3><ul class="reqs">${rows.map((r) => `
      <li class="req ${r.status}">
        <span class="req-icon" title="${STATUS[r.status][1]}">${STATUS[r.status][0]}</span>
        <div>
          <div class="req-text">${esc(r.text)} <span class="req-kind">${esc(r.kind)}</span></div>
          ${r.evidence ? `<div class="req-evidence">“${esc(r.evidence)}”</div>` : ""}
          ${r.note ? `<div class="req-note">${esc(r.note)}</div>` : ""}
        </div>
        <span class="req-status">${STATUS[r.status][1]}</span>
      </li>`).join("")}</ul>`;
  };
  $("#requirements").innerHTML = group("must", "Must-have") + group("nice", "Nice-to-have");
}

// ---- Kit ----
$("#kit-btn").addEventListener("click", async () => {
  if (state.analysis?.mode === "resume") {
    $("#input-card").scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(() => $("#job_url").focus({ preventScroll: true }), 400);
    return;
  }
  $("#kit-btn").disabled = true;
  $("#kit").hidden = true;
  $("#results").after($("#loading"));
  loader.start(["Rewriting your resume for this role", "Drafting your cover letter", "Writing recruiter outreach", "Preparing interview talking points"], 30);
  let ok = false;
  try {
    const data = await api("/api/apply-kit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume_text: state.resumeText, job: state.job, analysis: state.analysis }),
    });
    await loader.finish();
    ok = true;
    state.kit = data.kit;
    renderKit();
  } catch (err) {
    toast(err.message);
  } finally {
    if (!ok) loader.stop();
    $("#kit-btn").disabled = false;
  }
});

function renderKit() {
  const k = state.kit;
  setStep(3);
  $("#kit").hidden = false;
  $("#positioning").textContent = k.positioning_statement;
  $("#proj-from").textContent = state.analysis.overall_score;
  $("#proj-to").textContent = k.projected_score;
  $("#resume-doc").innerHTML = markdown(k.tailored_resume_markdown);
  $("#letter-doc").innerHTML = inline(k.cover_letter);
  $("#note").textContent = k.linkedin_connection_note;
  const len = k.linkedin_connection_note.length, cnt = $("#note-count");
  cnt.textContent = `${len}/300`; cnt.classList.toggle("over", len > 300);
  $("#dm").textContent = k.recruiter_message;
  $("#email-subject").textContent = "Subject: " + k.hiring_manager_email_subject;
  $("#email-body").textContent = k.hiring_manager_email_body;
  listInto("#contacts", k.who_to_contact, "—");
  $("#talking-points").innerHTML = k.interview_talking_points.map((t) => `<div class="tp"><h3>${esc(t.question)}</h3><p>${esc(t.answer_outline)}</p></div>`).join("");
  $("#checklist").innerHTML = k.application_checklist.map((c) => `<li><input type="checkbox"><span>${esc(c)}</span></li>`).join("");
  const apply = $("#apply-link");
  apply.hidden = !state.job.url; apply.href = state.job.url || "#";
  selectTab("resume");
  $("#kit").scrollIntoView({ behavior: "smooth", block: "start" });
}

function selectTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tab-panel").forEach((p) => (p.hidden = p.dataset.panel !== name));
}
$$(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));

$("#checklist").addEventListener("change", (e) => e.target.closest("li").classList.toggle("done", e.target.checked));

// ---- Copy / download ----
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".copy");
  if (!btn) return;
  const k = state.kit;
  let text;
  if (btn.dataset.change !== undefined) text = state.analysis.changes[+btn.dataset.change].suggested;
  else text = {
    resume: k?.tailored_resume_markdown,
    letter: k?.cover_letter,
    note: k?.linkedin_connection_note,
    dm: k?.recruiter_message,
    email: k && `Subject: ${k.hiring_manager_email_subject}\n\n${k.hiring_manager_email_body}`,
  }[btn.dataset.copy];
  if (!text) return;
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Couldn't access the clipboard"); }
});

$("#dl-docx").addEventListener("click", async () => {
  const name = (state.analysis.candidate_name || "resume") + " - " + (state.analysis.company || state.job.company || "tailored");
  const res = await fetch("/api/export/docx", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...accessHeaders() },
    body: JSON.stringify({ markdown: state.kit.tailored_resume_markdown, filename: name }),
  });
  if (!res.ok) return toast("Export failed");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = name.replace(/[^\w-]+/g, "_") + ".docx";
  a.click();
  URL.revokeObjectURL(a.href);
});

// ---- Status ----
// ---- Splash: play the intro once per visit (short on reloads), then fade out ----
(() => {
  const splash = $("#splash");
  let seen = false;
  try { seen = sessionStorage.getItem("hr_splash") === "1"; sessionStorage.setItem("hr_splash", "1"); } catch {}
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const minMs = reduce ? 200 : seen ? 700 : 1700;
  const t0 = performance.now();
  const hide = () => setTimeout(() => {
    splash.classList.add("out");
    setTimeout(() => splash.remove(), 500);
  }, Math.max(0, minMs - (performance.now() - t0)));
  document.readyState === "complete" ? hide() : addEventListener("load", hide, { once: true });
})();

$("#access_code").value = store.get("hireready_access_code");
api("/api/status").then((s) => {
  $("#demo-pill").hidden = !s.demo;
  if (s.access_code_required) $("#access-wrap").hidden = false;
}).catch(() => {});
