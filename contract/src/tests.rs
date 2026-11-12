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
    b.current_account_id(acc("vetwork.testnet"))
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
    acc(&format!("{n}.vetwork.testnet"))
}

#[test]
fn commitment_matches_python() {
    ctx("x.testnet", 0, 0);
    // value printed by swarm.bidding.commitment for the same inputs
    assert_eq!(
        commitment(&agent("ledger"), 1, "history", 300_000_000_000_000_000_000_000, "abc"),
        "cc15a9e8b17dcd3e8aab4ea8987178ec98de3f38926e815772c006165f0e14e1"
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

fn reviewed_job(c: &mut Contract) -> u64 {
    let id = full_job(c);
    ctx("reviewer.testnet", 0, 140 * SEC);
    c.reassign(id, "history".into());
    c.submit_review(id, "b".repeat(64), vec![true, true, false, false, false], 72, "FUND".into());
    id
}

#[test]
fn paid_work_is_bonded_and_counts_toward_reputation() {
    let mut c = setup();
    let id = reviewed_job(&mut c);
    let l = c.get_agent(agent("ledger")).unwrap();
    assert_eq!(l.locked.0, 3 * N / 10);
    let b = c.get_bond(id, "history".into()).unwrap();
    assert_eq!(b.agent, agent("ledger"));
    assert_eq!(b.until, 140 * SEC + CHALLENGE_SECS * SEC);
    let r = c.get_reputation(agent("ledger")).unwrap();
    assert_eq!((r.accepted, r.rejected, r.slashed), (1, 0, 0));
    let q = c.get_reputation(agent("quickcheck")).unwrap();
    assert_eq!((q.accepted, q.rejected), (0, 1));
    assert_eq!(r.score_bp, reputation_bp(1, 0, 0));
    assert_eq!(reputation_bp(1, 0, 0), 6666);
    assert_eq!(reputation_bp(0, 1, 0), 3333);
}

#[test]
fn upheld_challenge_slashes_the_bond() {
    let mut c = setup();
    let id = reviewed_job(&mut c);
    let before = c.get_agent(agent("ledger")).unwrap();
    ctx("watcher.testnet", CHALLENGE_DEPOSIT, 200 * SEC);
    c.challenge(id, "history".into(), 0, "count contradicts the cited proposals".into());
    ctx("reviewer.testnet", 0, 300 * SEC);
    c.resolve_challenge(id, "history".into(), true);
    let a = c.get_agent(agent("ledger")).unwrap();
    assert_eq!(a.balance.0, before.balance.0 - 3 * N / 10);
    assert_eq!(a.locked.0, 0);
    assert_eq!(a.slashed.0, 3 * N / 10);
    let r = c.get_reputation(agent("ledger")).unwrap();
    assert_eq!((r.accepted, r.slashed), (1, 1));
    assert!(c.get_bond(id, "history".into()).unwrap().settled);
}

#[test]
fn dismissed_challenge_pays_the_agent_and_bond_releases_later() {
    let mut c = setup();
    let id = reviewed_job(&mut c);
    let before = c.get_agent(agent("ledger")).unwrap().balance.0;
    ctx("watcher.testnet", CHALLENGE_DEPOSIT, 200 * SEC);
    c.challenge(id, "history".into(), 0, "wrong".into());
    ctx("reviewer.testnet", 0, 300 * SEC);
    c.resolve_challenge(id, "history".into(), false);
    assert_eq!(c.get_agent(agent("ledger")).unwrap().balance.0, before + CHALLENGE_DEPOSIT);
    ctx("anyone.testnet", 0, 141 * SEC + CHALLENGE_SECS * SEC);
    c.release_bond(id, "history".into());
    assert_eq!(c.get_agent(agent("ledger")).unwrap().locked.0, 0);
}

#[test]
#[should_panic(expected = "funds are bonded")]
fn bonded_funds_cannot_be_withdrawn() {
    let mut c = setup();
    reviewed_job(&mut c);
    let shares = c.get_shares(agent("ledger"), acc("backer.testnet"));
    ctx("backer.testnet", 0, 200 * SEC);
    c.withdraw_backing(agent("ledger"), shares);
}

#[test]
#[should_panic(expected = "challenge window closed")]
fn challenges_close_with_the_window() {
    let mut c = setup();
    let id = reviewed_job(&mut c);
    ctx("watcher.testnet", CHALLENGE_DEPOSIT, 141 * SEC + CHALLENGE_SECS * SEC);
    c.challenge(id, "history".into(), 0, "late".into());
}

#[test]
fn preregistration_records_who_and_when() {
    let mut c = setup();
    ctx("lead.testnet", 0, 42 * SEC);
    c.preregister("c".repeat(64), "pinned set".into());
    let p = c.get_preregistration("c".repeat(64)).unwrap();
    assert_eq!((p.by, p.at), (acc("lead.testnet"), 42 * SEC));
}
