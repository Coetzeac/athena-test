const number = (value, digits = 2) => Number(value).toFixed(digits);
const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
})[character]);

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function paintVerdict(verdict) {
  const color = verdict === "PROMOTE" ? "var(--green)" : verdict === "REJECT" ? "var(--red)" : "var(--amber)";
  const node = document.querySelector("#verdict");
  node.style.color = color;
  node.style.borderColor = color;
}

async function render() {
  try {
    const response = await fetch("../runtime/status.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`status HTTP ${response.status}`);
    const data = await response.json();
    document.querySelector("#state").textContent = data.state;
    document.querySelector("#state-dot").style.background = "var(--green)";
    document.querySelector("#verdict").textContent = data.cycle.verdict;
    paintVerdict(data.cycle.verdict);
    document.querySelector("#strategy").textContent = data.cycle.strategy_id;
    document.querySelector("#recommendation").textContent = data.cycle.recommendation;
    document.querySelector("#cycle-id").textContent = data.cycle.id;
    document.querySelector("#market").textContent = `${data.cycle.instrument} / ${data.cycle.timeframe}`;
    document.querySelector("#updated").textContent = new Date(data.updated_at).toLocaleString();

    const m = data.metrics;
    document.querySelector("#metrics").innerHTML = [
      metric("Sample", m.sample_size),
      metric("Hit rate", `${number(m.hit_rate * 100, 1)}%`),
      metric("Expectancy", `${number(m.expectancy_r)}R`),
      metric("Profit factor", m.profit_factor === null ? "∞" : number(m.profit_factor)),
      metric("Max drawdown", `${number(m.maximum_drawdown_r)}R`),
      metric("Confidence", `${number(data.decision.confidence * 100, 1)}%`),
    ].join("");

    const failed = data.decision.gates.filter(gate => !gate.passed).length;
    document.querySelector("#gate-summary").textContent = `${data.decision.gates.length - failed} PASS / ${failed} FAIL`;
    document.querySelector("#gates").innerHTML = data.decision.gates.map(gate => `
      <div class="gate">
        <span class="gate-name">${escapeHtml(gate.gate.replaceAll("_", " "))}</span>
        <span class="gate-result ${gate.passed ? "pass" : "fail"}">${gate.passed ? "PASS" : "FAIL"}</span>
        <span class="gate-reason">${escapeHtml(gate.passed ? `actual ${gate.actual}` : gate.reason)}</span>
      </div>`).join("");

    document.querySelector("#audit-valid").textContent = data.audit.valid ? "VALID" : "INVALID";
    document.querySelector("#audit-entries").textContent = data.audit.entries;
    document.querySelector("#audit-hash").textContent = data.audit.terminal_hash;
    document.querySelector("#counter-evidence").innerHTML = data.counter_evidence.map(item => `<li>${escapeHtml(item)}</li>`).join("");
    document.querySelector("#next-action").textContent = data.next_action;
  } catch (error) {
    document.querySelector("#state").textContent = "STATUS ERROR";
    document.querySelector("#recommendation").textContent = `${error.message}. Run make demo from the repository root.`;
  }
}

render();
