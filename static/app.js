const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const state = { resumeText: "", job: null, analysis: null, kit: null, chat: { messages: [], md: "" }, preview: false, locked: null, contact: "rnwedesign@gmail.com" };

const LOCK_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="7" width="10" height="7" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/></svg>';
const mailto = () => `mailto:${state.contact}?subject=${encodeURIComponent("Screenproof — full access & pricing")}&body=${encodeURIComponent("Hi,\n\nI tried the Screenproof preview and I'd like the full version. Could you share pricing?\n\nThanks")}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const lockBox = (title, sub, tall = false) => `
  <div class="locked ${tall ? "tall" : ""}">
    <div class="locked-blur" aria-hidden="true"><span></span><span></span><span></span></div>
    <div class="locked-body">
      <div class="lock-icon">${LOCK_SVG}</div>
      <strong>${esc(title)}</strong>
      <p>${esc(sub)}</p>
      <a class="btn primary small" href="${mailto()}">Contact admin for full access</a>
    </div>
  </div>`;
const lockLine = (text) => `<span class="lock-line">${LOCK_SVG}${esc(text)}</span>`;

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

// Headline types itself out once the splash lifts: sentence by sentence, with a pause between.
const headline = $("#headline");
if (headline && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const text = headline.textContent;
  headline.innerHTML = [...text].map((c) => `<span class="tw-char" aria-hidden="true">${esc(c)}</span>`).join("");
}
function typeHeadline() {
  const chars = headline ? [...headline.querySelectorAll(".tw-char")] : [];
  if (!chars.length) return;
  const caret = document.createElement("span");
  caret.className = "tw-caret";
  caret.setAttribute("aria-hidden", "true");
  let n = 0;
  const tick = () => {
    const c = chars[n++];
    c.classList.add("on");
    c.after(caret);
    if (n === chars.length) { caret.classList.add("done"); return; }
    const pause = /[.!?]/.test(c.textContent) ? 520 : c.textContent === " " ? 45 : 38 + Math.random() * 40;
    setTimeout(tick, pause);
  };
  headline.prepend(caret);
  setTimeout(tick, 250);
}

// Hero bulletin: rotate the headlines; pause on hover/focus; dots jump to one.
(() => {
  const items = $$(".bulletin-items li"), dots = $(".bulletin-dots");
  if (!items.length) return;
  let i = 0, timer = null;
  dots.innerHTML = [...items].map((_, n) => `<button type="button" aria-label="Headline ${n + 1}"></button>`).join("");
  const show = (n) => {
    if (n === i) return;
    items[i].classList.replace("active", "leaving");
    const old = items[i];
    setTimeout(() => old.classList.remove("leaving"), 450);
    i = n;
    items[i].classList.add("active");
    dots.querySelectorAll("button").forEach((d, k) => d.classList.toggle("on", k === i));
  };
  dots.querySelectorAll("button")[0].classList.add("on");
  const start = () => { if (!matchMedia("(prefers-reduced-motion: reduce)").matches) timer = setInterval(() => show((i + 1) % items.length), 4500); };
  const stop = () => clearInterval(timer);
  dots.addEventListener("click", (e) => { const k = [...dots.children].indexOf(e.target); if (k >= 0) { stop(); show(k); start(); } });
  const bar = $("#bulletin");
  bar.addEventListener("mouseenter", stop); bar.addEventListener("mouseleave", start);
  bar.addEventListener("focusin", stop); bar.addEventListener("focusout", start);
  start();
})();

// Progress bar: a paper plane flies along the track to the current step.
let currentStep = 1;
function placePlane(animate) {
  const wrap = $("#steps-wrap"), track = $(".steps-track"), li = $(`.steps li[data-step="${currentStep}"]`);
  if (!wrap || !li) return;
  const center = li.offsetLeft + li.offsetWidth / 2 - track.offsetLeft;
  wrap.classList.toggle("no-anim", !animate);
  $("#plane").style.setProperty("--x", `${center - 11}px`);
  $("#steps-fill").style.setProperty("--fill", `${Math.max(0, center)}px`);
  if (!animate) requestAnimationFrame(() => wrap.classList.remove("no-anim"));
}
function setStep(n) {
  const from = currentStep;
  if (n === from) return;
  currentStep = n;
  $$(".steps li").forEach((li) => {
    const s = +li.dataset.step, wasDone = li.classList.contains("done");
    li.classList.toggle("active", s === n);
    li.classList.toggle("done", s < n);
    li.classList.toggle("just-done", s < n && !wasDone);
    li.querySelector("span").textContent = s < n ? "✓" : s;
  });
  const plane = $("#plane");
  plane.classList.remove("flying", "backward");
  void plane.offsetWidth;  // restart the flight animation
  plane.classList.add(n > from ? "flying" : "backward");
  placePlane(true);
}
addEventListener("resize", () => placePlane(false));
(document.fonts?.ready || Promise.resolve()).then(() => placePlane(false));

function showError(msg) {
  const e = $("#form-error");
  e.textContent = msg; e.hidden = !msg;
}

const store = {
  // keys are screenproof_*; a value saved under the pre-rename hireready_* key is carried over once
  get: (k) => {
    try {
      const old = k.replace(/^screenproof_/, "hireready_"), v = localStorage.getItem(k);
      if (v === null && localStorage.getItem(old) !== null) { localStorage.setItem(k, localStorage.getItem(old)); localStorage.removeItem(old); }
      return localStorage.getItem(k) || "";
    } catch { return ""; }
  },
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
  if (f) setStep(1);
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

let formMode = "resume";
function setFormMode(mode) {
  formMode = mode;
  $$(".mode-tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  $("#resume-fields").hidden = mode !== "resume";
  $("#linkedin-fields").hidden = mode !== "linkedin";
  $("#compare-fields").hidden = mode !== "compare";
  $("#analyze-btn").textContent = { resume: "Score my resume", linkedin: "Review my profile", compare: "Compare resumes" }[mode];
  showError("");
}
$$(".mode-tab").forEach((t) => t.addEventListener("click", () => setFormMode(t.dataset.mode)));

async function submitLinkedIn() {
  const url = $("#profile_url").value.trim(), pdf = $("#profile_pdf").files[0];
  if (!url && !pdf) return showError("Add your LinkedIn profile link, or upload your LinkedIn PDF.");
  const fd = new FormData();
  fd.append("profile_url", url);
  fd.append("target_role", $("#target_role").value.trim());
  if (pdf) fd.append("profile_pdf", pdf);
  $("#analyze-btn").disabled = true;
  $("#results").hidden = $("#kit").hidden = true;
  $("#input-card").after($("#loading"));
  const stages = [...(pdf ? ["Uploading your LinkedIn PDF"] : []), "Reading your LinkedIn profile", "Checking what recruiters search for", "Scoring your profile", "Writing your rewrites"];
  loader.start(stages, 45, { upload: !!pdf });
  let ok = false;
  try {
    const data = await postForm("/api/linkedin", fd, (f) => loader.setUpload(f));
    await loader.finish();
    ok = true;
    store.set("screenproof_access_code", $("#access_code").value.trim());
    Object.assign(state, { demo: data.demo, preview: !!data.preview, locked: data.locked, resumeText: "", kit: null,
      job: { url: data.profile.url, description: "", title: "", company: "" }, analysis: data.analysis });
    renderAnalysis();
    loadHistory();
  } catch (err) {
    showError(err.message);
    if (err.code === "access_code") needAccessCode();
    if (err.code === "profile_fetch_failed") { $("#li-pdf-box").open = true; }
  } finally {
    if (!ok) loader.stop();
    $("#analyze-btn").disabled = false;
  }
}

// ---- Compare resumes (Pro) ----
$("#compare_files").addEventListener("change", () => {
  setStep(1);
  $("#compare-picked").innerHTML = [...$("#compare_files").files].map((f) => `<li>${esc(f.name)}</li>`).join("");
});
const STATUS_MARK = { met: ["✓", "st-met", "Met"], partial: ["½", "st-partial", "Partly met"], missing: ["✗", "st-missing", "Missing"] };
const CHANCES = { "top candidate": "Strong shortlist chances", competitive: "Good shortlist chances", possible: "Fair chances", "long shot": "Low chances" };

function renderComparison(c, job) {
  $("#compare-results").hidden = false;
  setStep(2);
  $("#cmp-job").textContent = [c.job_title || job.title, c.company || job.company].filter(Boolean).join(" · ");
  $("#cmp-verdict").textContent = c.verdict;
  $("#cmp-callout").classList.toggle("tie", c.tie);
  $("#cmp-callout-label").textContent = c.tie ? "Even match" : "Verdict";
  const callout = $("#cmp-callout");  // replay the entrance animation on every comparison
  callout.style.animation = "none"; void callout.offsetWidth; callout.style.animation = "";
  $("#cmp-cards").innerHTML = c.entries.map((e, i) => {
    const best = e.rank === 1 && !c.tie;
    return `<div class="cmp-card ${best ? "best" : ""}" style="--i:${i}">
      <div class="cmp-top">${best ? `<span class="cmp-rank"><span class="star">★</span> Best fit</span>` : ""}
        <span class="standing s-${esc(e.standing.split(" ")[0])}">${esc(CHANCES[e.standing] || e.standing)}</span></div>
      <div class="cmp-name">${esc(e.name)}</div>
      <div class="cmp-score"><span class="cmp-num" data-to="${e.overall_score}">0</span><small>/100</small></div>
      <div class="cmp-stats">
        <span>Must-haves: ${e.must_met} of ${e.must_total} met${e.must_partial ? `, ${e.must_partial} partly` : ""}</span>
        <span>Keywords word-for-word: ${e.keyword_pct}%</span>
        ${e.cap_reason ? `<span>${esc(e.cap_reason)}</span>` : ""}
      </div>
      ${e.unique_strengths.length ? `<div class="cmp-sub">Only this resume shows</div><ul class="cmp-list">${e.unique_strengths.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${e.missing_keywords.length ? `<div class="cmp-sub">Missing keywords</div><div class="cmp-stats">${esc(e.missing_keywords.join(", "))}</div>` : ""}
    </div>`;
  }).join("");
  $("#cmp-table").innerHTML = `<thead><tr><th>Requirement</th>${c.entries.map((e) => `<th title="${esc(e.name)}">${esc(e.name)}</th>`).join("")}</tr></thead>
    <tbody>${c.rows.map((r, ri) => `<tr style="--r:${ri}"><td>${esc(r.text)}${r.importance === "must" ? '<span class="cmp-must">must</span>' : ""}</td>${
      r.statuses.map((st) => `<td><span class="mark ${STATUS_MARK[st][1]}" title="${STATUS_MARK[st][2]}" aria-label="${STATUS_MARK[st][2]}">${STATUS_MARK[st][0]}</span></td>`).join("")}</tr>`).join("")}</tbody>`;
  countUp($$("#cmp-cards .cmp-num"));
  $("#compare-results").scrollIntoView({ behavior: "smooth" });
}

function countUp(els) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  els.forEach((el, i) => {
    const to = +el.dataset.to;
    if (reduce) { el.textContent = to; return; }
    const start = performance.now() + 150 + i * 90, dur = 700;
    const step = (now) => {
      const t = Math.min(1, Math.max(0, (now - start) / dur));
      el.textContent = Math.round(to * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

async function submitCompare() {
  const files = [...$("#compare_files").files], url = $("#compare_url").value.trim(), text = $("#compare_text").value.trim();
  if (files.length < 2 || files.length > 4) return showError("Pick 2 to 4 resumes to compare.");
  if (!url && !text) return showError("Add the job link or paste the job description.");
  const fd = new FormData();
  files.forEach((f) => fd.append("resumes", f));
  fd.append("job_url", url);
  fd.append("job_text", text);
  $("#analyze-btn").disabled = true;
  $("#results").hidden = $("#kit").hidden = $("#compare-results").hidden = true;
  $("#input-card").after($("#loading"));
  loader.start(["Uploading the resumes", ...(url && !text ? ["Fetching the job posting"] : []), "Reading the job's requirements",
    "Checking each resume against them", "Ranking the resumes"], 40 + 15 * files.length, { upload: true });
  let ok = false;
  try {
    const data = await postForm("/api/compare", fd, (f) => loader.setUpload(f));
    await loader.finish();
    ok = true;
    store.set("screenproof_access_code", $("#access_code").value.trim());
    renderComparison(data.comparison, data.job);
  } catch (err) {
    showError(err.message);
    if (err.code === "access_code") needAccessCode();
    if (err.code === "job_fetch_failed") { $("#compare-paste").open = true; $("#compare_text").focus(); }
  } finally {
    if (!ok) loader.stop();
    $("#analyze-btn").disabled = false;
  }
}

$("#analyze-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  if (formMode === "linkedin") return submitLinkedIn();
  if (formMode === "compare") return submitCompare();
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
    store.set("screenproof_access_code", $("#access_code").value.trim());
    state.demo = data.demo;
    state.preview = !!data.preview;
    state.locked = data.locked;
    state.resumeText = data.resume_text; state.job = data.job; state.analysis = data.analysis; state.kit = null;
    renderAnalysis();
    loadHistory();
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
  $("#compare-results").hidden = true;

  const score = Math.max(0, Math.min(100, a.overall_score));
  const color = score >= 80 ? "var(--good)" : score >= 65 ? "var(--good)" : score >= 45 ? "var(--warn)" : "var(--bad)";
  const fg = $("#ring-fg");
  fg.style.stroke = color;
  requestAnimationFrame(() => (fg.style.strokeDashoffset = 326.7 * (1 - score / 100)));
  let n = 0;
  const tick = setInterval(() => { n = Math.min(score, n + 2); $("#score-num").textContent = n; if (n >= score) clearInterval(tick); }, 16);

  $("#demo-banner").hidden = !state.demo;
  const resumeOnly = a.mode === "resume", profileMode = a.mode === "linkedin";
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
  if (profileMode) {
    $("#job-line").textContent = `LinkedIn profile · evaluated for ${title || "your field"}`;
    $("#breakdown-note").textContent = "Calculated in code from your profile — the same profile and title always get the same score.";
    $("#kw-have-title").textContent = `Recruiters search for ${title || "this role"} — on your profile`;
    $("#kw-miss-title").textContent = "Missing from your profile";
    $("#cta-title").textContent = "Score your resume too";
    $("#cta-text").textContent = "Upload your resume to see how it scores, and add a job link to check your match for a specific role.";
    $("#kit-btn").textContent = "Score my resume";
  }
  $("#chat-card").hidden = profileMode;
  $("#jobs-card").hidden = !resumeOnly || state.preview || !title;
  $("#jobs-role").textContent = title || "";
  $("#jobs-list").innerHTML = "";
  state.jobs = [];
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
  const warn = $("#file-warn"), warnings = a.warnings || [];
  warn.hidden = !warnings.length;
  warn.textContent = warnings.join(" ");
  const gates = a.gates || [];
  $("#gates").hidden = !gates.length;
  $("#gates-list").innerHTML = gates.map((g) => `<li>${esc(g)}</li>`).join("");
  renderScreens(a);
  const link = $("#job-link");
  link.hidden = !job.url; link.href = job.url || "#";
  link.textContent = a.mode === "linkedin" ? "View profile ↗" : "View job posting ↗";

  $("#categories").innerHTML = a.categories.map((c) => `
    <div class="cat ${c.weight ? "" : "not-counted"}">
      <div class="cat-head"><span>${esc(c.name)} <small>· ${c.weight ? c.weight + "%" : "not counted"}</small></span><span>${c.weight ? c.score : "n/a"}</span></div>
      <div class="bar"><div data-w="${c.weight ? c.score : 0}"></div></div>
      ${c.detail ? `<p class="cat-detail">${esc(c.detail)}</p>` : ""}
      ${state.preview ? `<p>${lockLine("Why this score — in the full report")}</p>` : `<p>${esc(c.rationale)}</p>`}
    </div>`).join("");
  requestAnimationFrame(() => $$(".bar > div").forEach((b) => (b.style.width = b.dataset.w + "%")));

  renderRequirements(a);
  resetChat();

  $("#kw-matched").innerHTML = a.matched_keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("") || "<small>None found</small>";
  $("#kw-missing").innerHTML = a.missing_keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("") || "<small>Nothing major missing</small>";
  listInto("#strengths", a.strengths, "—");
  listInto("#gaps", a.gaps, "No major gaps");
  listInto("#red-flags", a.red_flags, "None spotted");
  const L = state.locked;
  $("#preview-banner").hidden = !state.preview;
  if (state.preview && L) {
    const more = (sel, n, word) => { if (n) $(sel).insertAdjacentHTML("beforeend", `<li class="locked-li">${lockLine(`${plural(n, word)} more in the full report`)}</li>`); };
    more("#strengths", L.strengths, "strength");
    more("#gaps", L.gaps, "gap");
    if (L.red_flags) $("#red-flags").innerHTML = `<li class="locked-li">${lockLine(`${plural(L.red_flags, "red flag")} found — see them in the full report`)}</li>`;
    if (L.missing_keywords) $("#kw-missing").innerHTML = `<span class="chip lock-chip">${LOCK_SVG}${plural(L.missing_keywords, "missing keyword")} — full report</span>`;
  }

  const lockedEdits = state.preview && L?.changes
    ? lockBox(`${plural(L.changes, "more edit")} ranked by impact`, "Ready-to-paste rewrites for your resume, highest impact first.", true) : "";
  $("#changes").innerHTML = a.changes.map((c, i) => `
    <div class="change">
      <div class="change-head"><span class="prio ${c.priority}">${c.priority}</span><span class="change-section">${esc(c.section)}</span>${c.gain ? `<span class="gain">+${c.gain} pts</span>` : ""}</div>
      <div class="change-issue">${esc(c.issue)}</div>
      <div class="diff">
        ${c.current ? `<div class="old">${esc(c.current)}</div>` : ""}
        <div class="new">${inline(c.suggested)}<button class="btn ghost small copy" data-change="${i}">Copy</button></div>
      </div>
      <p class="why">${esc(c.why)}</p>
    </div>`).join("") + lockedEdits;

  const proj = $("#projected"), gainTotal = a.projected_score != null && a.projected_score > a.overall_score;
  proj.hidden = !gainTotal || a.mode === "linkedin";
  if (gainTotal) proj.innerHTML = `<span>Apply ${state.preview ? "every edit in the full report" : "these edits"}:</span>
    <strong>${a.overall_score}</strong><span class="arrow">→</span><strong>${a.projected_score}</strong>
    <span class="muted">re-scored in code, same engine as your score</span>`;

  // chat + kit are full-version features
  $("#chat-grid").hidden = state.preview;
  $("#chat-lock").hidden = !state.preview;
  if (state.preview) $("#chat-lock").innerHTML = lockBox("AI resume editing is in the full version", "Chat to rewrite your summary, bullets or whole resume, then download it as Word.", true);
  if (state.preview) {
    $("#cta-title").textContent = "Want the full report?";
    $("#cta-text").textContent = `Get every edit, the requirement-by-requirement check, AI resume editing and a tailored application kit. Email ${state.contact} for the paid version and pricing.`;
    $("#kit-btn").textContent = "Contact admin for pricing";
  }

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
  const lockedReqs = state.preview ? state.locked?.requirements || 0 : 0;
  card.hidden = !cov || (!reqs.length && !lockedReqs);
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
  $("#requirements").innerHTML = lockedReqs
    ? lockBox(`${plural(lockedReqs, "requirement")} checked one by one`, "See exactly which ones you meet, with the matching lines quoted from your resume.", true)
    : group("must", "Must-have") + group("nice", "Nice-to-have");
}

// ---- How each screen sees you: strict ATS, recruiter/AI ranker, 7-second skim ----
function renderScreens(a) {
  const L = a.lenses, K = a.skim;
  $("#screens").hidden = !L;
  if (!L) return;
  $("#lens-strict").innerHTML = `${L.strict_found}<small> of ${L.strict_total} terms</small>`;
  $("#lens-strict-note").textContent = "Requirements whose exact posting wording appears in your resume. Literal search misses abbreviations and variants.";
  $("#lens-semantic").innerHTML = `${L.semantic_pct}<small>%</small>`;
  $("#lens-semantic-note").textContent = `Of ${L.semantic_total} requirements met or partly met, counting variants and evidence. ${L.title_note ? L.title_note + "." : ""}`;
  const shown = (K.terms_shown || []).length, total = shown + (K.terms_missing || []).length;
  $("#lens-skim").innerHTML = `${(K.title_shown ? 1 : 0) + shown}<small> of ${1 + total} signals</small>`;
  $("#lens-skim-note").innerHTML = esc(K.verdict) + ((K.terms_missing || []).length
    ? `<span class="chips">${K.terms_missing.map((t) => `<span class="chip miss">${esc(t)}</span>`).join("")}</span>` : "");
}

// ---- Resume chat ----
function chatSuggestions() {
  const a = state.analysis, hasJob = a?.mode !== "resume";
  return [
    "Apply the high-priority changes",
    "Make my summary punchier",
    hasJob ? `Tailor it to the ${a?.job_title || "job"} role` : `Tailor it for ${a?.job_title || "my target"} roles`,
    "Start every bullet with a strong action verb",
    "Tighten it to fit one page",
  ];
}

function bubble(role, html, extraClass = "") {
  const el = document.createElement("div");
  el.className = `msg-bubble ${role} ${extraClass}`;
  el.innerHTML = html;
  $("#chat-log").append(el);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return el;
}

function renderPreview(flash = false) {
  const doc = $("#chat-doc"), md = state.chat.md;
  doc.classList.toggle("plain", !md);
  doc.innerHTML = md ? markdown(md) : esc(state.resumeText);
  $("#preview-label").textContent = md ? "Edited resume" : "Your resume (original)";
  $("#preview-label").classList.toggle("edited", !!md);
  if (flash) { doc.classList.remove("flash"); void doc.offsetWidth; doc.classList.add("flash"); }
}

function resetChat() {
  state.chat = { messages: [], md: "" };
  $("#chat-log").innerHTML = "";
  bubble("assistant", "Hi! Tell me what to change — a section, a tone, a fact to add — and I'll update your resume on the right. I'll never invent numbers or experience.");
  $("#chat-suggest").innerHTML = chatSuggestions().map((t) => `<button type="button">${esc(t)}</button>`).join("");
  renderPreview();
}

$("#chat-suggest").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) { $("#chat-input").value = b.textContent; $("#chat-form").requestSubmit(); }
});
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#chat-input").value.trim();
  if (!text || $("#chat-send").disabled) return;
  $("#chat-input").value = "";
  $("#chat-send").disabled = true;
  bubble("user", esc(text));
  state.chat.messages.push({ role: "user", content: text });
  const typing = bubble("assistant", "<span></span><span></span><span></span>", "typing");
  try {
    const { edit } = await api("/api/resume-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resume_text: state.resumeText,
        resume_markdown: state.chat.md,
        messages: state.chat.messages,
        job: state.job?.description ? state.job : null,
        analysis: state.analysis,
      }),
    });
    typing.remove();
    const items = edit.changes.map((c) => `<li class="${c.startsWith("Please verify") ? "warn" : ""}">${inline(c)}</li>`).join("");
    bubble("assistant", inline(edit.reply) + (items ? `<ul>${items}</ul>` : ""));
    state.chat.messages.push({ role: "assistant", content: edit.reply });
    if (edit.changed) { state.chat.md = edit.resume_markdown; renderPreview(true); }
  } catch (err) {
    typing.remove();
    state.chat.messages.pop();
    bubble("assistant", esc(err.message), "error");
    if (err.code === "access_code") needAccessCode();
  } finally {
    $("#chat-send").disabled = false;
    $("#chat-input").focus();
  }
});

$("#chat-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(state.chat.md || state.resumeText); toast("Copied"); } catch { toast("Couldn't access the clipboard"); }
});
$("#chat-docx").addEventListener("click", () => downloadResume(state.chat.md || state.resumeText, "edited", "docx"));
$("#chat-pdf").addEventListener("click", () => downloadResume(state.chat.md || state.resumeText, "edited", "pdf"));

// ---- Jobs that fit (resume-only score) ----
$("#jobs-btn").addEventListener("click", async () => {
  const location = $("#jobs_location").value.trim(), country = $("#jobs_country").value;
  store.set("screenproof_jobs_location", location);
  store.set("screenproof_jobs_country", country);
  const btn = $("#jobs-btn"), list = $("#jobs-list");
  btn.disabled = true;
  btn.textContent = "Searching…";
  try {
    const data = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: state.analysis.job_title, location, country, skills: state.analysis.matched_keywords || [] }),
    });
    state.jobs = data.jobs;
    list.innerHTML = data.jobs.length ? data.jobs.map((j, i) => `
      <li>
        <div>
          <div class="job-title">${esc(j.title)}</div>
          <small>${esc([j.company, j.location, j.remote ? "Remote" : "", j.date_posted].filter(Boolean).join(" · "))}</small>
          ${(j.fit || []).length ? `<span class="job-fit">Asks for ${j.fit.length} of your skills: ${esc(j.fit.slice(0, 4).join(", "))}${j.fit.length > 4 ? "…" : ""}</span>` : ""}
        </div>
        <div class="job-actions">
          <a class="btn ghost small" href="${esc(j.url)}" target="_blank" rel="noopener">View ↗</a>
          <button class="btn small" data-job="${i}">Check my match</button>
        </div>
      </li>`).join("") : `<li class="empty">No recent postings found. Try another city, or leave the city blank.</li>`;
  } catch (err) {
    toast(err.message);
    if (err.code === "access_code") needAccessCode();
  } finally {
    btn.disabled = false;
    btn.textContent = "Find jobs";
  }
});
$("#jobs-list").addEventListener("click", (e) => {
  const b = e.target.closest("[data-job]");
  if (!b) return;
  const j = state.jobs[+b.dataset.job];
  // Reuse the normal job-match flow; Indeed results carry the description, so nothing is re-fetched.
  $("#job_url").value = j.url;
  $("#job_text").value = j.description || "";
  $("#analyze-form").requestSubmit();
});

// ---- Kit ----
$("#kit-btn").addEventListener("click", async () => {
  if (state.preview) { location.href = mailto(); return; }
  if (state.analysis?.mode === "linkedin") {
    setFormMode("resume");
    $("#input-card").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
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

const kitSuffix = () => state.analysis.company || state.job.company || "tailored";
$("#dl-docx").addEventListener("click", () => downloadResume(state.kit.tailored_resume_markdown, kitSuffix(), "docx"));
$("#dl-pdf").addEventListener("click", () => downloadResume(state.kit.tailored_resume_markdown, kitSuffix(), "pdf"));

async function downloadResume(markdownText, suffix, format) {
  const name = (state.analysis.candidate_name || "resume") + " - " + suffix;
  const res = await fetch("/api/export/" + format, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...accessHeaders() },
    body: JSON.stringify({ markdown: markdownText, filename: name }),
  });
  if (!res.ok) return toast("Export failed");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = name.replace(/[^\w-]+/g, "_") + "." + format;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---- Status ----
// ---- Splash: play the intro once per visit (short on reloads), then fade out ----
(() => {
  const splash = $("#splash");
  let seen = false;
  try { seen = sessionStorage.getItem("sp_splash") === "1"; sessionStorage.setItem("sp_splash", "1"); } catch {}
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const minMs = reduce ? 200 : seen ? 700 : 1700;
  const t0 = performance.now();
  const hide = () => setTimeout(() => {
    splash.classList.add("out");
    setTimeout(() => splash.remove(), 500);
    setTimeout(typeHeadline, 350);
  }, Math.max(0, minMs - (performance.now() - t0)));
  document.readyState === "complete" ? hide() : addEventListener("load", hide, { once: true });
})();

// ---- Show/hide password ----
$$(".eye").forEach((btn) => btn.addEventListener("click", () => {
  const input = btn.previousElementSibling, show = input.type === "password";
  input.type = show ? "text" : "password";
  btn.setAttribute("aria-pressed", show);
  btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
  input.focus();
}));

// ---- Saved runs (admin only): preview an older resume + job without re-running anything ----
const when = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
};
const runTarget = (h) => h.mode === "resume" ? `General resume score${h.job_title ? " · best fit: " + h.job_title : ""}`
  : h.mode === "linkedin" ? `LinkedIn review · ${h.job_title || "profile"}`
  : [h.job_title, h.company].filter(Boolean).join(" · ") || h.job_url || "Job match";
const OUTCOMES = [["interview", "Interview"], ["offer", "Offer"], ["rejected", "Rejected"], ["no_reply", "No reply"]];
const runRow = (h) => `
  <span class="h-score">${esc(String(h.overall_score ?? "–"))}</span>
  <div class="h-main">
    <div class="h-name">${esc(h.resume_name || "Resume")}</div>
    <div class="h-target">${esc(runTarget(h))}</div>
    <div class="h-date">Last used ${esc(when(h.last_used))}</div>
    ${(h.trend || []).length > 1 ? `<div class="h-trend">This job: ${h.trend.map((v, i, arr) => i === arr.length - 1 ? `<b>${v}</b>` : v).join(" → ")}</div>` : ""}
    <div class="h-outcome" data-key="${esc(h.key)}"><span>After applying:</span>${OUTCOMES.map(([k, label]) =>
      `<button type="button" data-outcome="${k}" class="${h.outcome === k ? "on" : ""}">${label}</button>`).join("")}</div>
  </div>
  <button type="button" class="btn ghost small" data-preview="${esc(h.key)}">Preview</button>`;

async function loadHistory() {
  if (!state.admin) { $("#history").hidden = true; return; }
  let items = [];
  try { items = (await api("/api/history")).items || []; } catch { return; }
  $("#history").hidden = false;
  const [last, ...rest] = items;
  $("#history-last").innerHTML = last
    ? `<div class="history-last">${runRow(last).replace(`<div class="h-name">`, `<div class="h-tag">Last used resume</div><div class="h-name">`)}</div>` : "";
  $("#history-list").innerHTML = rest.length ? rest.map((h) => `<li class="history-item">${runRow(h)}</li>`).join("")
    : last ? "" : `<li class="history-empty">Runs you make while signed in as admin will appear here.</li>`;
}

$("#history").addEventListener("click", async (e) => {
  const out = e.target.closest("[data-outcome]");
  if (out) {
    const row = out.closest(".h-outcome");
    try {
      await api(`/api/history/${row.dataset.key}/outcome`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outcome: out.dataset.outcome }) });
      row.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === out));
      toast("Saved. Thanks, this helps calibrate the scores");
    } catch (err) { toast(err.message); }
    return;
  }
  const key = e.target.closest("[data-preview]")?.dataset.preview;
  if (!key) return;
  try {
    const run = await api(`/api/history/${key}`);
    Object.assign(state, { preview: false, locked: null, kit: null, resumeText: run.resume_text || "",
      job: run.job || { url: "", description: "" }, analysis: run.analysis });
    state.chat = { messages: [], md: "" };
    renderAnalysis();
    $("#results").scrollIntoView({ behavior: "smooth" });
    toast(`Showing ${run.resume_name || "saved run"}`);
  } catch (err) { toast(err.message); }
});

// ---- Owner sign-in (full version, no daily limits; the session is an HttpOnly cookie) ----
function setAdmin(on) {
  state.admin = on;
  $("#admin-pill").hidden = !on;
  $("#admin-out").hidden = !on;
  $("#admin-btn").hidden = on || !state.adminLogin;
  if (on) $("#access-wrap").hidden = true;
  else if (state.accessRequired) $("#access-wrap").hidden = false;
  loadHistory();
}
$("#admin-btn").addEventListener("click", () => { $("#admin-err").hidden = true; $("#admin-dialog").showModal(); $("#admin_user").focus(); });
$("#admin-cancel").addEventListener("click", () => $("#admin-dialog").close());
$("#admin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await fetch("/api/admin/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: $("#admin_user").value, password: $("#admin_pass").value }),
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    $("#admin-err").textContent = typeof d.detail === "string" ? d.detail : "Sign-in failed.";
    $("#admin-err").hidden = false;
    return;
  }
  $("#admin_pass").value = "";
  $("#admin_pass").type = "password";
  $("#admin-dialog .eye").setAttribute("aria-pressed", "false");
  $("#admin-dialog").close();
  setAdmin(true);
  toast("Signed in — full version unlocked");
});
$("#admin-out").addEventListener("click", async () => {
  await fetch("/api/admin/logout", { method: "POST" });
  setAdmin(false);
  toast("Signed out");
});

$("#access_code").value = store.get("screenproof_access_code");
$("#jobs_location").value = store.get("screenproof_jobs_location");
api("/api/status").then((s) => {
  $("#demo-pill").hidden = !s.demo;
  state.accessRequired = s.access_code_required;
  state.adminLogin = s.admin_login;
  if (s.access_code_required) $("#access-wrap").hidden = false;
  fetch("/api/admin").then((r) => r.json()).then((a) => setAdmin(a.admin)).catch(() => setAdmin(false));
  if (s.contact_email) state.contact = s.contact_email;
  const saved = store.get("screenproof_jobs_country");
  $("#jobs_country").innerHTML = (s.job_countries || []).map((c) => `<option ${c === saved ? "selected" : ""}>${esc(c)}</option>`).join("");
  $$(".contact-link").forEach((l) => { l.textContent = state.contact; l.href = mailto(); });
  $$(".contact-btn").forEach((l) => (l.href = mailto()));
}).catch(() => {});
