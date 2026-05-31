const CRITERIA = [
  { key: "overall", label: "Overall" },
  { key: "face", label: "Face" },
  { key: "identity", label: "Identity match" },
  { key: "hands", label: "Hands" },
  { key: "fingers", label: "Fingers" },
  { key: "body", label: "Body anatomy" },
  { key: "skin_tone", label: "Skin tone" },
  { key: "motion", label: "Motion / pose" },
  { key: "lighting", label: "Lighting" },
  { key: "artifacts", label: "Few artifacts" },
];

const PROMPT_LABELS = [
  "Positive inpaint 1",
  "Negative inpaint 1",
  "Positive inpaint 2 lower body",
  "Negative inpaint 2",
  "Video motion prompt",
  "Video negative",
  "Skin tone prompt (photo + inpaint)",
  "Skin tone prompt (video)",
];

let currentView = "generate";
let currentTab = "pending";
let items = [];
let selectedId = null;
let uploadedImage = null;
let promptsData = null;
let activePromptProfile = "prompt_1";
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
  $("#viewPrompts").classList.toggle("hidden", name !== "prompts");
  if (name === "rate") loadHistory();
  if (name === "prompts") loadPromptsEditor();
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

async function refreshStats() {
  try {
    const h = await api("/api/health");
    const el = $("#comfyStatus");
    el.textContent = h.comfy_ui ? "ComfyUI online" : "ComfyUI offline";
    el.className = "badge " + (h.comfy_ui ? "ok" : "bad");
    updatePendingUI(h.pending);
    return h.pending;
  } catch {
    $("#comfyStatus").textContent = "Trainer error";
    $("#comfyStatus").className = "badge bad";
    return { total: 0, videos: 0 };
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
    const score = item.overall != null ? `Rated · ${item.overall}/10` : "Not rated";
    li.innerHTML = `<div class="name">${name}</div><div class="score">${score}</div>`;
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

  $("#metaInfo").innerHTML = `
    <div><strong>ID</strong> ${item.id} · ${item.media_type || "image"}</div>
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
    toast(`Applied profile (${r.result.text_fields_updated} prompts, ${r.result.sampler_fields_updated} sampler fields)`);
    await loadProfile();
  } catch (e) {
    toast(e.message);
  }
}

// Upload
$("#photoInput").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  $("#generateBtn").disabled = true;
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");
    uploadedImage = data.filename;
    $("#uploadName").textContent = uploadedImage;
    $("#uploadPreview").src = URL.createObjectURL(file);
    $("#uploadPreview").classList.remove("hidden");
    $("#generateBtn").disabled = false;
    toast("Photo uploaded");
  } catch (err) {
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
  if (!uploadedImage) return toast("Upload a photo first");
  $("#generateBtn").disabled = true;
  try {
    const body = {
      image_name: uploadedImage,
      prompt_profile: $("#promptProfile").value,
      count: Number($("#genCount").value),
    };
    const r = await api("/api/generate", { method: "POST", body: JSON.stringify(body) });
    toast(`Queued ${r.queued} generations (${body.prompt_profile})`);
    pollBatch(r.batch_id, body.count);
  } catch (e) {
    toast(e.message);
  } finally {
    $("#generateBtn").disabled = false;
  }
};

// Prompts editor
async function loadPromptsEditor() {
  promptsData = await api("/api/prompts");
  renderPromptFields();
}

function renderPromptFields() {
  const wrap = $("#promptFields");
  wrap.innerHTML = "";
  const prof = promptsData[activePromptProfile];
  if (!prof) return;
  for (const label of PROMPT_LABELS) {
    const val = prof.nodes?.[label] || "";
    const id = `pf_${label.replace(/\W+/g, "_")}`;
    wrap.innerHTML += `
      <label for="${id}">${label}
        <textarea id="${id}" data-label="${label}">${val}</textarea>
      </label>`;
  }
}

$("#savePromptsBtn").onclick = async () => {
  if (!promptsData) await loadPromptsEditor();
  const prof = promptsData[activePromptProfile];
  document.querySelectorAll("#promptFields textarea").forEach((ta) => {
    prof.nodes[ta.dataset.label] = ta.value;
  });
  try {
    await fetch("/api/prompts", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(promptsData),
    });
    toast("Prompts saved");
  } catch (e) {
    toast("Save failed");
  }
};

document.querySelectorAll(".prompt-tabs .tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".prompt-tabs .tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    activePromptProfile = tab.dataset.prompt;
    renderPromptFields();
  };
});

$("#ratingForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedId) return;
  const body = { generation_id: selectedId, comment: $("#comment").value };
  for (const c of CRITERIA) {
    body[c.key] = Number(document.querySelector(`input[name="${c.key}"]`).value);
  }
  try {
    const r = await api("/api/rate", { method: "POST", body: JSON.stringify(body) });
    toast("Rating saved — profile updated");
    updatePendingUI(r.pending);
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
refreshStats();
loadProfile();
setInterval(refreshStats, 15000);
