// Live swarm console: D3 force graph + SSE bid log. No build step.
(function () {
  const A = window.VETWORK;
  const svg = d3.select("#graph");
  const log = document.getElementById("log");
  const status = document.getElementById("status");
  const phase = document.getElementById("phase");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const short = (a) => a.split(".")[0];
  const near = (y) => (Number(BigInt(y) / 10n ** 18n) / 1e6).toFixed(2);

  const nodes = [
    { id: "dao", label: short(A.dao) + " DAO", kind: "dao" },
    { id: "job", label: "job #" + A.id, kind: "job" },
    { id: "nearai", label: "NEAR AI", kind: "infra" },
    ...A.agents.map((a) => ({ id: a.id, label: a.name, kind: "agent", section: a.section })),
  ];
  const links = [
    { source: "dao", target: "job" },
    ...A.agents.map((a) => ({ source: "job", target: a.id })),
    ...A.agents.map((a) => ({ source: a.id, target: "nearai" })),
  ];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));

  let W = 600, H = 440;
  function size() {
    const r = svg.node().getBoundingClientRect();
    W = r.width || 600; H = r.height || 440;
    svg.attr("viewBox", `0 0 ${W} ${H}`);
  }
  size();
  const sections = ["history", "budget", "milestones", "treasury", "risk"];
  const angle = (s) => (sections.indexOf(s) / sections.length) * 2 * Math.PI - Math.PI / 2;
  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id).distance((l) => (l.target.id === "nearai" ? 150 : 110)).strength(0.25))
    .force("charge", d3.forceManyBody().strength(-260))
    .force("x", d3.forceX((d) => d.kind === "agent" ? W / 2 + Math.cos(angle(d.section)) * W * 0.3 : d.kind === "dao" ? W * 0.12 : d.kind === "infra" ? W * 0.88 : W / 2).strength(0.12))
    .force("y", d3.forceY((d) => d.kind === "agent" ? H / 2 + Math.sin(angle(d.section)) * H * 0.33 : H / 2).strength(0.12))
    .force("collide", d3.forceCollide(26));

  const gLinks = svg.append("g").attr("stroke", "rgba(255,255,255,.12)");
  const link = gLinks.selectAll("line").data(links).join("line");
  const gPay = svg.append("g");
  const node = svg.append("g").selectAll("g").data(nodes).join("g").attr("class", (d) => "n " + d.kind);
  node.append("circle")
    .attr("r", (d) => (d.kind === "job" ? 15 : d.kind === "agent" ? 8 : 11))
    .attr("fill", (d) => (d.kind === "job" ? "#3B6CF6" : "#0A0B0D"))
    .attr("stroke", (d) => (d.kind === "job" ? "none" : "#D0D6E0"))
    .attr("stroke-width", 1.2);
  node.append("text").text((d) => d.label).attr("x", 0).attr("y", (d) => (d.kind === "job" ? 30 : 22))
    .attr("text-anchor", "middle").attr("fill", "#8A8F98")
    .attr("paint-order", "stroke").attr("stroke", "#0A0B0D").attr("stroke-width", 4).attr("stroke-linejoin", "round").style("font", "11px 'IBM Plex Mono', monospace");
  function render() {
    nodes.forEach((d) => { const m = Math.max(30, d.label.length * 3.4 + 6); d.x = Math.max(m, Math.min(W - m, d.x)); d.y = Math.max(20, Math.min(H - 30, d.y)); });
    link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  }
  sim.on("tick", render);
  function refit(settle) {
    size();
    // forceX/forceY cache their targets: re-set the accessors so they read the new width
    sim.force("x").x(sim.force("x").x());
    sim.force("y").y(sim.force("y").y());
    sim.force("collide").radius(W < 500 ? 34 : 26);
    if (settle) {
      // lay out synchronously so the first paint is already settled, even in a throttled tab
      sim.stop().alpha(1);
      sim.tick(220);
      render();
      sim.alpha(0.05).restart();
    } else {
      sim.alpha(0.5).restart();
    }
  }
  window.addEventListener("resize", () => refit(false));
  refit(true);

  function pay(from, to, big) {
    const a = byId[from], b = byId[to];
    if (!a || !b || reduce) return;
    const c = gPay.append("circle").attr("r", big ? 4 : 2.5).attr("fill", "#3B6CF6").attr("cx", a.x).attr("cy", a.y);
    c.transition().duration(900).ease(d3.easeCubicInOut).attr("cx", b.x).attr("cy", b.y).remove();
  }
  function mark(id, f) { node.filter((d) => d.id === id).select("circle").call(f); }

  function line(html, cls) {
    const li = document.createElement("li");
    if (cls) li.className = cls;
    li.innerHTML = html;
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function sec(section, who, price, done) {
    const li = document.getElementById("sec-" + section);
    if (!li) return;
    li.querySelector(".who").textContent = who;
    if (price) li.querySelector(".price").textContent = price + " N";
    li.classList.toggle("done", !!done);
  }

  log.innerHTML = "";
  let seen = 0;
  let ended = false;
  function handle(e) {
    seen += 1;
    switch (e.type) {
      case "job_posted":
        status.textContent = `Job posted: ${near(e.bounty)} NEAR bounty in escrow for ${e.amount} request.`;
        line(`<b>${esc(short(e.dao))}</b> posted job · bounty ${near(e.bounty)} N`);
        pay("dao", "job", true);
        break;
      case "bid_committed":
        phase.textContent = "COMMIT";
        line(`<b>${esc(short(e.agent))}</b> sealed bid on ${e.section} · ${e.digest.slice(0, 10)}…`);
        mark(e.agent, (c) => c.attr("stroke", "#F7F8F8"));
        break;
      case "bid_revealed":
        phase.textContent = "REVEAL";
        line(`<b>${esc(short(e.agent))}</b> revealed ${near(e.price)} N`);
        break;
      case "bid_skipped":
        line(`<b>${esc(short(e.agent))}</b> sat out: ${esc(e.reason)}`, "bad");
        break;
      case "awarded":
      case "reassigned":
        phase.textContent = "WORK";
        line(`<b>${esc(short(e.agent))}</b> ${e.type === "awarded" ? "won" : "took over"} ${e.section} · ${near(e.price)} N`, "won");
        sec(e.section, short(e.agent) + " working…", near(e.price));
        mark(e.agent, (c) => c.attr("stroke", "#3B6CF6").attr("stroke-width", 2));
        break;
      case "inference_paid":
        line(`<b>${esc(short(e.agent))}</b> paid $${e.usd.toFixed(5)} inference${e.metered ? "" : " (est.)"} · ${e.tokens_in + e.tokens_out} tok`);
        pay(e.agent, "nearai");
        break;
      case "reviewed":
        if (e.accepted) {
          line(`reviewer checked ${e.verified} claim${e.verified === 1 ? "" : "s"} from <b>${esc(short(e.agent))}</b> · accepted`);
          sec(e.section, short(e.agent) + " · accepted", null, true);
        } else {
          line(`reviewer flagged ${e.flagged} claim${e.flagged === 1 ? "" : "s"} from <b>${esc(short(e.agent))}</b>: ${esc(e.flags[0] || "")}`, "bad");
          mark(e.agent, (c) => c.attr("stroke", "#E8A33D").attr("stroke-dasharray", "2 2"));
          sec(e.section, short(e.agent) + " · sent back", null, false);
        }
        break;
      case "payout":
        line(`job paid <b>${esc(short(e.agent))}</b> ${near(e.amount)} N → backers`, "won");
        pay("job", e.agent, true);
        mark(e.agent, (c) => c.attr("fill", "#3B6CF6").attr("stroke-dasharray", null));
        break;
      case "refund":
        line(`unspent ${near(e.amount)} N returned to <b>${esc(short(e.to))}</b>`);
        pay("job", "dao", true);
        break;
      case "memo_recorded":
        phase.textContent = "RECORDED";
        line(`memo hash recorded · ${e.memo_hash.slice(0, 16)}…`, "won");
        status.textContent = `Memo: ${e.verdict} · ${e.score}/100. Hash recorded against ${short(e.dao)}#${e.proposal_id}.`;
        document.getElementById("verdict").textContent = `${e.verdict} · ${e.score}/100`;
        document.getElementById("hash").textContent = e.memo_hash;
        document.getElementById("memo-cta").hidden = false;
        break;
    }
  }
  function finish() {
    ended = true;
  }
  // Replay: fetch the whole run once (/api/events) and pace it here with the stream's timing.
  // Used where long responses are cut (serverless hosts), and when a stream drops mid-run.
  const pace = (t) => (reduce ? 0 : { bid_committed: 120, bid_revealed: 120 }[t] || 450);
  function replay(from) {
    fetch(`/api/events?dao=${encodeURIComponent(A.dao)}&id=${A.id}`)
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((d) => {
        const rest = d.events.slice(from);
        let i = 0;
        (function next() {
          if (i >= rest.length) return finish();
          const e = rest[i++];
          handle(e);
          setTimeout(next, pace(e.type));
        })();
      })
      .catch(() => {
        status.classList.add("err");
        status.textContent = "Could not load the run. Reload to try again, or open the memo directly.";
        document.getElementById("memo-cta").hidden = false;
      });
  }
  if (A.transport === "replay" || !window.EventSource) {
    replay(0);
  } else {
    const src = new EventSource(`/events?dao=${encodeURIComponent(A.dao)}&id=${A.id}`);
    src.addEventListener("swarm", (m) => handle(JSON.parse(m.data)));
    src.addEventListener("done", () => { finish(); src.close(); });
    src.onerror = () => {
      if (ended) return;
      src.close();
      replay(seen); // pick up where the stream stopped
    };
  }
})();
