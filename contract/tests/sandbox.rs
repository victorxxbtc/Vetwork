//! End-to-end on a local NEAR sandbox (near-workspaces): the real wasm, real accounts,
//! real balances. Covers escrow, sub-account agents, sealed bids, payouts and refunds.
//!
//! Needs the wasm (`cargo near build non-reproducible-wasm`) and a sandbox binary
//! (`NEAR_SANDBOX_BIN_PATH=/path/to/near-sandbox`, or let near-workspaces download one).
//! Skips with a message when `VETWORK_SKIP_SANDBOX=1`.

mod common;
use common::*;
use near_workspaces::types::NearToken;
use serde_json::{json, Value};

#[tokio::test]
async fn full_job_escrow_bids_review_payouts_and_refund() -> anyhow::Result<()> {
    if skip() {
        return Ok(());
    }
    let w = world().await?;

    // Agents get real sub-accounts of the swarm contract.
    let ledger = register(&w, "ledger", "history", 2).await?;
    let quick = register(&w, "quickcheck", "history", 2).await?;
    let comps = register(&w, "comparables", "budget", 2).await?;
    assert_eq!(ledger, format!("ledger.{}", w.c.id()));
    assert!(w.worker.view_account(&ledger.parse()?).await.is_ok(), "sub-account exists on chain");

    // DAO posts a 2 NEAR job: the bounty sits in escrow.
    let poster_before = w.dao.view_account().await?.balance.as_yoctonear();
    let job_id: u64 = w
        .dao
        .call(w.c.id(), "post_job")
        .args_json(json!({"dao": "marketing.sputnik-dao.near", "proposal_id": 1005, "description": "review",
                          "commit_secs": 3, "reveal_secs": 30}))
        .deposit(NearToken::from_near(2))
        .transact()
        .await?
        .into_result()?
        .json()?;
    let j = job(&w, job_id).await?;
    assert_eq!(yocto(&j["escrow"]), 2 * N);

    // Sealed bids: the operator commits for each agent; digests reveal nothing.
    let bids = [(&ledger, "history", N / 4, "s1"), (&quick, "history", N / 10, "s2"), (&comps, "budget", N / 5, "s3")];
    for (agent, section, price, salt) in bids {
        w.operator
            .call(w.c.id(), "commit_bid")
            .args_json(json!({"job_id": job_id, "section": section, "agent": agent,
                              "digest": commitment(agent, job_id, section, price, salt)}))
            .transact()
            .await?
            .into_result()?;
    }
    // A bid after the commit window is refused.
    wait_until(&w, j["commit_ends"].as_u64().unwrap()).await?;
    let late = w
        .operator
        .call(w.c.id(), "commit_bid")
        .args_json(json!({"job_id": job_id, "section": "budget", "agent": comps, "digest": "0".repeat(64)}))
        .transact()
        .await?;
    assert!(late.is_failure(), "late commit must fail");

    // Reveal: a wrong salt does not count; the right one does.
    let bad: bool = w
        .operator
        .call(w.c.id(), "reveal_bid")
        .args_json(json!({"job_id": job_id, "section": "history", "agent": quick, "price": (N / 10).to_string(), "salt": "wrong"}))
        .transact()
        .await?
        .into_result()?
        .json()?;
    assert!(!bad);
    for (agent, section, price, salt) in bids {
        let ok: bool = w
            .operator
            .call(w.c.id(), "reveal_bid")
            .args_json(json!({"job_id": job_id, "section": section, "agent": agent, "price": price.to_string(), "salt": salt}))
            .transact()
            .await?
            .into_result()?
            .json()?;
        assert!(ok, "{agent} reveal");
    }

    // Closing before the reveal window ends is refused.
    assert!(w.c.call("close_auction").args_json(json!({"job_id": job_id})).transact().await?.is_failure());
    wait_until(&w, j["reveal_ends"].as_u64().unwrap()).await?;
    w.dao.call(w.c.id(), "close_auction").args_json(json!({"job_id": job_id})).transact().await?.into_result()?;
    let j = job(&w, job_id).await?;
    assert_eq!(j["sections"][0]["ranked"][0][0], json!(quick), "lowest price wins history");

    // Inference is paid from the agent's own balance to the operator.
    w.operator
        .call(w.c.id(), "charge_inference")
        .args_json(json!({"agent": quick, "amount": (N / 100).to_string(), "job_id": job_id}))
        .transact()
        .await?
        .into_result()?;

    // Only the reviewer may reassign or submit.
    let rogue = w.dao.call(w.c.id(), "reassign").args_json(json!({"job_id": job_id, "section": "history"})).transact().await?;
    assert!(rogue.is_failure());
    // Reviewer rejects quickcheck's work: history goes to the runner-up.
    let next: Option<String> = w
        .reviewer
        .call(w.c.id(), "reassign")
        .args_json(json!({"job_id": job_id, "section": "history"}))
        .transact()
        .await?
        .into_result()?
        .json()?;
    assert_eq!(next.as_deref(), Some(ledger.as_str()));

    let before = |a: Value| yocto(&a["balance"]);
    let ledger_before = before(w.c.view("get_agent").args_json(json!({"agent": ledger})).await?.json()?);
    w.reviewer
        .call(w.c.id(), "submit_review")
        .args_json(json!({"job_id": job_id, "memo_hash": "b".repeat(64),
                          "accepted": [true, true, false, false, false], "score": 72, "verdict": "FUND"}))
        .max_gas()
        .transact()
        .await?
        .into_result()?;

    // Payouts land in the agents' balances (backers' shares grow); the rest is refunded.
    let ledger_after = before(w.c.view("get_agent").args_json(json!({"agent": ledger})).await?.json()?);
    assert_eq!(ledger_after - ledger_before, N / 4);
    let j = job(&w, job_id).await?;
    assert_eq!(yocto(&j["escrow"]), 0);
    assert_eq!(j["status"], json!("Reviewed"));
    let poster_after = w.dao.view_account().await?.balance.as_yoctonear();
    let refund = 2 * N - N / 4 - N / 5;
    // poster spent 2 NEAR + gas, got the refund back: net within 0.05 NEAR of the paid sections
    let spent = poster_before - poster_after;
    assert!(spent >= N / 4 + N / 5 && spent < N / 4 + N / 5 + N / 20, "spent {spent}, refund {refund}");

    let memo: Value = w.c.view("get_memo").args_json(json!({"dao": "marketing.sputnik-dao.near", "proposal_id": 1005})).await?.json()?;
    assert_eq!(memo["verdict"], json!("FUND"));
    assert_eq!(memo["score"], json!(72));
    Ok(())
}

#[tokio::test]
async fn backers_withdraw_earnings_pro_rata_in_real_near() -> anyhow::Result<()> {
    if skip() {
        return Ok(());
    }
    let w = world().await?;
    let agent = register(&w, "ledger", "history", 3).await?;
    // a second backer funds the same agent
    let b2 = w.worker.root_account()?.create_subaccount("backer2").initial_balance(NearToken::from_near(20)).transact().await?.into_result()?;
    b2.call(w.c.id(), "fund_agent").args_json(json!({"agent": agent})).deposit(NearToken::from_near(5)).transact().await?.into_result()?;
    let shares: Value = w.c.view("get_shares").args_json(json!({"agent": agent, "backer": b2.id()})).await?.json()?;
    let before = b2.view_account().await?.balance.as_yoctonear();
    let out: Value = b2
        .call(w.c.id(), "withdraw_backing")
        .args_json(json!({"agent": agent, "shares": shares}))
        .transact()
        .await?
        .into_result()?
        .json()?;
    assert_eq!(yocto(&out), 5 * N);
    let after = b2.view_account().await?.balance.as_yoctonear();
    assert!(after > before + 5 * N - N / 100, "withdrawn NEAR arrived");
    // over-withdrawing fails
    let again = b2.call(w.c.id(), "withdraw_backing").args_json(json!({"agent": agent, "shares": shares})).transact().await?;
    assert!(again.is_failure());
    Ok(())
}
