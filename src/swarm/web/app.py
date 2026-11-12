"""FastAPI app: live swarm console (SSE), memo documents, evaluation, agent registry."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..agents.analysts import fmt_amount
from ..agents.claims import SECTION_TITLES, SECTIONS
from ..agents.llm import client_from_env
from ..agents.registry import KNOWLEDGE_BASES, TOOLS, AgentSpec, validate
from ..engine import Swarm, run_job
from ..evaluate import RESULTS, load_config, load_decimals, load_snapshot
from ..ledger import YOCTO, LedgerError, near
from ..review.memo import title_of

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DAO = "marketing.sputnik-dao.near"
DEFAULT_PROPOSAL = 1005

app = FastAPI(title="Vetwork")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
STATE: dict[str, Any] = {"swarm": Swarm.seeded()}


def nears(y: int | str) -> str:
    v = int(y) / YOCTO
    return f"{v:.2f}" if v >= 0.01 or v == 0 else f"{v:.6f}"


templates.env.filters["nears"] = nears
templates.env.globals.update(
    contract_id=os.environ.get("SWARM_CONTRACT_ID", ""),
    network=os.environ.get("NEAR_NETWORK", "testnet"),
    mode=lambda: "live NEAR AI" if client_from_env() else "demo",
    # cache-buster for /static: changes whenever a static file changes
    asset_v=hashlib.sha256(b"".join(f.read_bytes() for f in sorted((ROOT / "static").iterdir()) if f.is_file())).hexdigest()[:10],
)


@lru_cache(maxsize=8)
def snapshot(dao: str) -> dict[str, Any]:
    cfg = load_config()
    if dao not in sum(cfg["splits"].values(), []):
        raise HTTPException(404, "DAO not in the cached dataset")
    return load_snapshot(dao)


def job_id_for(dao: str, pid: int) -> int:
    return int(hashlib.sha256(f"{dao}#{pid}".encode()).hexdigest()[:6], 16)


def run(dao: str, pid: int):
    snap = snapshot(dao)
    if pid not in {p["id"] for p in snap["proposals"]}:
        raise HTTPException(404, "no such transfer proposal in the cache")
    swarm = copy.deepcopy(STATE["swarm"])
    return run_job(swarm, snap, pid, load_decimals(), job_id=job_id_for(dao, pid), llm=client_from_env())


def outcome(dao: str, pid: int) -> str:
    return next(p["status"] for p in snapshot(dao)["proposals"] if p["id"] == pid)


@app.get("/", response_class=HTMLResponse)
def console(request: Request, dao: str = DEFAULT_DAO, id: int = DEFAULT_PROPOSAL):
    snap = snapshot(dao)
    p = next((p for p in snap["proposals"] if p["id"] == id), None)
    if p is None:
        raise HTTPException(404, "no such transfer proposal in the cache")
    swarm: Swarm = STATE["swarm"]
    agents = [{"id": a.account(swarm.account), "name": a.name, "section": a.section} for a in swarm.agents]
    recent = [q for q in snap["proposals"] if q["status"] in ("Approved", "Rejected")][-12:][::-1]
    dec = load_decimals()
    return templates.TemplateResponse(
        request,
        "console.html",
        {
            "dao": dao, "p": p, "title": title_of(p["description"]),
            "amount": fmt_amount(p["amount"], dec.get(p["token_id"], -1), p["token_id"]),
            "agents": agents, "sections": SECTIONS, "titles": SECTION_TITLES, "job_id": job_id_for(dao, id),
            "recent": [{"id": q["id"], "title": title_of(q["description"]), "status": q["status"]} for q in recent],
            "daos": sum(load_config()["splits"].values(), []),
        },
    )


@app.get("/events")
async def events(dao: str = DEFAULT_DAO, id: int = DEFAULT_PROPOSAL, speed: float = 1.0):
    res = run(dao, id)

    async def gen():
        for e in res.events:
            yield f"event: swarm\ndata: {json.dumps(e)}\n\n"
            if speed > 0:
                await asyncio.sleep({"bid_committed": 0.12, "bid_revealed": 0.12}.get(e["type"], 0.45) / speed)
        yield f"event: done\ndata: {json.dumps({'memo_url': f'/memo/{dao}/{id}'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/memo/{dao}/{pid}", response_class=HTMLResponse)
def memo_page(request: Request, dao: str, pid: int):
    res = run(dao, pid)
    return templates.TemplateResponse(
        request, "memo.html", {"m": res.memo, "events": res.events, "outcome": outcome(dao, pid), "backend": res.backend}
    )


@app.get("/api/memo/{dao}/{pid}")
def memo_json(dao: str, pid: int):
    return JSONResponse(run(dao, pid).memo)


@app.get("/eval", response_class=HTMLResponse)
def eval_page(request: Request):
    data = {}
    for split in ("prereg", "test", "dev"):
        f = RESULTS / f"{split}.json"
        if f.exists():
            data[split] = json.loads(f.read_text())
    return templates.TemplateResponse(request, "eval.html", {"data": data, "cfg": load_config()})


@app.get("/break", response_class=HTMLResponse)
def break_page(request: Request):
    f = ROOT / "docs" / "attacks.json"
    data = json.loads(f.read_text()) if f.exists() else None
    return templates.TemplateResponse(request, "break.html", {"data": data})


@app.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request, error: str = "", added: str = ""):
    swarm: Swarm = STATE["swarm"]
    rows = []
    for a in swarm.agents:
        acct = a.account(swarm.account)
        b = swarm.ledger.agents[acct]
        rows.append({"spec": a, "account": acct, "balance": b.balance, "backers": len(b.shares), "earned": b.earned})
    return templates.TemplateResponse(
        request, "agents.html",
        {"rows": rows, "sections": SECTIONS, "titles": SECTION_TITLES, "kbs": KNOWLEDGE_BASES, "tools": TOOLS,
         "error": error, "added": added, "swarm_account": swarm.account},
    )


@app.post("/agents")
async def add_agent(request: Request):
    # urlencoded form parsed by hand: no multipart dependency needed
    form = parse_qs((await request.body()).decode(), keep_blank_values=True)
    get = lambda k, d="": (form.get(k) or [d])[0]  # noqa: E731
    name, section, prompt = get("name"), get("section"), get("prompt")
    knowledge_base, tools, backer = get("knowledge_base", "dated-context"), form.get("tools", []), get("backer", "you.testnet")
    try:
        price, deposit = float(get("price", "0.25")), float(get("deposit", "1"))
    except ValueError:
        return RedirectResponse("/agents?error=price and deposit must be numbers", status_code=303)
    spec = AgentSpec(name=name.strip().lower(), title=name.strip(), section=section, prompt=prompt,
                     knowledge_base=knowledge_base, tools=tools, base_price_near=price, creator=backer)
    errs = validate(spec)
    if deposit < 1:
        errs.append("deposit must be at least 1 NEAR")
    if not errs:
        try:
            STATE["swarm"].add_agent(spec, backer, near(deposit))
        except LedgerError as e:
            errs.append(str(e))
    if errs:
        return RedirectResponse(f"/agents?error={quote('; '.join(errs))}", status_code=303)
    return RedirectResponse(f"/agents?added={spec.name}", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": "nearai" if client_from_env() else "demo"}
