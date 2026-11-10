use super::*;
use near_sdk::test_utils::VMContextBuilder;
use near_sdk::testing_env;

const N: u128 = 1_000_000_000_000_000_000_000_000;
const SEC: u64 = 1_000_000_000;

fn acc(s: &str) -> AccountId {
    s.parse().unwrap()
}

fn ctx(who: &str, deposit: u128, t: u64) {
    let mut b = VMContextBuilder::new();
    b.current_account_id(acc("assay.testnet"))
        .predecessor_account_id(acc(who))
        .attached_deposit(NearToken::from_yoctonear(deposit))
        .block_timestamp(t)
        .account_balance(NearToken::from_near(1000));
    testing_env!(b.build());
}

fn setup() -> Contract {
    ctx("owner.testnet", 0, 0);
    let mut c = Contract::new(acc("owner.testnet"), acc("reviewer.testnet"), acc("operator.testnet"));
    for (name, section) in [("ledger", "history"), ("quickcheck", "history"), ("comparables", "budget")] {
        ctx("backer.testnet", 2 * N, 0);
        c.register_agent(name.into(), section.into(), "a".repeat(64), "dated-context".into(), vec!["sputnik.history".into()], None);
    }
    c
}

fn agent(n: &str) -> AccountId {
    acc(&format!("{n}.assay.testnet"))
}

#[test]
fn commitment_matches_python() {
    ctx("x.testnet", 0, 0);
    // value printed by swarm.bidding.commitment for the same inputs
    assert_eq!(
        commitment(&agent("ledger"), 1, "history", 300_000_000_000_000_000_000_000, "abc"),
        "f7b868237a0bd888813ba15efac257069cff0ff329e4e204e4ca860cae9a2111"
    );
}

#[test]
fn registers_subaccount_agents_with_backer_shares() {
    let c = setup();
    let a = c.get_agent(agent("ledger")).unwrap();
    assert_eq!(a.balance.0, 2 * N - SUBACCOUNT_SEED);
    assert_eq!(c.get_shares(agent("ledger"), acc("backer.testnet")).0, a.total_shares.0);
}

#[test]
#[should_panic(expected = "agent deposit must be at least 1 NEAR")]
fn agent_needs_deposit() {
    let mut c = setup();
    ctx("x.testnet", N / 2, 0);
    c.register_agent("cheap".into(), "risk".into(), "a".repeat(64), "kb".into(), vec![], None);
}

fn full_job(c: &mut Contract) -> u64 {
    ctx("marketing.sputnik-dao.near", 2 * N, 0);
    let id = c.post_job(acc("marketing.sputnik-dao.near"), 812, "review".into(), 60, 60);
    for (n, price, salt) in [("ledger", 3 * N / 10, "s1"), ("quickcheck", N / 10, "s2"), ("comparables", N / 4, "s3")] {
        let section = if n == "comparables" { "budget" } else { "history" };
        ctx("operator.testnet", 0, 10 * SEC);
        let d = commitment(&agent(n), id, section, price, salt);
        c.commit_bid(id, section.into(), agent(n), d);
    }
    for (n, price, salt) in [("ledger", 3 * N / 10, "s1"), ("quickcheck", N / 10, "s2"), ("comparables", N / 4, "s3")] {
        let section = if n == "comparables" { "budget" } else { "history" };
        ctx("operator.testnet", 0, 70 * SEC);
        assert!(c.reveal_bid(id, section.into(), agent(n), U128(price), salt.into()));
    }
    ctx("anyone.testnet", 0, 130 * SEC);
    c.close_auction(id);
    id
}

#[test]
fn lowest_revealed_bid_wins_and_runner_up_takes_over() {
    let mut c = setup();
    let id = full_job(&mut c);
    let job = c.get_job(id).unwrap();
    let hist = &job.sections[0];
    assert_eq!(hist.ranked[0].0, agent("quickcheck"));
    assert_eq!(hist.ranked[1].0, agent("ledger"));
    ctx("reviewer.testnet", 0, 140 * SEC);
    assert_eq!(c.reassign(id, "history".into()), Some(agent("ledger")));
}

#[test]
fn wrong_salt_does_not_reveal() {
    let mut c = setup();
    ctx("dao.testnet", N, 0);
    let id = c.post_job(acc("dao.testnet"), 1, "r".into(), 60, 60);
    ctx("operator.testnet", 0, 1);
    c.commit_bid(id, "history".into(), agent("ledger"), commitment(&agent("ledger"), id, "history", N / 10, "good"));
    ctx("operator.testnet", 0, 61 * SEC);
    assert!(!c.reveal_bid(id, "history".into(), agent("ledger"), U128(N / 10), "bad".into()));
    assert!(!c.reveal_bid(id, "history".into(), agent("ledger"), U128(N / 5), "good".into()));
}

#[test]
#[should_panic(expected = "commit window closed")]
fn no_commits_after_deadline() {
    let mut c = setup();
    ctx("dao.testnet", N, 0);
    let id = c.post_job(acc("dao.testnet"), 1, "r".into(), 60, 60);
    ctx("operator.testnet", 0, 61 * SEC);
    c.commit_bid(id, "history".into(), agent("ledger"), "0".repeat(64));
}

#[test]
#[should_panic(expected = "only the agent or operator")]
fn strangers_cannot_bid_for_an_agent() {
    let mut c = setup();
    ctx("dao.testnet", N, 0);
    let id = c.post_job(acc("dao.testnet"), 1, "r".into(), 60, 60);
    ctx("mallory.testnet", 0, 1);
    c.commit_bid(id, "history".into(), agent("ledger"), "0".repeat(64));
}

#[test]
fn review_pays_accepted_sections_into_backed_balance_and_records_memo() {
    let mut c = setup();
    let id = full_job(&mut c);
    let before = c.get_agent(agent("ledger")).unwrap().balance.0;
    ctx("reviewer.testnet", 0, 140 * SEC);
    c.reassign(id, "history".into()); // quickcheck's work failed review
    c.submit_review(id, "b".repeat(64), vec![true, true, false, false, false], 72, "FUND".into());
    let ledger = c.get_agent(agent("ledger")).unwrap();
    assert_eq!(ledger.balance.0, before + 3 * N / 10);
    assert_eq!(ledger.earned.0, 3 * N / 10);
    assert_eq!(c.get_agent(agent("quickcheck")).unwrap().earned.0, 0);
    let job = c.get_job(id).unwrap();
    assert_eq!(job.escrow.0, 0);
    assert_eq!(job.status, JobStatus::Reviewed);
    let memo = c.get_memo(acc("marketing.sputnik-dao.near"), 812).unwrap();
    assert_eq!(memo.verdict, "FUND");
    assert_eq!(memo.score, 72);
}

#[test]
#[should_panic(expected = "only reviewer")]
fn only_reviewer_records_memos() {
    let mut c = setup();
    let id = full_job(&mut c);
    ctx("operator.testnet", 0, 140 * SEC);
    c.submit_review(id, "b".repeat(64), vec![true; 5], 72, "FUND".into());
}

#[test]
fn inference_is_paid_from_agent_balance() {
    let mut c = setup();
    ctx("operator.testnet", 0, 1);
    let before = c.get_agent(agent("ledger")).unwrap().balance.0;
    c.charge_inference(agent("ledger"), U128(N / 1000), 1);
    let a = c.get_agent(agent("ledger")).unwrap();
    assert_eq!(a.balance.0, before - N / 1000);
    assert_eq!(a.spent_inference.0, N / 1000);
}

#[test]
fn backers_share_earnings_pro_rata() {
    let mut c = setup();
    ctx("second.testnet", 2 * N - SUBACCOUNT_SEED, 0);
    let minted = c.fund_agent(agent("ledger")).0;
    assert_eq!(minted, 2 * N - SUBACCOUNT_SEED); // same price per share
    // revenue arrives: both backers' shares are worth more
    {
        let a = c.agents.get_mut(&agent("ledger")).unwrap();
        a.balance.0 += N;
    }
    ctx("second.testnet", 0, 0);
    let out = c.withdraw_backing(agent("ledger"), U128(minted)).0;
    assert_eq!(out, 2 * N - SUBACCOUNT_SEED + N / 2);
}

#[test]
fn mul_div_is_exact_beyond_u128() {
    let y = 10u128.pow(24);
    assert_eq!(crate::mul_div(5 * y, 3 * y, 7 * y), 15 * y / 7);
    assert_eq!(crate::mul_div(u128::MAX, u128::MAX, u128::MAX), u128::MAX);
    assert_eq!(crate::mul_div(7, 9, 4), 15);
}
