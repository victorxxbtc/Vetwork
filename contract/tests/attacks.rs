//! "Try to break it": real attacks against the compiled contract on a local sandbox.
//! Every attack is a real transaction from a real account; each must be refused.
//! Writes `target/attacks.json` (the web app's /break page shows it) and prints "N of N refused".

mod common;
use common::*;
use near_workspaces::result::ExecutionFinalResult;
use near_workspaces::types::NearToken;
use serde_json::{json, Value};

struct Log(Vec<Value>);

impl Log {
    /// The transaction must fail; record the contract's panic message.
    fn refused(&mut self, name: &str, how: &str, r: ExecutionFinalResult) {
        let ok = r.is_failure();
        let msg = if ok { format!("{:?}", r.into_result().err().unwrap()) } else { "ACCEPTED".into() };
        let msg = msg.split("Smart contract panicked: ").nth(1).map(|m| m.split('"').next().unwrap_or(m).trim_end_matches('\\').to_string()).unwrap_or(msg);
        println!("{} {name}: {msg}", if ok { "refused " } else { "ACCEPTED" });
        self.0.push(json!({"attack": name, "how": how, "refused": ok, "answer": msg}));
    }
    fn refused_value(&mut self, name: &str, how: &str, refused: bool, answer: &str) {
        println!("{} {name}: {answer}", if refused { "refused " } else { "ACCEPTED" });
        self.0.push(json!({"attack": name, "how": how, "refused": refused, "answer": answer}));
    }
}

#[tokio::test]
async fn try_to_break_it() -> anyhow::Result<()> {
    if skip() {
        return Ok(());
    }
    let w = world().await?;
    let root = w.worker.root_account()?;
    let mallory = root.create_subaccount("mallory").initial_balance(NearToken::from_near(20)).transact().await?.into_result()?;
    let mut log = Log(vec![]);

    let ledger = register(&w, "ledger", "history", 2).await?;
    let quick = register(&w, "quickcheck", "history", 2).await?;
    let comps = register(&w, "comparables", "budget", 2).await?;
    let broke = register(&w, "broke", "budget", 1).await?;

    // 1. duplicate agent name: would hijack an existing agent's sub-account and backers
    let r = w.backer.call(w.c.id(), "register_agent")
        .args_json(json!({"name": "ledger", "section": "history", "prompt_hash": "a".repeat(64), "knowledge_base": "x", "tools": []}))
        .deposit(NearToken::from_near(2)).max_gas().transact().await?;
    log.refused("Take over an existing agent", "register_agent with a name already in use", r);

    // 2. bounty below the minimum
    let r = w.dao.call(w.c.id(), "post_job")
        .args_json(json!({"dao": "marketing.sputnik-dao.near", "proposal_id": 1, "description": "x", "commit_secs": 3, "reveal_secs": 30}))
        .deposit(NearToken::from_millinear(10)).transact().await?;
    log.refused("Post a job with a dust bounty", "post_job with 0.01 NEAR", r);

    let job_id: u64 = w.dao.call(w.c.id(), "post_job")
        .args_json(json!({"dao": "marketing.sputnik-dao.near", "proposal_id": 1005, "description": "review", "commit_secs": 4, "reveal_secs": 30}))
        .deposit(NearToken::from_near(2)).transact().await?.into_result()?.json()?;
    let bids = [(&ledger, "history", N / 4, "s1"), (&quick, "history", N / 10, "s2"), (&comps, "budget", N / 5, "s3")];
    for (agent, section, price, salt) in bids {
        w.operator.call(w.c.id(), "commit_bid")
            .args_json(json!({"job_id": job_id, "section": section, "agent": agent, "digest": commitment(agent, job_id, section, price, salt)}))
            .transact().await?.into_result()?;
    }

    // 3. a stranger bids in an agent's name
    let r = mallory.call(w.c.id(), "commit_bid")
        .args_json(json!({"job_id": job_id, "section": "history", "agent": ledger, "digest": "1".repeat(64)}))
        .transact().await?;
    log.refused("Bid in someone else's name", "mallory calls commit_bid for ledger", r);

    // 4. an agent with no money left bids anyway (it could not pay for its own inference)
    w.operator.call(w.c.id(), "charge_inference")
        .args_json(json!({"agent": broke, "amount": (N * 945 / 1000).to_string(), "job_id": job_id}))
        .transact().await?.into_result()?;
    let r = w.operator.call(w.c.id(), "commit_bid")
        .args_json(json!({"job_id": job_id, "section": "budget", "agent": broke, "digest": commitment(&broke, job_id, "budget", 1, "s")}))
        .transact().await?;
    log.refused("Unfunded agent bids", "agent balance drained below the 0.01 NEAR inference reserve, then commit_bid", r);

    // 5. peek: reveal while others are still committing
    let r = w.operator.call(w.c.id(), "reveal_bid")
        .args_json(json!({"job_id": job_id, "section": "history", "agent": quick, "price": (N / 10).to_string(), "salt": "s2"}))
        .transact().await?;
    log.refused("Reveal early to signal a price", "reveal_bid during the commit window", r);

    // 6. a stranger charges an agent for "inference" and pockets it
    let r = mallory.call(w.c.id(), "charge_inference")
        .args_json(json!({"agent": ledger, "amount": N.to_string(), "job_id": job_id})).transact().await?;
    log.refused("Drain an agent through fake inference bills", "mallory calls charge_inference", r);

    let j: Value = w.c.view("get_job").args_json(json!({"job_id": job_id})).await?.json()?;
    wait_until(&w, j["commit_ends"].as_u64().unwrap()).await?;

    // 7. bid after seeing the window close
    let r = w.operator.call(w.c.id(), "commit_bid")
        .args_json(json!({"job_id": job_id, "section": "budget", "agent": comps, "digest": "2".repeat(64)}))
        .transact().await?;
    log.refused("Late bid", "commit_bid after the commit window", r);

    // 8. reveal a different (lower) price than the one committed
    let changed: bool = w.operator.call(w.c.id(), "reveal_bid")
        .args_json(json!({"job_id": job_id, "section": "history", "agent": ledger, "price": (N / 100).to_string(), "salt": "s1"}))
        .transact().await?.into_result()?.json()?;
    log.refused_value("Change the price after sealing", "reveal_bid with a lower price than committed", !changed, "reveal does not match the commitment; bid not counted");

    // 9. right price, wrong salt
    let bad: bool = w.operator.call(w.c.id(), "reveal_bid")
        .args_json(json!({"job_id": job_id, "section": "history", "agent": quick, "price": (N / 10).to_string(), "salt": "guess"}))
        .transact().await?.into_result()?.json()?;
    log.refused_value("Reveal with a guessed salt", "reveal_bid with the right price and a wrong salt", !bad, "reveal does not match the commitment; bid not counted");

    for (agent, section, price, salt) in bids {
        w.operator.call(w.c.id(), "reveal_bid")
            .args_json(json!({"job_id": job_id, "section": section, "agent": agent, "price": price.to_string(), "salt": salt}))
            .transact().await?.into_result()?;
    }
    // 10. reveal twice
    let r = w.operator.call(w.c.id(), "reveal_bid")
        .args_json(json!({"job_id": job_id, "section": "history", "agent": ledger, "price": (N / 4).to_string(), "salt": "s1"}))
        .transact().await?;
    log.refused("Reveal twice", "second reveal_bid for the same commitment", r);

    // 11. close the auction before reveals end
    let r = mallory.call(w.c.id(), "close_auction").args_json(json!({"job_id": job_id})).transact().await?;
    log.refused("Close the auction early", "close_auction during the reveal window", r);

    wait_until(&w, j["reveal_ends"].as_u64().unwrap()).await?;
    w.dao.call(w.c.id(), "close_auction").args_json(json!({"job_id": job_id})).transact().await?.into_result()?;

    // 12. a fake reviewer approves everything and pays itself
    let r = mallory.call(w.c.id(), "submit_review")
        .args_json(json!({"job_id": job_id, "memo_hash": "e".repeat(64), "accepted": [true, true, true, true, true], "score": 99, "verdict": "FUND"}))
        .max_gas().transact().await?;
    log.refused("Fake reviewer approves", "mallory calls submit_review", r);

    // 13. a fake reviewer knocks out the winner
    let r = mallory.call(w.c.id(), "reassign").args_json(json!({"job_id": job_id, "section": "history"})).transact().await?;
    log.refused("Fake reviewer rejects a winner", "mallory calls reassign", r);

    w.reviewer.call(w.c.id(), "reassign").args_json(json!({"job_id": job_id, "section": "history"})).transact().await?.into_result()?;
    w.reviewer.call(w.c.id(), "submit_review")
        .args_json(json!({"job_id": job_id, "memo_hash": "b".repeat(64), "accepted": [true, true, false, false, false], "score": 72, "verdict": "FUND"}))
        .max_gas().transact().await?.into_result()?;
    let paid: Value = w.c.view("get_agent").args_json(json!({"agent": ledger})).await?.json()?;

    // 14. double payout
    let r = w.reviewer.call(w.c.id(), "submit_review")
        .args_json(json!({"job_id": job_id, "memo_hash": "b".repeat(64), "accepted": [true, true, false, false, false], "score": 72, "verdict": "FUND"}))
        .max_gas().transact().await?;
    let again: Value = w.c.view("get_agent").args_json(json!({"agent": ledger})).await?.json()?;
    assert_eq!(paid["balance"], again["balance"], "no second payout");
    log.refused("Double payout", "reviewer submits the same review twice", r);

    // 15. backer pulls bonded money out before the challenge window ends
    let shares: Value = w.c.view("get_shares").args_json(json!({"agent": ledger, "backer": w.backer.id()})).await?.json()?;
    let r = w.backer.call(w.c.id(), "withdraw_backing").args_json(json!({"agent": ledger, "shares": shares})).transact().await?;
    log.refused("Run with the bond", "withdraw_backing of all shares while the bond is locked", r);

    // 16. withdraw someone else's shares
    let r = mallory.call(w.c.id(), "withdraw_backing").args_json(json!({"agent": ledger, "shares": "1"})).transact().await?;
    log.refused("Withdraw another backer's money", "mallory calls withdraw_backing with no shares", r);

    // 17. free challenge spam
    let r = mallory.call(w.c.id(), "challenge")
        .args_json(json!({"job_id": job_id, "section": "history", "claim_index": 0, "reason": "spam"})).transact().await?;
    log.refused("Challenge without a deposit", "challenge with no NEAR attached", r);

    // 18. challenger settles their own challenge
    mallory.call(w.c.id(), "challenge")
        .args_json(json!({"job_id": job_id, "section": "history", "claim_index": 0, "reason": "contradicts source"}))
        .deposit(NearToken::from_millinear(100)).transact().await?.into_result()?;
    let r = mallory.call(w.c.id(), "resolve_challenge")
        .args_json(json!({"job_id": job_id, "section": "history", "upheld": true})).transact().await?;
    log.refused("Challenger slashes the agent itself", "mallory calls resolve_challenge on her own challenge", r);

    // 19. release a bond early to dodge a pending challenge
    let r = mallory.call(w.c.id(), "release_bond").args_json(json!({"job_id": job_id, "section": "history"})).transact().await?;
    log.refused("Release a challenged bond", "release_bond while a challenge is open", r);

    // 20. back-date a pre-registration by registering the same hash again later
    w.dao.call(w.c.id(), "preregister").args_json(json!({"hash": "c".repeat(64), "note": "first"})).transact().await?.into_result()?;
    let r = mallory.call(w.c.id(), "preregister").args_json(json!({"hash": "c".repeat(64), "note": "mine"})).transact().await?;
    log.refused("Overwrite a pre-registration", "preregister an existing hash", r);

    let n = log.0.len();
    let refused = log.0.iter().filter(|a| a["refused"] == json!(true)).count();
    println!("\n{refused} of {n} refused");
    std::fs::create_dir_all("target")?;
    std::fs::write(
        "target/attacks.json",
        serde_json::to_string_pretty(&json!({"refused": refused, "total": n, "sandbox": "near-workspaces 0.24, local neard", "attacks": log.0}))?,
    )?;
    assert_eq!(refused, n, "an attack got through");
    Ok(())
}
