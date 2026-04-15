const API_BASE = window.location.origin;

function getHeaders() {
  const key = document.getElementById("api-key").value;
  return {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${key}`,
  };
}

async function generateStory() {
  const premise = document.getElementById("premise").value.trim();
  if (!premise) return;

  const label = document.getElementById("label").value.trim() || null;
  const statusEl = document.getElementById("generate-status");
  const btn = document.getElementById("generate-btn");

  btn.disabled = true;
  statusEl.textContent = "Submitting...";

  try {
    const resp = await fetch(`${API_BASE}/v1/stories/generate`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify({ premise, label }),
    });

    if (!resp.ok) {
      const err = await resp.text();
      statusEl.textContent = `Error: ${resp.status} — ${err}`;
      return;
    }

    const data = await resp.json();
    statusEl.textContent = `Started! Story ID: ${data.story_id}`;
    pollStory(data.story_id);
  } catch (e) {
    statusEl.textContent = `Error: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function pollStory(storyId) {
  const statusEl = document.getElementById("generate-status");
  const poll = async () => {
    try {
      const resp = await fetch(`${API_BASE}/v1/stories/${storyId}/status`, {
        headers: getHeaders(),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      const badge = statusBadge(data.status);
      statusEl.innerHTML = `${badge} Score: ${data.review_score ?? "—"} | Words: ${data.total_word_count ?? "—"} | Loops: ${data.review_loops}`;

      if (data.status === "completed" || data.status === "failed") {
        loadStories();
        return;
      }
      setTimeout(poll, 5000);
    } catch {
      setTimeout(poll, 10000);
    }
  };
  poll();
}

function statusBadge(status) {
  return `<span class="badge badge-${status}">${status}</span>`;
}

async function loadStories() {
  const listEl = document.getElementById("stories-list");
  try {
    const resp = await fetch(`${API_BASE}/v1/stories?limit=20`, {
      headers: getHeaders(),
    });
    if (!resp.ok) {
      listEl.textContent = "Failed to load stories";
      return;
    }
    const stories = await resp.json();
    if (!stories.length) {
      listEl.textContent = "No stories yet.";
      return;
    }
    listEl.innerHTML = stories.map(s => `
      <div class="story-card" onclick="showStory('${s.id}')">
        <div class="title">${s.label || s.premise.substring(0, 80)}...</div>
        <div class="meta">
          ${statusBadge(s.status)}
          Score: ${s.review_score ?? "—"} |
          Words: ${s.total_word_count ?? "—"} |
          ${new Date(s.created_at).toLocaleString()}
        </div>
      </div>
    `).join("");
  } catch (e) {
    listEl.textContent = `Error: ${e.message}`;
  }
}

async function showStory(storyId) {
  const section = document.getElementById("detail-section");
  const detailEl = document.getElementById("story-detail");
  section.classList.remove("hidden");

  try {
    const resp = await fetch(`${API_BASE}/v1/stories/${storyId}/status`, {
      headers: getHeaders(),
    });
    if (!resp.ok) {
      detailEl.textContent = "Failed to load story";
      return;
    }
    const s = await resp.json();

    let actsHtml = "";
    if (s.acts && s.acts.length) {
      actsHtml = s.acts.map(a => `
        <div class="act-block">
          <h3>Act ${a.act_number}: ${a.title}</h3>
          <div class="meta">${a.word_count} words (target: ${a.target_word_count})</div>
          <div class="prose">${escapeHtml(a.text)}</div>
        </div>
      `).join("");
    }

    detailEl.innerHTML = `
      <div style="margin-bottom:1rem">
        ${statusBadge(s.status)}
        ${s.review_score ? `<span class="score">${s.review_score.toFixed(1)}</span>` : ""}
        <span class="meta"> | ${s.total_word_count ?? 0} words | ${s.review_loops} review loops</span>
      </div>
      <div class="form-group">
        <label>Premise</label>
        <p>${escapeHtml(s.premise)}</p>
      </div>
      ${s.error ? `<div style="color:#d45b5b;margin-bottom:1rem">Error: ${escapeHtml(s.error)}</div>` : ""}
      ${actsHtml}
    `;
  } catch (e) {
    detailEl.textContent = `Error: ${e.message}`;
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
