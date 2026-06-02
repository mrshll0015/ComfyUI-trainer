const CRITERIA = [
  { key: "overall", label: "Correct result" },
  { key: "face", label: "Face" },
  { key: "hands", label: "Hands" },
  { key: "fingers", label: "Fingers" },
  { key: "body", label: "Body" },
  { key: "skin_tone", label: "Skin tone" },
];

const MAX_PHOTOS = 10;

let currentView = "generate";
let currentTab = "pending";
let items = [];
let selectedId = null;
let uploadedImages = [];
let uploadPreviewUrls = [];
let promptsData = null;
let batchPollTimer = null;

const $ = (sel) => document.querySelector(sel);

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function showView(name) {
  currentView = name;
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  $("#viewGenerate").classList.toggle("hidden", name !== "generate");
  $("#viewRate").classList.toggle("hidden", name !== "rate");
  if (name === "rate") loadHistory();
}

async function loadActionForProfile() {
  promptsData = await api("/api/prompts");
  const profile = $("#promptProfile").value;
  $("#actionInput").value = promptsData[profile]?.action || "";
}

function currentAction() {
  return ($("#actionInput").value || "").trim();
}

function buildSliders() {
  const wrap = $("#sliders");
  wrap.innerHTML = "";
  for (const c of CRITERIA) {
    const row = document.createElement("div");
    row.className = "slider-row";
    row.innerHTML = `
      <label><span>${c.label}</span><strong data-val="${c.key}">7</strong></label>
      <input type="range" min="1" max="10" value="7" name="${c.key}" />
    `;
    const input = row.querySelector("input");
    const val = row.querySelector(`[data-val="${c.key}"]`);
    input.addEventListener("input", () => { val.textContent = input.value; });
    wrap.appendChild(row);
  }
}

function mediaUrl(item) {
  const path = item.file_path || "";
  const base = path.split("/output/").pop();
  if (base) return `/api/media?path=${encodeURIComponent(base)}`;
  return "";
}

function updatePendingUI(stats) {
  const videos = stats?.videos ?? 0;
  const total = stats?.total ?? 0;
  const badge = $("#pendingBadge");
  const startBtn = $("#startRateBtn");
  const summary = $("#pendingSummary");

  if (total > 0) {
    badge.classList.remove("hidden");
    badge.textContent = `${videos} video${videos !== 1 ? "s" : ""} · ${total} unrated`;
    startBtn.classList.remove("hidden");
    summary.textContent = `${videos} video(s) and ${total} total output(s) waiting for rating.`;
  } else {
    badge.classList.add("hidden");
    startBtn.classList.add("hidden");
    summary.textContent = "No unrated outputs. Generate a batch first.";
  }
}

function updateLearnUI(learn) {
  if (!learn) return;
  const text = $("#learnStatusText");
  const blockers = $("#learnBlockers");
  const settings = $("#learnSettings");
  if (!text) return;

  const stageLabels = {
    idle: "Idle — generate a batch to begin",
    sync_needed: "Sync needed — run settings not saved yet",
    rating: "Rating — score outputs to start learning",
    learning: "Learning active — profile built from ratings",
    profile_empty: "Rated but profile has no settings yet",
    blocked: "Blocked — check issues below",
  };
  const stage = learn.stage || "idle";
  const active = learn.learning_active ? "yes" : "no";
  text.textContent =
    `${stageLabels[stage] || stage} · learning active: ${active} · ` +
    `gens ${learn.generations_with_run_json}/${learn.generations_total} with run_json · ` +
    `ratings ${learn.ratings_count} (${learn.rated_with_run_json} usable)`;

  if (learn.blockers?.length) {
    blockers.classList.remove("hidden");
    blockers.innerHTML = learn.blockers.map((b) => `<li>${b}</li>`).join("");
  } else {
    blockers.classList.add("hidden");
    blockers.innerHTML = "";
  }

  if (learn.settings_learned?.length) {
    settings.classList.remove("hidden");
    settings.textContent = `Learned keys: ${learn.settings_learned.join(", ")}`;
  } else {
    settings.classList.add("hidden");
    settings.textContent = "";
  }
}

async function refreshStats() {
  try {
    const h = await api("/api/health");
    const el = $("#comfyStatus");
    el.textContent = h.comfy_ui ? "ComfyUI online" : "ComfyUI offline";
    el.className = "badge " + (h.comfy_ui ? "ok" : "bad");
    updatePendingUI(h.pending);
    updateLearnUI(h.learn);
    return h.pending;
  } catch {
    $("#comfyStatus").textContent = "Trainer error";
    $("#comfyStatus").className = "badge bad";
    return { total: 0, videos: 0 };
  }
}

function parseAction(item) {
  if (!item?.run_json) return "";
  try {
    const run = typeof item.run_json === "string" ? JSON.parse(item.run_json) : item.run_json;
    return run.action || "";
  } catch {
    return "";
  }
}

function parseSourceImage(item) {
  if (!item?.run_json) return "";
  try {
    const run = typeof item.run_json === "string" ? JSON.parse(item.run_json) : item.run_json;
    return run.source_image || "";
  } catch {
    return "";
  }
}

function updateGenerateBtn() {
  const n = uploadedImages.length;
  const btn = $("#generateBtn");
  btn.disabled = n === 0;
  btn.textContent = n === 1 ? "Generate 1 video" : `Generate ${n} videos`;
}

function renderUploadGrid(files) {
  const grid = $("#uploadGrid");
  grid.innerHTML = "";
  uploadPreviewUrls.forEach((url) => URL.revokeObjectURL(url));
  uploadPreviewUrls = [];
  for (const file of files) {
    const url = URL.createObjectURL(file);
    uploadPreviewUrls.push(url);
    const img = document.createElement("img");
    img.src = url;
    img.alt = file.name;
    grid.appendChild(img);
  }
}

function renderQueue() {
  const list = $("#queueList");
  list.innerHTML = "";
  let filtered = items;
  if (currentTab === "pending") filtered = items.filter((i) => i.overall == null);
  else if (currentTab === "videos") filtered = items.filter((i) => (i.media_type || "") === "video");

  if (!filtered.length) {
    list.innerHTML = `<li class="muted">No items</li>`;
    return;
  }

  for (const item of filtered) {
    const li = document.createElement("li");
    li.className = "queue-item" + (item.id === selectedId ? " active" : "");
    const name = (item.file_path || "").split("/").pop();
    const src = parseSourceImage(item);
    const score = item.overall != null ? `Rated · ${item.overall}/10` : "Not rated";
    li.innerHTML = `<div class="name">${name}</div><div class="score">${score}${src ? ` · ${src}` : ""}</div>`;
    li.onclick = () => selectItem(item.id);
    list.appendChild(li);
  }
}

function selectItem(id) {
  selectedId = id;
  const item = items.find((i) => i.id === id);
  if (!item) return;

  $("#emptyState").classList.add("hidden");
  $("#ratingPanel").classList.remove("hidden");

  const img = $("#previewImg");
  const vid = $("#previewVideo");
  const url = mediaUrl(item);
  img.classList.add("hidden");
  vid.classList.add("hidden");

  const isVideo = (item.media_type || "") === "video" || /\.(mp4|webm|gif)$/i.test(url);
  if (isVideo) {
    vid.src = url;
    vid.classList.remove("hidden");
  } else {
    img.src = url;
    img.classList.remove("hidden");
  }

  const src = parseSourceImage(item);
  const act = parseAction(item);
  $("#metaInfo").innerHTML = `
    <div><strong>ID</strong> ${item.id} · ${item.media_type || "image"}</div>
    ${act ? `<div><strong>Action</strong> ${act}</div>` : ""}
    ${src ? `<div><strong>Source photo</strong> ${src}</div>` : ""}
    <div>${item.file_path || ""}</div>
    <div>${new Date((item.created_at || 0) * 1000).toLocaleString()}</div>
  `;

  for (const c of CRITERIA) {
    const input = document.querySelector(`input[name="${c.key}"]`);
    const val = item[c.key] ?? 7;
    if (input) {
      input.value = val;
      document.querySelector(`[data-val="${c.key}"]`).textContent = val;
    }
  }
  $("#comment").value = item.rating_comment || "";
  renderQueue();
}

async function loadHistory() {
  let url = `/api/history?pending=${currentTab === "pending" ? "1" : "0"}&limit=100`;
  if (currentTab === "videos") url += "&media=video";
  const data = await api(url);
  items = data.items || [];
  renderQueue();
}

async function loadProfile() {
  try {
    const p = await api("/api/profile");
    $("#profileEmpty").classList.add("hidden");
    $("#profileJson").classList.remove("hidden");
    $("#profileJson").textContent = JSON.stringify(p, null, 2);
  } catch {
    $("#profileEmpty").classList.remove("hidden");
    $("#profileJson").classList.add("hidden");
  }
}

async function syncAndStartRating() {
  try {
    const r = await api("/api/sync", { method: "POST", body: "{}" });
    toast(`Synced ${r.synced} outputs`);
    updatePendingUI(r.pending);
    updateLearnUI(r.learn);
    showView("rate");
    currentTab = "pending";
    document.querySelectorAll("#viewRate .tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === "pending");
    });
    await loadHistory();
    const next = items.find((i) => i.overall == null);
    if (next) selectItem(next.id);
  } catch (e) {
    toast(e.message);
  }
}

async function applyProfile() {
  try {
    const r = await api("/api/apply", { method: "POST", body: JSON.stringify({ explore: false }) });
    toast(`Applied profile (${r.result.text_fields_updated} action, ${r.result.sampler_fields_updated} sampler fields)`);
    await loadProfile();
  } catch (e) {
    toast(e.message);
  }
}

// Upload (up to 10 photos)
$("#photoInput").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []).slice(0, MAX_PHOTOS);
  if (!files.length) return;
  if ((e.target.files || []).length > MAX_PHOTOS) {
    toast(`Only first ${MAX_PHOTOS} photos used`);
  }

  const fd = new FormData();
  for (const file of files) fd.append("file", file);

  $("#generateBtn").disabled = true;
  $("#uploadName").textContent = `Uploading ${files.length} photo(s)…`;
  renderUploadGrid(files);

  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");
    uploadedImages = data.filenames || (data.filename ? [data.filename] : []);
    $("#uploadName").textContent = `${uploadedImages.length} photo(s) ready`;
    updateGenerateBtn();
    toast(`Uploaded ${uploadedImages.length} photo(s)`);
  } catch (err) {
    uploadedImages = [];
    renderUploadGrid([]);
    updateGenerateBtn();
    toast(err.message);
  }
});

function pollBatch(batchId, total) {
  if (batchPollTimer) clearInterval(batchPollTimer);
  $("#genProgress").classList.remove("hidden");
  batchPollTimer = setInterval(async () => {
    try {
      const s = await api(`/api/generate/status?batch_id=${batchId}`);
      const pct = Math.round((s.completed / total) * 100);
      $("#progressFill").style.width = `${pct}%`;
      $("#genStatusText").textContent = `Status: ${s.status} · ${s.completed}/${total} done · queue ${s.queue_pending} pending`;
      updatePendingUI({ videos: s.pending_videos, total: s.pending_total });
      if (s.status === "done") {
        clearInterval(batchPollTimer);
        batchPollTimer = null;
        toast(`Batch complete — ${s.pending_videos} videos to rate`);
        $("#genStatusText").textContent += " · Sync & rate when ready";
      }
    } catch (err) {
      clearInterval(batchPollTimer);
      toast(err.message);
    }
  }, 4000);
}

$("#generateBtn").onclick = async () => {
  if (!uploadedImages.length) return toast("Upload photos first");
  const action = currentAction();
  if (!action) return toast("Describe what should happen (one line)");
  $("#generateBtn").disabled = true;
  const count = uploadedImages.length;
  try {
    const body = {
      image_names: uploadedImages,
      prompt_profile: $("#promptProfile").value,
      action,
    };
    const r = await api("/api/generate", { method: "POST", body: JSON.stringify(body) });
    toast(`Queued ${r.queued} video(s) — 1 per photo`);
    pollBatch(r.batch_id, count);
  } catch (e) {
    toast(e.message);
  } finally {
    updateGenerateBtn();
  }
};

$("#promptProfile").addEventListener("change", loadActionForProfile);

$("#ratingForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedId) return;
  const body = { generation_id: selectedId, comment: $("#comment").value };
  for (const c of CRITERIA) {
    body[c.key] = Number(document.querySelector(`input[name="${c.key}"]`).value);
  }
  try {
    const r = await api("/api/rate", { method: "POST", body: JSON.stringify(body) });
    toast(r.learn?.learning_active ? "Rating saved — learning active" : "Rating saved — see Learning status");
    updatePendingUI(r.pending);
    updateLearnUI(r.learn);
    if (r.profile) {
      $("#profileEmpty").classList.add("hidden");
      $("#profileJson").classList.remove("hidden");
      $("#profileJson").textContent = JSON.stringify(r.profile, null, 2);
    }
    currentTab = "pending";
    document.querySelector('#viewRate .tab[data-tab="pending"]').click();
    await loadHistory();
    const next = items.find((i) => i.overall == null);
    if (next) selectItem(next.id);
    else {
      $("#ratingPanel").classList.add("hidden");
      $("#emptyState").classList.remove("hidden");
      selectedId = null;
    }
  } catch (err) {
    toast(err.message);
  }
});

$("#skipBtn").onclick = () => {
  const idx = items.findIndex((i) => i.id === selectedId);
  const next = items.slice(idx + 1).find((i) => i.overall == null) || items.find((i) => i.overall == null);
  if (next) selectItem(next.id);
};

document.querySelectorAll("#viewRate .tab").forEach((tab) => {
  tab.onclick = async () => {
    document.querySelectorAll("#viewRate .tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentTab = tab.dataset.tab;
    await loadHistory();
  };
});

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.onclick = () => showView(btn.dataset.view);
});

$("#syncAndRateBtn").onclick = syncAndStartRating;
$("#startRateBtn").onclick = syncAndStartRating;
$("#applyBtn").onclick = applyProfile;

buildSliders();
updateGenerateBtn();
loadActionForProfile();
refreshStats();
loadProfile();
setInterval(refreshStats, 15000);
