//! Reputation is recomputable from chain history: collect every EVENT_JSON log the
//! contract emits during two real jobs (one with a rejection and an upheld challenge),
//! recompute each agent's reputation from the log alone, and compare with the on-chain view.
//! Writes target/events.jsonl and target/reputation.json (the Python recompute is tested on them).

mod common;
use common::*;
use near_workspaces::result::ExecutionFinalResult;
use near_workspaces::types::NearToken;
use serde_json::{json, Value};
use std::collections::BTreeMap;

fn logs(r: ExecutionFinalResult, out: &mut Vec<Value>) -> anyhow::Result<ExecutionFinalResult> {
    for l in r.logs() {
        if let Some(j) = l.strip_prefix("EVENT_JSON:") {
            out.push(serde_json::from_str(j)?);
        }
    }
    Ok(r)
}

async fn job(w: &World, ev: &mut Vec<Value>, pid: u64, bids: &[(&str, &str, u128, &str)], reject_first: bool) -> anyhow::Result<u64> {
    let r = w.dao.call(w.c.id(), "post_job")
        .args_json(json!({"dao": "marketing.sputnik-dao.near", "proposal_id": pid, "description": "r", "commit_secs": 4, "reveal_secs": 30}))
        .deposit(NearToken::from_near(2)).transact().await?;
    let id: u64 = logs(r, ev)?.json()?;
    for (a, s, p, salt) in bids {
        let r = w.operator.call(w.c.id(), "commit_bid")
            .args_json(json!({"job_id": id, "section": s, "agent": a, "digest": commitment(a, id, s, *p, salt)})).transact().await?;
        logs(r, ev)?.into_result()?;
    }
    let j: Value = w.c.view("get_job").args_json(json!({"job_id": id})).await?.json()?;
    wait_until(w, j["commit_ends"].as_u64().unwrap()).await?;
    for (a, s, p, salt) in bids {
        let r = w.operator.call(w.c.id(), "reveal_bid")
            .args_json(json!({"job_id": id, "section": s, "agent": a, "price": p.to_string(), "salt": salt})).transact().await?;
        logs(r, ev)?.into_result()?;
    }
    wait_until(w, j["reveal_ends"].as_u64().unwrap()).await?;
    logs(w.dao.call(w.c.id(), "close_auction").args_json(json!({"job_id": id})).transact().await?, ev)?.into_result()?;
    if reject_first {
        let r = w.reviewer.call(w.c.id(), "reassign").args_json(json!({"job_id": id, "section": "history"})).transact().await?;
        logs(r, ev)?.into_result()?;
    }
    let r = w.reviewer.call(w.c.id(), "submit_review")
        .args_json(json!({"job_id": id, "memo_hash": "b".repeat(64), "accepted": [true, false, false, false, false], "score": 70, "verdict": "FUND"}))
        .max_gas().transact().await?;
    logs(r, ev)?.into_result()?;
    Ok(id)
}

#[tokio::test]
async fn reputation_recomputes_from_event_log() -> anyhow::Result<()> {
    if skip() {
        return Ok(());
    }
    let w = world().await?;
    let mut ev = vec![];
    let ledger = register(&w, "ledger", "history", 2).await?;
    let quick = register(&w, "quickcheck", "history", 2).await?;
    let bids = [(ledger.as_str(), "history", N / 4, "a"), (quick.as_str(), "history", N / 10, "b")];

    // job 1: quickcheck wins, its work is rejected, ledger is paid; then ledger's work is challenged and slashed
    let j1 = job(&w, &mut ev, 1, &bids, true).await?;
    let r = w.backer.call(w.c.id(), "challenge")
        .args_json(json!({"job_id": j1, "section": "history", "claim_index": 0, "reason": "count contradicts cited proposals"}))
        .deposit(NearToken::from_yoctonear(N / 10)).transact().await?;
    logs(r, &mut ev)?.into_result()?;
    let r = w.reviewer.call(w.c.id(), "resolve_challenge").args_json(json!({"job_id": j1, "section": "history", "upheld": true})).transact().await?;
    logs(r, &mut ev)?.into_result()?;
    // job 2: quickcheck wins and is paid
    job(&w, &mut ev, 2, &bids, false).await?;

    // recompute from the log alone
    let mut counts: BTreeMap<String, (u32, u32, u32)> = BTreeMap::new();
    for e in &ev {
        let Some(a) = e["agent"].as_str() else { continue };
        let c = counts.entry(a.to_string()).or_default();
        match e["event"].as_str() {
            Some("payout") => c.0 += 1,
            Some("section_rejected") => c.1 += 1,
            Some("slashed") => c.2 += 1,
            _ => {}
        }
    }
    let mut views = serde_json::Map::new();
    for a in [&ledger, &quick] {
        let v: Value = w.c.view("get_reputation").args_json(json!({"agent": a})).await?.json()?;
        let (acc, rej, sl) = counts[a.as_str()];
        assert_eq!((v["accepted"].as_u64(), v["rejected"].as_u64(), v["slashed"].as_u64()), (Some(acc as u64), Some(rej as u64), Some(sl as u64)), "{a}");
        let bp = (acc as u64 + 1) * 10_000 / (acc as u64 + rej as u64 + 3 * sl as u64 + 2);
        assert_eq!(v["score_bp"].as_u64(), Some(bp));
        views.insert(a.clone(), v);
    }
    assert_eq!(counts[ledger.as_str()], (1, 0, 1));
    assert_eq!(counts[quick.as_str()], (1, 1, 0));
    std::fs::create_dir_all("target")?;
    std::fs::write("target/events.jsonl", ev.iter().map(|e| e.to_string()).collect::<Vec<_>>().join("\n") + "\n")?;
    std::fs::write("target/reputation.json", serde_json::to_string_pretty(&Value::Object(views))?)?;
    println!("{} events; reputation recomputed from the log matches get_reputation for both agents", ev.len());
    Ok(())
}
