document.addEventListener("DOMContentLoaded", () => {
  // Auto-inject CSRF token into every form
  const token = document.querySelector('meta[name="csrf-token"]')?.content;
  if (token) {
    document.querySelectorAll("form").forEach(f => {
      if (!f.querySelector('input[name="_csrf_token"]')) {
        const i = document.createElement("input");
        i.type = "hidden"; i.name = "_csrf_token"; i.value = token;
        f.appendChild(i);
      }
    });
  }

  // Highlight the active nav link (and open its parent dropdown) based on current path,
  // and pick the best-matching link to drive the page heading below.
  const path = window.location.pathname;
  let bestMatch = null, bestLen = -1;
  document.querySelectorAll(".sidebar-nav-pro .nav-link, .navbar-nav-links .nav-link").forEach(a => {
    const href = a.getAttribute("href");
    if (!href || href === "#") return;
    try {
      const linkPath = new URL(a.href).pathname;
      const isExact = linkPath === path;
      const isPrefix = linkPath !== "/" && (path === linkPath || path.startsWith(linkPath + "/"));
      if (isExact) {
        a.classList.add("active");
        const toggle = a.closest(".dropdown")?.querySelector(":scope > .dropdown-toggle");
        toggle?.classList.add("active");
      }
      if (isPrefix && linkPath.length > bestLen) {
        bestLen = linkPath.length;
        bestMatch = a;
      }
    } catch (e) {}
  });

  // Dashboard heading — swap "Dashboard" for whichever section is actually open
  if (bestMatch) {
    const label = bestMatch.querySelector("span")?.textContent?.trim();
    if (label) {
      document.title = label + " · RecruiterATS";
    }
  }

  // Unified professional sidebar toggle
  const sidebarToggle = document.getElementById("sidebarToggleBtn");
  sidebarToggle?.addEventListener("click", () => {
    if (window.innerWidth <= 991) {
      document.body.classList.toggle("sidebar-open");
      sidebarToggle.setAttribute("aria-expanded", document.body.classList.contains("sidebar-open") ? "true" : "false");
    } else {
      document.body.classList.toggle("sidebar-collapsed");
      sidebarToggle.setAttribute("aria-expanded", document.body.classList.contains("sidebar-collapsed") ? "false" : "true");
    }
  });

  // Close the mobile sidebar after navigation.
  document.querySelectorAll(".sidebar-nav-pro .nav-link").forEach(a => {
    a.addEventListener("click", () => document.body.classList.remove("sidebar-open"));
  });

  // Top navbar mobile menu toggle
  const navToggler = document.getElementById("navTogglerBtn");
  const navMenu = document.getElementById("navMenu");
  navToggler?.addEventListener("click", () => {
    const open = navMenu.classList.toggle("show");
    navToggler.setAttribute("aria-expanded", open ? "true" : "false");
  });
  // Close the mobile menu after a link is clicked
  navMenu?.querySelectorAll(".nav-link:not(.dropdown-toggle)").forEach(a => {
    a.addEventListener("click", () => {
      navMenu.classList.remove("show");
      navToggler?.setAttribute("aria-expanded", "false");
    });
  });

  // Cosmetic only: give the topbar a subtle shadow once the page scrolls
  const topbar = document.querySelector(".topbar");
  const contentArea = document.querySelector(".content-area");
  const scrollSource = contentArea || window;
  const onScroll = () => {
    const y = contentArea ? contentArea.scrollTop : window.scrollY;
    topbar?.classList.toggle("scrolled", y > 4);
  };
  scrollSource.addEventListener("scroll", onScroll, { passive: true });
});

// AJAX form submission for AI actions (resume parse / screening).
// Updates the visible page immediately from the JSON response, so no manual refresh is required.
document.addEventListener("submit", async (e) => {
  const f = e.target;
  if (!f.classList.contains("ajax-form")) return;
  e.preventDefault();

  const btn = f.querySelector("button");
  const original = btn ? btn.innerHTML : "";
  const target = f.dataset.target ? document.getElementById(f.dataset.target) : null;
  const row = f.closest("tr");

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Working…';
  }

  try {
    const r = await fetch(f.action, {
      method: f.method || "POST",
      body: (() => { const fd = new FormData(f); return fd; })(),
      headers: {
        "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
        "X-Requested-With": "XMLHttpRequest",
        "Cache-Control": "no-cache"
      },
      cache: "no-store"
    });

    const j = await r.json();

    if (!j.ok) {
      const html = "<div class='alert alert-danger mt-3 mb-0'>" +
        escapeAiHtml(j.error || "AI error") + "</div>";
      if (target) target.innerHTML = html;
      else if (row) showInlineAiMessage(row, html);
      else alert(j.error || "AI error");
      return;
    }

    // Resume parser: immediately update candidate fields and AI summary.
    if (f.action.includes("/ai/resume/")) {
      const d = j.data || {};
      const setText = (id, value, fallback = "—") => {
        const el = document.getElementById(id);
        if (el) el.textContent = value ?? fallback;
      };

      setText("candidateNameValue", d.name || "");
      setText("candidateEmailValue", d.email || "");
      setText("candidatePhoneValue", d.phone || "");
      setText("candidateLocationValue", d.location || "");
      setText("candidateSkillsValue", Array.isArray(d.skills) ? d.skills.join(", ") : d.skills, "—");
      setText("candidateExperienceValue", (d.experience_years ?? 0) + " years");
      setText("candidateEducationValue", Array.isArray(d.education) ? d.education.join(", ") : d.education, "—");
      setText("candidateAiSummary", d.summary || "AI analysis completed.");

      if (target) {
        target.innerHTML =
          "<div class='alert alert-success mt-3 mb-0'>" +
          "<i class='bi bi-check-circle me-1'></i>AI resume data updated successfully." +
          "</div>";
      }
      showResumeAiResult(d);
    }

    // AI screening: immediately update score + 3 matching keywords in the table.
    if (f.action.includes("/ai/screen/") && row) {
      const d = j.data || {};
      const score = Number.isFinite(Number(d.score)) ? Number(d.score) : "—";
      const cells = row.querySelectorAll("td");
      if (cells.length >= 5) {
        const scoreCell = cells[3];
        if (scoreCell) scoreCell.innerHTML = "<b>" + escapeAiHtml(score) + "</b>";

        const screenCell = cells[4];
        if (screenCell) {
          const keywords = Array.isArray(d.matching_keywords) ? d.matching_keywords : [];
          screenCell.innerHTML =
            '<div class="small mb-1 ai-screening-result">' +
            keywords.slice(0, 3).map(k =>
              '<span class="d-block"><i class="bi bi-check2-circle text-success me-1"></i>' +
              escapeAiHtml(k) + "</span>"
            ).join("") +
            "</div>" +
            '<span class="badge text-bg-success-subtle border border-success-subtle">Updated</span>';
        }
      }
      const statusSel = cells[2] ? cells[2].querySelector("select") : null;
      if (statusSel && d.status) {
        if (![...statusSel.options].some(o => o.value === d.status || o.text === d.status)) statusSel.add(new Option(d.status, d.status));
        statusSel.value = d.status;
      }
      showInlineAiMessage(row,
        "<div class='alert alert-success py-2 px-3 mb-0 small'>" +
        "<i class='bi bi-check-circle me-1'></i>AI screening updated: " + escapeAiHtml(d.decision || "") +
        describeInterview(d.interview) + "</div>"
      );
    }

  } catch (x) {
    const html = "<div class='alert alert-danger mt-3 mb-0'>Request failed: " +
      escapeAiHtml(x?.message || x) + "</div>";
    if (target) target.innerHTML = html;
    else if (row) showInlineAiMessage(row, html);
    else alert("Request failed: " + x);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }
});

function showResumeAiResult(d) {
  let modal=document.getElementById("resumeAiResultModal");
  if(!modal){
    modal=document.createElement("div"); modal.id="resumeAiResultModal"; modal.className="modal fade"; modal.tabIndex=-1;
    modal.innerHTML='<div class="modal-dialog modal-dialog-centered modal-lg"><div class="modal-content"><div class="modal-header"><h5 class="modal-title"><i class="bi bi-stars me-2 text-primary"></i>AI Resume Analysis Result</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body" id="resumeAiResultBody"></div><div class="modal-footer"><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button></div></div></div>';
    document.body.appendChild(modal);
  }
  const body=document.getElementById("resumeAiResultBody"), decisions=Array.isArray(d.decisions)?d.decisions:[];
  const rule='<div class="small text-muted mb-3">AI rule: ≤30% Reject · 31–50% Hold · 51–70% Accept · 71–79% Shortlisted · '+escapeAiHtml(d.threshold||80)+'%+ Auto Interview (existing Threshold).</div>';
  const rows=decisions.length?decisions.map(x=>{ const cls=x.score==null?'text-bg-danger':(x.score>=Number(d.threshold||80)?'text-bg-success':x.score>=71?'text-bg-primary':x.score>=51?'text-bg-info':x.score>=31?'text-bg-warning':'text-bg-danger'); return '<div class="border rounded p-3 mb-2"><div class="d-flex justify-content-between align-items-center"><b>'+escapeAiHtml(x.job||'Job')+'</b><span class="badge '+cls+'">'+escapeAiHtml(x.score==null?'Error':x.score+'%')+'</span></div><div class="mt-2"><b>Decision:</b> '+escapeAiHtml(x.decision||'—')+'</div>'+(x.why_match?.length?'<div class="small mt-2"><b>Why:</b> '+escapeAiHtml(x.why_match.join(' · '))+'</div>':'')+(x.missing_requirements?.length?'<div class="small mt-1"><b>Missing:</b> '+escapeAiHtml(x.missing_requirements.join(', '))+'</div>':'')+(x.interview?'<div class="small mt-2 text-success">'+describeInterview(x.interview).replace(/^ · /,'')+'</div>':'')+'</div>';}).join(''):'<div class="alert alert-light border mb-0">Candidate is saved, but no job application/JD is linked yet, so there is no JD match to score.</div>';
  body.innerHTML=rule+rows;
  bootstrap.Modal.getOrCreateInstance(modal).show();
}

function describeInterview(iv) {
  if (!iv) return "";
  if (iv.status === "scheduled") {
    const mail = iv.email === "queued" ? "email queued to candidate"
      : iv.email === "queued_smtp_missing" ? "email queued (SMTP not configured yet)"
      : iv.email === "no_email" ? "no candidate email on file, so no email sent" : "";
    return " · Interview booked " + escapeAiHtml((iv.scheduled_at || "").replace("T", " ")) + " with " + escapeAiHtml(iv.interviewer || "") + (mail ? " · " + mail : "");
  }
  if (iv.status === "already_scheduled") return " · interview already scheduled (" + escapeAiHtml((iv.scheduled_at || "").replace("T", " ")) + ")";
  return iv.message ? " · " + escapeAiHtml(iv.message) : "";
}

function escapeAiHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function showInlineAiMessage(row, html) {
  const old = row.parentElement?.querySelector(".ai-inline-message");
  old?.remove();

  const messageRow = document.createElement("tr");
  messageRow.className = "ai-inline-message";
  const cell = document.createElement("td");
  cell.colSpan = row.children.length || 1;
  cell.innerHTML = html;
  messageRow.appendChild(cell);
  row.insertAdjacentElement("afterend", messageRow);

  window.setTimeout(() => messageRow.remove(), 5000);
}


/* ATS candidate-image size guard: keeps candidate profile images under 20 KB. */
async function compressCandidateImageUnder20KB(file, maxBytes = 20 * 1024) {
  if (!file || !file.type?.startsWith("image/")) return file;
  if (file.size <= maxBytes) return file;

  const bitmap = await createImageBitmap(file);
  const maxDimension = 900;
  const scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
  let width = Math.max(1, Math.round(bitmap.width * scale));
  let height = Math.max(1, Math.round(bitmap.height * scale));

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { alpha: false });

  async function encode(q, w, h) {
    canvas.width = w; canvas.height = h;
    ctx.drawImage(bitmap, 0, 0, w, h);
    return new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", q));
  }

  // Binary-search JPEG quality, then reduce dimensions if necessary.
  let low = 0.05, high = 0.9, best = null;
  for (let i = 0; i < 8; i++) {
    const q = (low + high) / 2;
    const blob = await encode(q, width, height);
    if (blob.size <= maxBytes) { best = blob; low = q; }
    else high = q;
  }

  while (!best && width > 120) {
    width = Math.max(120, Math.round(width * 0.8));
    height = Math.max(120, Math.round(height * 0.8));
    for (let q = 0.75; q >= 0.08; q -= 0.07) {
      const blob = await encode(q, width, height);
      if (blob.size <= maxBytes) { best = blob; break; }
    }
  }

  bitmap.close?.();
  if (!best) throw new Error("Candidate image could not be compressed below 20 KB.");

  return new File([best], "candidate-photo.jpg", {
    type: "image/jpeg",
    lastModified: Date.now()
  });
}

window.compressCandidateImageUnder20KB = compressCandidateImageUnder20KB;


document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("candidateImageModal");
  if (modal) {
    modal.addEventListener("show.bs.modal", (event) => {
      const trigger = event.relatedTarget;
      if (!trigger) return;
      const img = document.getElementById("candidateImageModalImg");
      const title = document.getElementById("candidateImageModalTitle");
      img.src = trigger.dataset.imageUrl || "";
      title.textContent = trigger.dataset.candidateName ? trigger.dataset.candidateName + " — Candidate Photo" : "Candidate Photo";
    });
  }

  document.querySelectorAll('input[type="file"][name="photo"]').forEach(input => {
    input.addEventListener("change", async () => {
      if (!input.files?.[0]) return;
      try {
        const compressed = await window.compressCandidateImageUnder20KB(input.files[0]);
        const dt = new DataTransfer(); dt.items.add(compressed); input.files = dt.files;
        const note = document.createElement("small");
        note.className = "text-success d-block mt-1";
        note.textContent = `Photo optimized to ${(compressed.size/1024).toFixed(1)} KB`;
        input.parentElement.appendChild(note);
      } catch (e) { console.warn("Photo compression failed; server-side validation will handle it.", e); }
    });
  });
});


// Batch resume drag/drop processing: maximum 10 files. Reusable: every .batch-widget on the page is initialised.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.batch-widget').forEach(root => {
    const form = root.querySelector('.bw-form');
    const input = root.querySelector('.bw-input');
    const drop = root.querySelector('.bw-drop');
    const count = root.querySelector('.bw-count');
    const list = root.querySelector('.bw-files');
    const submit = root.querySelector('.bw-submit');
    const clear = root.querySelector('.bw-clear');
    const result = root.querySelector('.bw-result');
    const positionSel = root.querySelector('.bw-position');
    let selected = [];
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    function setFiles(files) {
      selected = Array.from(files || []).filter(f => /\.(pdf|docx|txt)$/i.test(f.name)).slice(0, 10);
      count.textContent = selected.length ? `${selected.length} resume${selected.length > 1 ? 's' : ''} selected` : 'No files selected';
      list.innerHTML = selected.map((f,i) => `<div>${i+1}. ${esc(f.name)} <span class="text-muted">(${Math.round(f.size/1024)} KB)</span></div>`).join('');
      submit.disabled = selected.length === 0;
      if ((files || []).length > 10) list.insertAdjacentHTML('afterbegin','<div class="text-warning mb-1">Only the first 10 resumes will be processed.</div>');
    }
    drop.addEventListener('click', () => input.click());
    drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }});
    input.addEventListener('change', e => setFiles(e.target.files));
    ['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag-active'); }));
    ['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag-active'); }));
    drop.addEventListener('drop', e => setFiles(e.dataTransfer.files));
    clear.addEventListener('click', () => { selected=[]; input.value=''; setFiles([]); result.innerHTML=''; });

    const badge = st => ({Queued:'text-bg-secondary', Processing:'text-bg-primary', Done:'text-bg-success', Error:'text-bg-danger'}[st] || 'text-bg-light');
    function interviewCell(x) {
      if (x.interview_status === 'scheduled') {
        const mail = x.email_status === 'queued' ? 'email queued' : x.email_status === 'queued_smtp_missing' ? 'email queued (SMTP missing)' : x.email_status === 'no_email' ? 'no email on file' : '';
        return `<span class="text-success">${esc((x.interview_at||'').replace('T',' '))}</span><div class="text-muted">${esc(x.interviewer||'')}${mail ? ' · ' + mail : ''}</div>`;
      }
      if (x.interview_status === 'already_scheduled') return `<span class="text-muted">Already scheduled ${esc((x.interview_at||'').replace('T',' '))}</span>`;
      if (x.interview_status) return `<span class="text-warning">${esc(x.note || x.interview_status)}</span>`;
      return '<span class="text-muted">—</span>';
    }
    function renderRun(run) {
      const rows = (run.items || []).map(x => `<tr>
        <td><a href="/candidates/${x.candidate_id}">${esc(x.name)}</a><div class="text-muted">${esc(x.file || '')}</div></td>
        <td><span class="badge ${badge(x.state)}">${esc(x.state)}</span></td>
        <td>${x.score == null ? '—' : esc(x.score) + '%'}</td>
        <td>${esc(x.decision || x.status || '—')}</td>
        <td>${interviewCell(x)}${x.error ? `<div class="text-danger">${esc(x.error)}</div>` : ''}${x.note && x.interview_status === '' ? `<div class="text-muted">${esc(x.note)}</div>` : ''}</td></tr>`).join('');
      const finished = run.status !== 'Running';
      return `<div class="small mb-2">${finished ? '<b>Finished.</b> ' : '<span class="spinner-border spinner-border-sm me-1"></span><b>Screening against “' + esc(run.position) + '”…</b> '}${run.done}/${run.total} resumes processed</div>
        <div class="table-responsive"><table class="table table-sm align-middle small mb-2"><thead><tr><th>Candidate</th><th>State</th><th>Score</th><th>Decision</th><th>Interview</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${finished ? '<button type="button" class="btn btn-sm btn-outline-primary bw-reload">Refresh page</button>' : ''}`;
    }
    async function poll(runId) {
      try {
        const r = await fetch('/candidates/batch-status/' + runId, { headers: {'X-Requested-With':'XMLHttpRequest'}, cache:'no-store' });
        const run = await r.json();
        if (!run.ok) throw new Error(run.error || 'Status check failed');
        result.innerHTML = renderRun(run);
        if (run.status === 'Running') { setTimeout(() => poll(runId), 2000); }
        else {
          const rl = result.querySelector('.bw-reload'); if (rl) rl.addEventListener('click', () => window.location.reload());
          submit.disabled = false;
        }
      } catch (err) {
        result.innerHTML = `<div class="alert alert-warning mb-0">Could not read progress (${esc(err.message || err)}). The screening keeps running; refresh the page in a minute.</div>`;
        submit.disabled = false;
      }
    }

    form.addEventListener('submit', async e => {
      e.preventDefault();
      if (!selected.length) return;
      submit.disabled = true;
      result.innerHTML = '<div class="alert alert-info mb-0">Uploading resumes…</div>';
      const fd = new FormData();
      fd.append('_csrf_token', form.querySelector('[name="_csrf_token"]').value);
      if (positionSel && positionSel.value) fd.append('job_id', positionSel.value);
      fd.append('threshold', String(Number(root.dataset.threshold || 80)));
      selected.forEach(f => fd.append('resumes', f));
      try {
        const r = await fetch('/candidates/batch-upload', { method:'POST', body:fd });
        const data = await r.json();
        if (!r.ok || !data.ok) throw new Error(data.error || 'Batch upload failed');
        const skipped = data.errors?.length ? `<div class="alert alert-warning mt-2 mb-0"><strong>Skipped:</strong><br>${data.errors.map(x => esc(x)).join('<br>')}</div>` : '';
        if (data.screening && data.run_id) {
          result.innerHTML = skipped + '<div class="alert alert-info mt-2 mb-0">Uploaded ' + data.count + ' resume(s). AI screening started…</div>';
          poll(data.run_id);
        } else {
          result.innerHTML = `<div class="alert alert-success mb-0">Uploaded ${data.count} resume${data.count === 1 ? '' : 's'}. No position was selected, so AI screening was not run.</div>` + skipped;
          setTimeout(() => window.location.reload(), 1200);
        }
      } catch (err) {
        result.innerHTML = `<div class="alert alert-danger mb-0">${esc(err.message || err)}</div>`;
        submit.disabled = false;
      }
    });
  });
});

/* Global header search and notification bell */
document.addEventListener("DOMContentLoaded", () => {
  const aiIndicator=document.getElementById("aiConnectionIndicator");
  if(aiIndicator){ const checkAi=()=>{ fetch("/api/ai-status",{headers:{"X-Requested-With":"XMLHttpRequest"},cache:"no-store"}).then(r=>r.json()).then(x=>{ const ok=!!x.connected; aiIndicator.classList.toggle("connected",ok); aiIndicator.classList.toggle("disconnected",!ok); aiIndicator.innerHTML='<i class="bi bi-circle-fill"></i> AI '+(ok?"Connected":"Not Connected"); aiIndicator.title=ok?"AI service is reachable":"AI service unavailable: "+(x.error||"check .env and model"); }).catch(()=>{ aiIndicator.classList.remove("connected"); aiIndicator.classList.add("disconnected"); aiIndicator.innerHTML='<i class="bi bi-circle-fill"></i> AI Not Connected'; }); }; checkAi(); setInterval(()=>{ if(!document.hidden) checkAi(); },60000); }
  const thresholdInput=document.getElementById("aiThreshold");
  if(thresholdInput){ const saved=Number(localStorage.getItem("ats_ai_threshold")||thresholdInput.value||80); thresholdInput.value=saved; thresholdInput.addEventListener("change",()=>{ let v=Math.max(1,Math.min(100,Number(thresholdInput.value||80))); thresholdInput.value=v; localStorage.setItem("ats_ai_threshold",String(v)); }); }
  const searchInput = document.getElementById("globalSearchInput");
  const searchResults = document.getElementById("globalSearchResults");
  const searchWrap = document.getElementById("globalSearchWrap");
  let searchTimer = null;

  function esc(v) {
    return String(v ?? "").replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[c]));
  }

  async function doGlobalSearch(q) {
    if (!searchResults) return;
    if (q.length < 2) { searchResults.hidden = true; searchResults.innerHTML=""; return; }
    searchResults.hidden = false;
    searchResults.innerHTML = '<div class="search-loading">Searching…</div>';
    try {
      const r = await fetch("/api/global-search?q=" + encodeURIComponent(q), {
        headers: {"X-Requested-With":"XMLHttpRequest"}, cache:"no-store"
      });
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || "Search failed");
      if (!data.results.length) {
        searchResults.innerHTML = '<div class="search-empty"><i class="bi bi-search me-1"></i>No matching candidates, jobs or companies.</div>';
        return;
      }
      searchResults.innerHTML = data.results.map(x =>
        `<a class="search-result-item" href="${esc(x.url)}">
          <span class="search-result-icon"><i class="bi ${x.type==="Candidate"?"bi-person":"bi-building"}"></i></span>
          <span class="search-result-copy"><b>${esc(x.title)}</b><small>${esc(x.type)}${x.meta ? " · "+esc(x.meta) : ""}</small></span>
        </a>`
      ).join("");
    } catch (e) {
      searchResults.innerHTML = '<div class="search-empty text-danger">Search is temporarily unavailable.</div>';
    }
  }

  searchInput?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => doGlobalSearch(searchInput.value.trim()), 220);
  });
  searchInput?.addEventListener("focus", () => {
    if (searchInput.value.trim().length >= 2) doGlobalSearch(searchInput.value.trim());
  });
  document.addEventListener("click", e => {
    if (searchWrap && !searchWrap.contains(e.target)) {
      if (searchResults) searchResults.hidden = true;
    }
  });
  searchInput?.addEventListener("keydown", e => {
    if (e.key === "Escape") { searchResults.hidden=true; searchInput.blur(); }
  });

  const bell = document.getElementById("notificationBellBtn");
  const panel = document.getElementById("notificationPanel");
  const list = document.getElementById("notificationList");
  const count = document.getElementById("notificationCount");
  const close = document.getElementById("notificationClose");
  const clearBtn = document.getElementById("notificationClear");
  const csrfMeta = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
  const setCount = n => { if (!count) return; count.textContent = String(n || 0); count.style.display = n ? "inline-flex" : "none"; };
  async function postClear(body) {
    const r = await fetch("/api/notifications/clear", {method:"POST", headers:{"Content-Type":"application/json","X-Requested-With":"XMLHttpRequest","X-CSRF-Token":csrfMeta()}, body:JSON.stringify(body||{})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error("clear failed");
  }

  async function loadNotifications(quiet) {
    if (!list) return;
    if (!quiet) list.innerHTML = '<div class="text-muted small p-3">Loading…</div>';
    try {
      const r = await fetch("/api/notifications", {headers:{"X-Requested-With":"XMLHttpRequest"}, cache:"no-store"});
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error("Notification load failed");
      setCount(data.count);
      if (clearBtn) clearBtn.hidden = !data.items?.length;
      if (!data.items?.length) {
        list.innerHTML = '<div class="text-muted small p-3">No notifications.</div>';
        return;
      }
      list.innerHTML = data.items.map(n =>
        `<div class="notification-item" data-nid="${Number(n.id)}">
          <div class="notification-dot"></div>
          <div class="notification-body"><b>${esc(n.title)}</b><p>${esc(n.message)}</p><small>${esc(n.time)}</small></div>
          <button type="button" class="notification-x" data-nid="${Number(n.id)}" title="Clear this notification" aria-label="Clear this notification">&times;</button>
        </div>`
      ).join("");
    } catch (e) {
      list.innerHTML = '<div class="text-danger small p-3">Notifications are temporarily unavailable.</div>';
    }
  }

  bell?.addEventListener("click", async e => {
    e.stopPropagation();
    const open = !panel.hidden;
    panel.hidden = open;
    bell.setAttribute("aria-expanded", open ? "false" : "true");
    if (!open) await loadNotifications();
  });
  close?.addEventListener("click", () => { panel.hidden=true; bell?.setAttribute("aria-expanded","false"); });
  clearBtn?.addEventListener("click", async e => {
    e.stopPropagation();
    try { await postClear({}); setCount(0); clearBtn.hidden = true; list.innerHTML = '<div class="text-muted small p-3">No notifications.</div>'; }
    catch (err) { list.insertAdjacentHTML("afterbegin", '<div class="text-danger small px-3 pt-2">Could not clear right now. Try again.</div>'); }
  });
  list?.addEventListener("click", async e => {
    const x = e.target.closest(".notification-x"); if (!x) return;
    e.stopPropagation();
    try { await postClear({id: Number(x.dataset.nid)}); x.closest(".notification-item")?.remove(); const left = list.querySelectorAll(".notification-item").length; setCount(left); if (!left) { clearBtn && (clearBtn.hidden = true); list.innerHTML = '<div class="text-muted small p-3">No notifications.</div>'; } }
    catch (err) {}
  });
  if (bell) loadNotifications(true);   // show the unread count on page load
  document.addEventListener("click", e => {
    if (panel && !panel.hidden && !e.target.closest(".notification-wrap")) {
      panel.hidden=true; bell?.setAttribute("aria-expanded","false");
    }
  });
});

(function(){
  const sidebar=document.getElementById("mainSidebar");
  const toggle=document.getElementById("sidebarToggleBtn");
  const KEY="recruiterats_sidebar_collapsed";
  function sync(){
    if(!sidebar) return;
    const collapsed=document.body.classList.contains("sidebar-collapsed");
    sidebar.querySelectorAll(".nav-link").forEach(a=>{
      const label=a.querySelector("span");
      if(label && !a.dataset.iconTitle) a.dataset.iconTitle=label.textContent.trim();
      if(collapsed && a.dataset.iconTitle) a.title=a.dataset.iconTitle; else a.removeAttribute("title");
    });
    if(toggle) toggle.title=collapsed?"Show sidebar":"Hide sidebar";
  }
  try{ if(localStorage.getItem(KEY)==="1") document.body.classList.add("sidebar-collapsed"); }catch(e){}
  toggle?.addEventListener("click",()=>{
    setTimeout(()=>{
      try{ localStorage.setItem(KEY,document.body.classList.contains("sidebar-collapsed")?"1":"0"); }catch(e){}
      sync();
    },30);
  });
  window.addEventListener("load",sync);
  new MutationObserver(sync).observe(document.body,{attributes:true,attributeFilter:["class"]});
})();

/* Status pill colouring (visual only) */
document.addEventListener("DOMContentLoaded", () => {
  const map = [
    [/^(active|open|shortlisted|hired|offer|completed|confirmed|connected|enabled)/i, "is-green"],
    [/^(pending|paused|on hold|new|applied)/i, "is-amber"],
    [/^(rejected|closed|cancel|inactive|failed|disabled)/i, "is-red"],
    [/^(interview|scheduled|screened)/i, ""],
    [/^(admin|manager)/i, "is-purple"]
  ];
  document.querySelectorAll(".pill,.status-pill").forEach(el => {
    const t = el.textContent.trim();
    for (const [re, cls] of map) if (re.test(t)) { if (cls) el.classList.add(cls); break; }
  });
});


/* Day / night mode: saved in this browser; defaults to the device setting on first visit. */
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('themeToggleBtn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-bs-theme', next);
    try { localStorage.setItem('ats-theme', next); } catch (e) {}
  });
});


/* Back buttons: go to the previous page when it was inside this app, otherwise to the fallback page. */
document.addEventListener('click', e => {
  const a = e.target.closest('[data-back-fallback]');
  if (!a) return;
  let sameSite = false;
  try { sameSite = document.referrer && new URL(document.referrer).origin === location.origin; } catch (err) {}
  if (sameSite && history.length > 1) { e.preventDefault(); history.back(); }
});

/* AI ON/OFF small buttons */
document.addEventListener('change', async function (ev) {
  const el = ev.target.closest && ev.target.closest('.ai-switch'); if (!el) return;
  const key = el.dataset.aiKey, val = el.checked ? '1' : '0';
  const body = new URLSearchParams({ key: key, value: val });
  try {
    const r = await fetch('/ai/switch', { method: 'POST', body: body, headers: { 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' } });
    const j = await r.json(); if (!j.ok) throw new Error(j.error || 'failed');
    document.querySelectorAll('.ai-switch[data-ai-key="' + key + '"]').forEach(x => x.checked = el.checked);
    if (key === 'ai_master') { const p = document.getElementById('aiPill'); if (p) { p.classList.toggle('on', el.checked); document.getElementById('aiPillText').textContent = 'AI ' + (el.checked ? 'ON' : 'OFF'); } }
  } catch (e) { el.checked = !el.checked; alert('Could not change the AI setting. ' + (e.message || '')); }
});
