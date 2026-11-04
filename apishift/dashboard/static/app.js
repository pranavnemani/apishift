// APIShift dashboard: plays a recorded run's events, or streams a live run over WebSocket.
const STAGES = [
  ["specdiff", "Spec Diff", "no LLM"], ["scout", "Scout", "Spark"], ["analyst", "Analyst", "Spark"],
  ["refactor", "Refactor", "Wonder"], ["sandbox", "Sandbox tests", "Docker"], ["triage", "Triage", "Spark / Wonder"],
  ["pr_writer", "PR Writer", "Spark"],
];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let events = [], idx = 0, timer = null, speed = 1, playing = false, currentRun = null;
let added = new Set();

function reset() {
  $("pipeline").innerHTML = STAGES.map(([k, n, m], i) =>
    `${i ? '<span class="arrow">→</span>' : ""}<div class="stage" id="st-${k}"><b>${n}</b><small>${m}</small></div>`).join("");
  $("log").innerHTML = ""; $("diff").textContent = "No changes yet."; $("failures").innerHTML = "";
  $("guards").innerHTML = '<li class="muted">No edits yet.</li>'; $("graph").innerHTML = '<span class="muted">Waiting for the Analyst.</span>';
  for (const b of ["beforeBar", "afterBar"]) $(b).style.width = "0";
  $("beforeTxt").textContent = "–"; $("afterTxt").textContent = "–"; $("testsLabel").textContent = "";
  for (const [id, v] of [["cCalls", "0"], ["cIn", "0"], ["cCached", "0%"], ["cOut", "0"], ["cUsd", "$0.000"]]) $(id).textContent = v;
  $("result").className = "bar result"; $("result").innerHTML = '<span class="muted">Running…</span>';
  added = new Set(); idx = 0;
}

function log(ev, cls = "") {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `<span class="t">${ev.t.toFixed(1)}s</span><span class="s">${esc(ev.step)}</span><span class="m">${esc(ev.message)}</span>`;
  $("log").appendChild(li); $("log").scrollTop = $("log").scrollHeight;
}

function renderDiff(text) {
  if (!text) { $("diff").textContent = "No changes yet."; return; }
  $("diff").innerHTML = text.split("\n").map((l) => {
    const c = l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@") || l.startsWith("diff ") ? "hdr"
      : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return c ? `<span class="${c}">${esc(l)}</span>` : esc(l) + "\n";
  }).join("");
}

function renderGraph(g) {
  const nodes = g.nodes.map((n) => `<div class="node ${added.has(n.file_path) ? "added" : ""}">
      <code>${esc(n.file_path)}</code> <span class="muted">${esc(n.symbol)} · lines ${n.lines.join(", ")}</span>
      <div class="act">${esc(n.action)}</div></div>`).join("");
  const edges = g.edges.map((e) => `<div class="edge">${esc(e.from_node)} ${esc(e.kind)} ${esc(e.to_node)}${e.field ? ` (${esc(e.field)})` : ""}</div>`).join("");
  $("graph").innerHTML = nodes + edges;
}

function setTests(r, which) {
  const pct = r.collected ? (100 * r.passed / r.collected) : 0;
  $(which + "Bar").style.width = pct + "%";
  $(which + "Txt").textContent = `${r.passed}/${r.collected}`;
  if (which === "after") {
    $("failures").innerHTML = r.failures.slice(0, 8).map((f) => `<li>✗ ${esc(f.nodeid)}: ${esc(f.message.slice(0, 90))}</li>`).join("");
  }
}

function guard(text, cls) {
  if ($("guards").querySelector(".muted")) $("guards").innerHTML = "";
  const li = document.createElement("li"); li.className = cls; li.textContent = text; $("guards").appendChild(li);
}

function apply(ev) {
  const st = $("st-" + ev.step);
  switch (ev.kind) {
    case "stage":
      if (st) {
        st.className = "stage " + (ev.status || "");
        if (ev.message) { const s = st.querySelector("small"); s.textContent = ev.message; s.title = ev.message; }
      }
      if (ev.graph) { (ev.added || []).forEach((f) => added.add(f)); renderGraph(ev.graph); }
      if (ev.message) log(ev, ev.status === "failed" ? "bad" : ev.status === "done" && ev.step === "sandbox" ? "good" : "");
      break;
    case "tests":
      $("testsLabel").textContent = `· ${ev.label}`;
      if (ev.label === "before migration") setTests(ev.report, "before");
      else if (ev.label.startsWith("attempt")) setTests(ev.report, "after");
      log(ev, ev.report && ev.report.failed + ev.report.errors === 0 && ev.report.collected ? "good" : "");
      break;
    case "diff": renderDiff(ev.diff); break;
    case "rejected": guard(`Blocked: ${ev.file} (${ev.reason})`, "good"); log(ev, "warn"); break;
    case "violation": guard(ev.message, "bad"); log(ev, "bad"); break;
    case "model_call": {
      const t = ev.totals || {};
      $("cCalls").textContent = t.calls ?? 0;
      $("cIn").textContent = (t.input_tokens ?? 0).toLocaleString();
      $("cOut").textContent = (t.output_tokens ?? 0).toLocaleString();
      $("cCached").textContent = t.input_tokens ? Math.round(100 * t.cached_tokens / t.input_tokens) + "%" : "0%";
      $("cUsd").textContent = "$" + (t.cost_usd ?? 0).toFixed(3);
      break;
    }
    case "end":
      if (ev.step === "run") {
        const ok = ev.status === "READY";
        $("result").className = "bar result " + (ok ? "ready" : "human");
        const run = encodeURIComponent(currentRun || "");
        const links = currentRun ? `<span><a href="/api/runs/${run}/artifacts/PR.md" target="_blank">PR body</a> · <a href="/api/runs/${run}/artifacts/final.diff" target="_blank">diff</a></span>` : "";
        $("result").innerHTML = `<span>${ok ? "✓ Ready PR" : "Draft PR, needs human"}: ${esc(ev.message)}</span>${links}`;
        if (ok && $("guards").querySelector(".muted")) guard("All edits passed guardrails", "good");
      }
      log(ev, ev.status === "READY" ? "good" : "warn");
      break;
    case "log": case "tool_call": case "start": log(ev); break;
    case "warning": log(ev, "warn"); break;
  }
}

// ---- playback ----
function stop() { clearTimeout(timer); playing = false; $("playBtn").textContent = "Play"; }
function step() { if (idx < events.length) apply(events[idx++]); if (idx >= events.length) stop(); }
function tick() {
  if (!playing || idx >= events.length) return stop();
  const prev = events[idx - 1], ev = events[idx];
  step();
  const next = events[idx];
  if (!next) return;
  const gap = Math.max(40, Math.min((next.t - ev.t) * 1000, 1500) / speed);
  timer = setTimeout(tick, prev ? gap : 40);
}
async function loadRun(id) {
  stop(); currentRun = id; reset();
  events = await (await fetch(`/api/runs/${encodeURIComponent(id)}/events`)).json();
  $("result").innerHTML = `<span class="muted">${events.length} recorded events. Press Play.</span>`;
}
async function loadRuns(select, keepView = false) {
  const runs = await (await fetch("/api/runs")).json();
  $("runSelect").innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.status)}</option>`).join("")
    || "<option value=''>No recorded runs</option>";
  const id = select || (runs[0] && runs[0].id);
  if (id) { $("runSelect").value = id; if (!keepView) await loadRun(id); }
}
function stream(id) {
  stop(); currentRun = id; reset(); events = [];
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/runs/${encodeURIComponent(id)}`);
  ws.onmessage = (m) => { const ev = JSON.parse(m.data); events.push(ev); idx = events.length; apply(ev); };
  ws.onclose = () => loadRuns(id, true).then(() => { idx = events.length; });
}

$("playBtn").onclick = () => {
  if (playing) return stop();
  if (idx >= events.length) { reset(); }
  playing = true; $("playBtn").textContent = "Pause"; tick();
};
$("stepBtn").onclick = () => { stop(); step(); };
$("runSelect").onchange = (e) => loadRun(e.target.value);
document.querySelectorAll(".seg button").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".seg button").forEach((x) => x.classList.remove("on"));
  b.classList.add("on"); speed = Number(b.dataset.speed);
});
$("liveBtn").onclick = () => { $("liveErr").textContent = ""; $("liveDialog").showModal(); };
$("liveGo").onclick = async (e) => {
  e.preventDefault();
  const mode = $("liveMode").value, key = $("liveKey").value.trim();
  const headers = { "Content-Type": "application/json" };
  if (key) headers["X-Meta-Key"] = key;
  const res = await fetch("/api/runs", { method: "POST", headers,
    body: JSON.stringify({ mode, source_run: mode === "replay" ? $("runSelect").value : null }) });
  const body = await res.json();
  if (!res.ok) { $("liveErr").textContent = body.detail || "Could not start the run"; return; }
  $("liveDialog").close(); stream(body.run_id);
};
reset();
loadRuns();
