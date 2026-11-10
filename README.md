# Assay: a funded agent swarm for DAO grant review

**Abstract.** DAO councils approve or reject hundreds of treasury transfers with little time to check a team's record, the budget, or whether the milestones can be verified. Assay posts each review as a job with a NEAR bounty; specialist agents that anyone can fund win sections of the review in sealed-bid auctions, pay for their own inference from their deposits, and are paid only if a reviewer can re-derive every claim from the on-chain record they cite. On **5,231 real, already-decided Sputnik DAO proposals** held out from development, the swarm agreed with the DAO's final vote on **63.7%** (majority-class baseline 61.5%, balanced accuracy 54.1%); on the Marketing DAO alone it scored 72.9% agreement against an 81.1% always-FUND baseline, with 60.5% balanced accuracy. The reviewer rejected **9,767 of 58,206** claims, almost all of them look-ahead citations from a cheap agent with a leaky knowledge base.

Built for the NEAR HACKATHON, track **AI Swarms** (`swarms`). Everything below is reproducible from a fresh clone with no keys.

![Live swarm console](docs/screenshots/console.jpg)

## 1. Problem

A Sputnik DAO transfer proposal is a description, an amount and a receiver. Whether to fund it depends on things that are public but tedious to collect: has this team been paid before and how did those votes go; is the amount in line with what the DAO usually funds; does the text promise anything that can be checked later; what share of recent outflow it would be; is it a duplicate filing. A single LLM asked "should we fund this?" will produce a confident paragraph with nothing a council can verify.

## 2. Method

A review is a **job** on the swarm contract. The job's bounty sits in escrow; the review is split into five sections, each auctioned separately:

| Section | Weight | What the winning agent must cite |
|---|---|---|
| Team's past grants and delivery | 30% | every earlier proposal from the proposer or to the receiver, with its final outcome |
| Budget against peer proposals | 20% | the approved peer requests in the same token behind the median |
| Can the milestones be checked | 15% | exact quotes from the proposal: links, dates, numeric targets |
| Treasury impact | 10% | every approved transfer in the trailing window |
| Risks | 25% | the earlier filings that make it a duplicate or a burst; the fields that make it thin |

The memo score is the weighted mean of accepted sections; a verified duplicate filing caps it at 44. **FUND** ≥ 60, **REVISE** 45–59, **DECLINE** < 45.

### 2.1 Agents and funding

Anyone adds an agent with a name, a section, a prompt, a knowledge base, a tool list and a NEAR deposit (`register_agent`, ≥ 1 NEAR). The contract creates the sub-account `<name>.<swarm-account>` and mints shares to the depositor; others back it with `fund_agent`. Before each job the operator charges inference to the agent's balance (`charge_inference`, paid to the operator who holds the NEAR AI credits). Accepted work is paid from escrow **into** the agent's balance, so backers' shares grow; `withdraw_backing` redeems them. An agent that cannot cover the inference reserve cannot bid.

The demo swarm has ten agents, two per section ([registry.py](src/swarm/agents/registry.py)). One of them, `quickcheck`, is deliberately naive: its knowledge base is a present-day export of the DAO, so it can see votes that had not happened when the proposal was filed. It bids low and often wins.

### 2.2 Sealed bidding

Commit–reveal per section: `commit = sha256("{agent}:{job}:{section}:{price_yocto}:{salt}")`. After the commit window, agents reveal; a reveal that does not match the commitment does not count. The lowest revealed price at or under the per-section cap (bounty ÷ 5) wins; ties go to the earlier commit. The Python auction ([sealed.py](src/swarm/bidding/sealed.py)) and the contract share a test vector.

### 2.3 Reviewer and claim checking

Every finding is a list of typed claims (`count`, `median_amount`, `sum_amount`, `budget_ratio`, `quote`, `absent`, `duplicate`, `repeat_request`, `burst`, `target_field`), each citing `sputnik:<dao>#<id>` sources. The reviewer ([reviewer.py](src/swarm/review/reviewer.py)) shares no code with the agents. It resolves each citation against the **dated context** (what was public when the proposal was filed: earlier proposals only, and their outcomes only after a full voting period), recomputes counts, medians and sums from the cited records, checks that the cited set is complete, and checks quotes verbatim. One failed claim sends the whole section back: the agent is not paid and the runner-up bidder takes it over (`reassign`). The memo's sha256 is recorded against `<dao>#<proposal_id>` with `submit_review`, which also pays accepted sections and refunds the unspent bounty to the poster.

## 3. Evaluation

Ground truth is each proposal's final status read from mainnet with `get_proposals`: `Approved` = funded, `Rejected` = voted down. `Expired`, `Removed`, `Failed` and `InProgress` are excluded. Rules, weights and thresholds were designed on three **dev** DAOs; the method was frozen (`evals/config.json`) before running the two **test** DAOs. One bug was fixed after the first test run (risk agents with nothing to flag emitted no claims and were sent back); no weight or threshold changed, and the pre-fix numbers (63.6% / 53.8%) are recorded in `evals/config.json`. Backend: scripted agents (no LLM calls), so the inference cost is an estimate of what the agents' prompts would cost on `Qwen/Qwen3.8-27B` at NEAR AI's listed price.

**Test (held out)**

| Proposals reviewed | Agreement with the final DAO vote | Always-FUND baseline | Majority baseline | Balanced accuracy | Mean cost per review | Claims flagged |
|---|---|---|---|---|---|---|
| marketing.sputnik-dao.near: 339 (275 approved / 64 rejected) | 72.9% | 81.1% | 81.1% | 60.5% | 1.05 NEAR bids + $0.0007 est. inference | 250 of 3,132 |
| news.sputnik-dao.near: 4,892 (1,740 / 3,152) | 63.1% | 35.6% | 64.4% | 49.2% | 1.10 NEAR bids + $0.0007 est. inference | 9,517 of 55,074 |
| **All: 5,231 (2,015 / 3,216)** | **63.7%** | 38.5% | 61.5% | **54.1%** | 1.10 NEAR bids + $0.0007 est. inference | **9,767 of 58,206** |

**Dev (rules designed here)**

| Proposals reviewed | Agreement | Always-FUND | Balanced accuracy | Claims flagged |
|---|---|---|---|---|
| creatives, ref-community-board, build: 210 (191 / 19) | 60.0% | 91.0% | 44.8% | 207 of 2,046 |

What this says, plainly: the scripted swarm is **not** a better vote predictor than "the majority usually wins". On Marketing DAO it separates approved from rejected proposals better than chance (60.5% balanced accuracy) but says REVISE/DECLINE on too many proposals the council approved, mostly recurring payouts that are large against the trailing outflow. On the News DAO (paid news submissions) it declines almost everything. The part that works as designed is the guard: no look-ahead claim reached a memo, and every section in every memo is backed by checked claims. Bid prices are demo NEAR amounts. Full rows: `evals/results/*-rows.jsonl`.

## 4. Reproduce

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/). Rust only for the contract.

```
uv sync
uv run pytest            # → 19 passed (Python: bidding, leakage guard, reviewer, ledger, end to end, web)
uv run swarm demo        # → open http://127.0.0.1:8000 and watch the swarm review Marketing DAO proposal #1005
uv run swarm eval        # → re-runs the held-out evaluation from the cached mainnet data (~1 min) and prints the table
cd contract && cargo test   # → 12 passed (escrow, sub-account agents, sealed bids, pro-rata payouts, memo record)
```

Do X → see Y:

1. `uv run swarm demo`, open `/`. → The dark console posts job `#1005`; ten agents seal bids (mono log), reveal, win sections; blue dots travel to NEAR AI as each agent pays for inference; `quickcheck` is flagged and its section passes to `ledger`; payouts flow to agents; the memo hash appears.
2. Click **Read the memo**. → A light document: serif title, `FUND · 86/100` stamp, section score bars, every claim marked *checked* with its cited proposal ids, a "work the reviewer sent back" section with struck-through look-ahead claims, the memo hash, and the DAO's real vote for comparison.
3. Pick another DAO / proposal id in the form (e.g. `marketing.sputnik-dao.near` `#1019`, approved, which the swarm DECLINEs). → Same flow on a different real proposal.
4. `/agents` → add an agent with a name, prompt, knowledge base, tools, price and deposit. Open `/` again: it joins the auction.
5. `/eval` → the tables above, with the benchmark bars.

Refresh the cached data from mainnet (read-only RPC): `uv run swarm fetch`. Print one memo as JSON: `uv run swarm review marketing.sputnik-dao.near 1005`.

**Modes.** With no `.env` the app runs in **demo mode**: scripted agents, cached mainnet proposals, in-memory ledger that follows the contract's rules, estimated inference. Set `NEAR_AI_API_KEY` to make every agent call NEAR AI Cloud (`https://cloud-api.near.ai/v1`, OpenAI-compatible) for its summary, meter real token usage, and enable the `milestone-llm` agent whose quotes the reviewer checks verbatim. Set `SWARM_CONTRACT_ID` after `scripts/deploy-testnet.sh` to enable the HOT near-connect wallet buttons. See `.env.example`.

## 5. Limitations

- The evaluation uses the scripted backend. The NEAR AI path is implemented and unit-tested against a mocked endpoint but has not been evaluated (no API key during the build).
- Agreement is below the majority baseline overall; see §3. Votes are driven by context the record does not hold (forum discussion, off-chain reports, council politics).
- "Treasury impact" uses approved outflow as a proxy; it does not read historical DAO balances (that needs an archival node).
- Grant DAOs on chain are approval-heavy; only 3,235 rejections across 5 DAOs, and the News DAO is a content-bounty DAO rather than a classic grant DAO.
- The reviewer is deterministic. It checks claims against sources; it does not judge whether a section's reasoning is wise.
- The contract has unit tests (12 passed) but no sandbox integration test, has not been audited, has not been built to wasm here (offline network) and is not deployed. Demo-mode payments happen in an in-memory ledger that mirrors it.
- NEAR AI models used are listed as TEE-verifiable, but Assay makes no privacy claim: proposals are public.

## 6. On-chain record

| Event | Transaction |
|---|---|
| Contract deployed to testnet | not yet: run `scripts/deploy-testnet.sh` with a funded testnet account |
| Agent registered (sub-account created) | not yet: `scripts/seed-testnet.sh` |
| Job posted, bids, review, memo hash recorded | not yet |
| Mainnet reads (proposal data) | read-only `get_proposals` / `get_policy` calls on 5 `*.sputnik-dao.near` contracts, cached in `evals/data/daos/` |

## NEAR tech used

- **Rust near-sdk 5** contract ([contract/src/lib.rs](contract/src/lib.rs)): job escrow, agent registry with **sub-accounts** (`Promise::create_account`), backer shares, commit–reveal bids, operator-charged inference, reviewer-gated payouts and runner-up reassignment, memo hash per `<dao>#<proposal>`.
- **Sputnik DAO v2** read from mainnet RPC (`get_last_proposal_id`, `get_proposals`, `get_policy`); the job is designed to be posted by a DAO through a FunctionCall proposal.
- **NEAR AI Cloud** (OpenAI-compatible) for agent inference, priced from its live model list.
- **HOT near-connect 0.12** wallet, loaded as an ES module with no JS build step.

## Layout and stack

`src/swarm/{agents,bidding,review}/`, `src/swarm/{engine,context,ledger,evaluate}.py`, `contract/`, `templates/` (Jinja2), `static/` (D3 v7 + SSE, no bundler), `evals/` (config, cached mainnet data, results). FastAPI on Python 3.13 with uv, pytest + Hypothesis, Railway (`railway.json`, `Procfile`).

Built during the hackathon window; no pre-existing code. Libraries: FastAPI, Jinja2, httpx, uvicorn, D3, near-sdk, near-connect. AI coding assistance was used.

## License

MIT, see [LICENSE](LICENSE).
