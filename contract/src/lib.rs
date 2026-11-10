//! Assay swarm contract.
//!
//! * A DAO (or anyone) posts a review job for a Sputnik proposal with a bounty held in escrow.
//! * Anyone adds an agent: it gets the sub-account `<name>.<this contract>` and a NEAR balance
//!   owned by its backers as shares. The balance pays for the agent's inference.
//! * Agents bid on review sections with commit–reveal sealed bids; lowest revealed price wins.
//! * The operator charges inference to an agent's balance (paid to the operator, who holds the
//!   NEAR AI credits).
//! * The reviewer accepts or rejects each section. Accepted work is paid from escrow into the
//!   agent's balance (raising its backers' share value); rejected work goes to the runner-up.
//! * The memo hash is recorded against `<dao>#<proposal_id>`; unspent bounty returns to the poster.

use near_sdk::json_types::U128;
use near_sdk::store::{IterableMap, LookupMap};
use near_sdk::{env, near, require, AccountId, BorshStorageKey, NearToken, PanicOnDefault, Promise, PublicKey};

pub const SECTIONS: [&str; 5] = ["history", "budget", "milestones", "treasury", "risk"];
const MIN_BOUNTY: u128 = 500_000_000_000_000_000_000_000; // 0.5 NEAR
const MIN_AGENT_DEPOSIT: u128 = 1_000_000_000_000_000_000_000_000; // 1 NEAR
const SUBACCOUNT_SEED: u128 = 50_000_000_000_000_000_000_000; // 0.05 NEAR, storage for the new account
const BID_RESERVE: u128 = 10_000_000_000_000_000_000_000; // 0.01 NEAR: must be able to pay for a call

#[derive(BorshStorageKey)]
#[near]
enum Key {
    Jobs,
    Agents,
    Shares,
    Commits,
    Memos,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub enum JobStatus {
    Bidding,
    Assigned,
    Reviewed,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Section {
    pub name: String,
    pub bidders: Vec<AccountId>,
    /// (agent, price) sorted by price then commit order, filled by `close_auction`.
    pub ranked: Vec<(AccountId, U128)>,
    pub winner_idx: u32,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Job {
    pub poster: AccountId,
    pub dao: AccountId,
    pub proposal_id: u64,
    pub description: String,
    pub bounty: U128,
    pub escrow: U128,
    pub commit_ends: u64,
    pub reveal_ends: u64,
    pub sections: Vec<Section>,
    pub status: JobStatus,
    pub memo_hash: Option<String>,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Agent {
    pub account_id: AccountId,
    pub section: String,
    pub creator: AccountId,
    pub prompt_hash: String,
    pub knowledge_base: String,
    pub tools: Vec<String>,
    pub balance: U128,
    pub total_shares: U128,
    pub earned: U128,
    pub spent_inference: U128,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Commit {
    pub digest: String,
    pub seq: u64,
    pub price: Option<U128>,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct MemoRecord {
    pub job_id: u64,
    pub memo_hash: String,
    pub score: u8,
    pub verdict: String,
    pub recorded_at: u64,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Contract {
    owner: AccountId,
    reviewer: AccountId,
    operator: AccountId,
    next_job: u64,
    seq: u64,
    jobs: IterableMap<u64, Job>,
    agents: IterableMap<AccountId, Agent>,
    shares: LookupMap<String, u128>,
    commits: LookupMap<String, Commit>,
    memos: LookupMap<String, MemoRecord>,
}

pub fn hex(bytes: &[u8]) -> String {
    const H: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(H[(b >> 4) as usize] as char);
        s.push(H[(b & 15) as usize] as char);
    }
    s
}

/// Same preimage as `swarm.bidding.commitment` in Python.
pub fn commitment(agent: &AccountId, job_id: u64, section: &str, price: u128, salt: &str) -> String {
    hex(&env::sha256(format!("{agent}:{job_id}:{section}:{price}:{salt}").as_bytes()))
}

fn share_key(agent: &AccountId, backer: &AccountId) -> String {
    format!("{agent}|{backer}")
}
fn commit_key(job: u64, section: &str, agent: &AccountId) -> String {
    format!("{job}|{section}|{agent}")
}
fn memo_key(dao: &AccountId, proposal_id: u64) -> String {
    format!("{dao}#{proposal_id}")
}

#[near]
impl Contract {
    #[init]
    pub fn new(owner: AccountId, reviewer: AccountId, operator: AccountId) -> Self {
        Self {
            owner,
            reviewer,
            operator,
            next_job: 1,
            seq: 0,
            jobs: IterableMap::new(Key::Jobs),
            agents: IterableMap::new(Key::Agents),
            shares: LookupMap::new(Key::Shares),
            commits: LookupMap::new(Key::Commits),
            memos: LookupMap::new(Key::Memos),
        }
    }

    // ------------------------------------------------------------ jobs
    #[payable]
    pub fn post_job(&mut self, dao: AccountId, proposal_id: u64, description: String, commit_secs: u32, reveal_secs: u32) -> u64 {
        let bounty = env::attached_deposit().as_yoctonear();
        require!(bounty >= MIN_BOUNTY, "bounty must be at least 0.5 NEAR");
        require!(description.len() <= 2000, "description too long");
        require!(commit_secs > 0 && reveal_secs > 0, "windows must be positive");
        let now = env::block_timestamp();
        let commit_ends = now + commit_secs as u64 * 1_000_000_000;
        let id = self.next_job;
        self.next_job += 1;
        let sections = SECTIONS
            .iter()
            .map(|s| Section { name: s.to_string(), bidders: vec![], ranked: vec![], winner_idx: 0 })
            .collect();
        self.jobs.insert(
            id,
            Job {
                poster: env::predecessor_account_id(),
                dao: dao.clone(),
                proposal_id,
                description,
                bounty: U128(bounty),
                escrow: U128(bounty),
                commit_ends,
                reveal_ends: commit_ends + reveal_secs as u64 * 1_000_000_000,
                sections,
                status: JobStatus::Bidding,
                memo_hash: None,
            },
        );
        env::log_str(&format!("EVENT_JSON:{{\"event\":\"job_posted\",\"job_id\":{id},\"dao\":\"{dao}\",\"proposal_id\":{proposal_id},\"bounty\":\"{bounty}\"}}"));
        id
    }

    // ------------------------------------------------------------ agents
    #[payable]
    pub fn register_agent(
        &mut self,
        name: String,
        section: String,
        prompt_hash: String,
        knowledge_base: String,
        tools: Vec<String>,
        public_key: Option<PublicKey>,
    ) -> AccountId {
        let deposit = env::attached_deposit().as_yoctonear();
        require!(deposit >= MIN_AGENT_DEPOSIT, "agent deposit must be at least 1 NEAR");
        require!(SECTIONS.contains(&section.as_str()), "unknown section");
        require!(
            name.len() >= 2 && name.len() <= 32 && name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'),
            "name: 2-32 chars of a-z 0-9 -"
        );
        require!(prompt_hash.len() == 64, "prompt_hash must be a sha256 hex digest");
        let account: AccountId = format!("{name}.{}", env::current_account_id()).parse().unwrap();
        require!(!self.agents.contains_key(&account), "agent exists");
        let creator = env::predecessor_account_id();
        let balance = deposit - SUBACCOUNT_SEED;
        self.agents.insert(
            account.clone(),
            Agent {
                account_id: account.clone(),
                section,
                creator: creator.clone(),
                prompt_hash,
                knowledge_base,
                tools,
                balance: U128(balance),
                total_shares: U128(balance),
                earned: U128(0),
                spent_inference: U128(0),
            },
        );
        self.shares.insert(share_key(&account, &creator), balance);
        let mut p = Promise::new(account.clone()).create_account().transfer(NearToken::from_yoctonear(SUBACCOUNT_SEED));
        if let Some(pk) = public_key {
            p = p.add_full_access_key(pk);
        }
        p.detach();
        env::log_str(&format!("EVENT_JSON:{{\"event\":\"agent_registered\",\"agent\":\"{account}\",\"backer\":\"{creator}\"}}"));
        account
    }

    /// Back an agent: the deposit joins its balance and mints shares at the current price.
    #[payable]
    pub fn fund_agent(&mut self, agent: AccountId) -> U128 {
        let amount = env::attached_deposit().as_yoctonear();
        require!(amount > 0, "attach NEAR");
        let backer = env::predecessor_account_id();
        let a = self.agents.get_mut(&agent).expect("no such agent");
        let minted = if a.total_shares.0 == 0 || a.balance.0 == 0 { amount } else { amount * a.total_shares.0 / a.balance.0 };
        require!(minted > 0, "deposit too small");
        a.balance.0 += amount;
        a.total_shares.0 += minted;
        let k = share_key(&agent, &backer);
        let prev = self.shares.get(&k).copied().unwrap_or(0);
        self.shares.insert(k, prev + minted);
        U128(minted)
    }

    /// Redeem shares for their pro-rata part of the agent's balance (earnings included).
    pub fn withdraw_backing(&mut self, agent: AccountId, shares: U128) -> U128 {
        let backer = env::predecessor_account_id();
        let k = share_key(&agent, &backer);
        let held = self.shares.get(&k).copied().unwrap_or(0);
        require!(shares.0 > 0 && shares.0 <= held, "not enough shares");
        let a = self.agents.get_mut(&agent).expect("no such agent");
        let out = shares.0 * a.balance.0 / a.total_shares.0;
        a.balance.0 -= out;
        a.total_shares.0 -= shares.0;
        self.shares.insert(k, held - shares.0);
        Promise::new(backer).transfer(NearToken::from_yoctonear(out)).detach();
        U128(out)
    }

    /// Operator only: pay for an agent's NEAR AI inference out of its balance.
    pub fn charge_inference(&mut self, agent: AccountId, amount: U128, job_id: u64) {
        require!(env::predecessor_account_id() == self.operator, "only operator");
        let a = self.agents.get_mut(&agent).expect("no such agent");
        require!(amount.0 <= a.balance.0, "agent cannot pay for inference");
        a.balance.0 -= amount.0;
        a.spent_inference.0 += amount.0;
        Promise::new(self.operator.clone()).transfer(NearToken::from_yoctonear(amount.0)).detach();
        env::log_str(&format!("EVENT_JSON:{{\"event\":\"inference_paid\",\"agent\":\"{agent}\",\"job_id\":{job_id},\"amount\":\"{}\"}}", amount.0));
    }

    // ------------------------------------------------------------ sealed bids
    pub fn commit_bid(&mut self, job_id: u64, section: String, agent: AccountId, digest: String) {
        let caller = env::predecessor_account_id();
        require!(caller == agent || caller == self.operator, "only the agent or operator");
        require!(digest.len() == 64 && digest.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)), "digest must be sha256 hex");
        let a = self.agents.get(&agent).expect("no such agent");
        require!(a.section == section, "agent does not bid on this section");
        require!(a.balance.0 >= BID_RESERVE, "agent balance below inference reserve");
        let job = self.jobs.get_mut(&job_id).expect("no such job");
        require!(env::block_timestamp() < job.commit_ends, "commit window closed");
        let s = job.sections.iter_mut().find(|s| s.name == section).expect("unknown section");
        let k = commit_key(job_id, &section, &agent);
        require!(!self.commits.contains_key(&k), "already committed");
        s.bidders.push(agent);
        self.seq += 1;
        self.commits.insert(k, Commit { digest, seq: self.seq, price: None });
    }

    pub fn reveal_bid(&mut self, job_id: u64, section: String, agent: AccountId, price: U128, salt: String) -> bool {
        let caller = env::predecessor_account_id();
        require!(caller == agent || caller == self.operator, "only the agent or operator");
        let job = self.jobs.get(&job_id).expect("no such job");
        let now = env::block_timestamp();
        require!(now >= job.commit_ends && now < job.reveal_ends, "not in reveal window");
        let k = commit_key(job_id, &section, &agent);
        let c = self.commits.get_mut(&k).expect("no commitment");
        require!(c.price.is_none(), "already revealed");
        if commitment(&agent, job_id, &section, price.0, &salt) != c.digest {
            return false;
        }
        c.price = Some(price);
        true
    }

    /// Anyone, after the reveal window: rank revealed bids at or under the per-section cap.
    pub fn close_auction(&mut self, job_id: u64) {
        let mut job = self.jobs.get(&job_id).expect("no such job").clone();
        require!(job.status == JobStatus::Bidding, "already closed");
        require!(env::block_timestamp() >= job.reveal_ends, "reveal window still open");
        let cap = job.bounty.0 / SECTIONS.len() as u128;
        for s in job.sections.iter_mut() {
            let mut ranked: Vec<(u128, u64, AccountId)> = s
                .bidders
                .iter()
                .filter_map(|b| {
                    let c = self.commits.get(&commit_key(job_id, &s.name, b))?;
                    let p = c.price?.0;
                    (p > 0 && p <= cap).then(|| (p, c.seq, b.clone()))
                })
                .collect();
            ranked.sort();
            s.ranked = ranked.into_iter().map(|(p, _, a)| (a, U128(p))).collect();
        }
        job.status = JobStatus::Assigned;
        self.jobs.insert(job_id, job);
    }

    // ------------------------------------------------------------ review
    /// Reviewer only: the current winner's work failed review; hand the section to the runner-up.
    pub fn reassign(&mut self, job_id: u64, section: String) -> Option<AccountId> {
        require!(env::predecessor_account_id() == self.reviewer, "only reviewer");
        let job = self.jobs.get_mut(&job_id).expect("no such job");
        require!(job.status == JobStatus::Assigned, "job not assigned");
        let s = job.sections.iter_mut().find(|s| s.name == section).expect("unknown section");
        s.winner_idx += 1;
        s.ranked.get(s.winner_idx as usize).map(|(a, _)| a.clone())
    }

    /// Reviewer only: pay accepted sections, refund the rest, record the memo hash.
    pub fn submit_review(&mut self, job_id: u64, memo_hash: String, accepted: Vec<bool>, score: u8, verdict: String) {
        require!(env::predecessor_account_id() == self.reviewer, "only reviewer");
        require!(memo_hash.len() == 64, "memo_hash must be sha256 hex");
        require!(score <= 100, "score is 0-100");
        require!(["FUND", "REVISE", "DECLINE"].contains(&verdict.as_str()), "bad verdict");
        let mut job = self.jobs.get(&job_id).expect("no such job").clone();
        require!(job.status == JobStatus::Assigned, "job not assigned");
        require!(accepted.len() == job.sections.len(), "one flag per section");
        for (s, ok) in job.sections.iter().zip(accepted.iter()) {
            if !*ok {
                continue;
            }
            let (agent, price) = s.ranked.get(s.winner_idx as usize).expect("no winner for accepted section").clone();
            job.escrow.0 -= price.0;
            let a = self.agents.get_mut(&agent).expect("agent");
            a.balance.0 += price.0;
            a.earned.0 += price.0;
            env::log_str(&format!("EVENT_JSON:{{\"event\":\"payout\",\"job_id\":{job_id},\"agent\":\"{agent}\",\"amount\":\"{}\"}}", price.0));
        }
        let refund = job.escrow.0;
        job.escrow = U128(0);
        job.status = JobStatus::Reviewed;
        job.memo_hash = Some(memo_hash.clone());
        self.memos.insert(
            memo_key(&job.dao, job.proposal_id),
            MemoRecord { job_id, memo_hash: memo_hash.clone(), score, verdict: verdict.clone(), recorded_at: env::block_timestamp() },
        );
        if refund > 0 {
            Promise::new(job.poster.clone()).transfer(NearToken::from_yoctonear(refund)).detach();
        }
        env::log_str(&format!(
            "EVENT_JSON:{{\"event\":\"memo_recorded\",\"dao\":\"{}\",\"proposal_id\":{},\"memo_hash\":\"{memo_hash}\",\"verdict\":\"{verdict}\",\"score\":{score}}}",
            job.dao, job.proposal_id
        ));
        self.jobs.insert(job_id, job);
    }

    // ------------------------------------------------------------ views
    pub fn get_job(&self, job_id: u64) -> Option<Job> {
        self.jobs.get(&job_id).cloned()
    }
    pub fn get_agent(&self, agent: AccountId) -> Option<Agent> {
        self.agents.get(&agent).cloned()
    }
    pub fn list_agents(&self, from: u32, limit: u32) -> Vec<Agent> {
        self.agents.values().skip(from as usize).take(limit as usize).cloned().collect()
    }
    pub fn get_shares(&self, agent: AccountId, backer: AccountId) -> U128 {
        U128(self.shares.get(&share_key(&agent, &backer)).copied().unwrap_or(0))
    }
    pub fn get_memo(&self, dao: AccountId, proposal_id: u64) -> Option<MemoRecord> {
        self.memos.get(&memo_key(&dao, proposal_id)).cloned()
    }
    pub fn get_roles(&self) -> (AccountId, AccountId, AccountId) {
        (self.owner.clone(), self.reviewer.clone(), self.operator.clone())
    }
}

#[cfg(test)]
mod tests;
