#![allow(dead_code)]
//! Shared sandbox setup for the end-to-end and attack tests.

use near_workspaces::types::NearToken;
use near_workspaces::{Account, Contract};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub const N: u128 = 1_000_000_000_000_000_000_000_000;
pub const WASM: &str = "target/near/vetwork_swarm.wasm";

pub fn commitment(agent: &str, job: u64, section: &str, price: u128, salt: &str) -> String {
    let h = Sha256::digest(format!("{agent}:{job}:{section}:{price}:{salt}").as_bytes());
    h.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn yocto(v: &Value) -> u128 {
    v.as_str().unwrap().parse().unwrap()
}

pub struct World {
    pub worker: near_workspaces::Worker<near_workspaces::network::Sandbox>,
    pub c: Contract,
    pub reviewer: Account,
    pub operator: Account,
    pub dao: Account,
    pub backer: Account,
}

pub async fn world() -> anyhow::Result<World> {
    let wasm = std::fs::read(WASM).map_err(|e| anyhow::anyhow!("{WASM}: {e}; run `cargo near build non-reproducible-wasm` first"))?;
    let worker = near_workspaces::sandbox().await?;
    let root = worker.root_account()?;
    let mk = |name: &'static str| {
        let root = root.clone();
        async move {
            root.create_subaccount(name).initial_balance(NearToken::from_near(50)).transact().await.map(|r| r.into_result().unwrap())
        }
    };
    let (owner, reviewer, operator, dao, backer) = (mk("owner").await?, mk("reviewer").await?, mk("operator").await?, mk("dao").await?, mk("backer").await?);
    let swarm = root.create_subaccount("vetwork").initial_balance(NearToken::from_near(30)).transact().await?.into_result()?;
    let c = swarm.deploy(&wasm).await?.into_result()?;
    c.call("new")
        .args_json(json!({"owner": owner.id(), "reviewer": reviewer.id(), "operator": operator.id()}))
        .transact()
        .await?
        .into_result()?;
    Ok(World { worker, c, reviewer, operator, dao, backer })
}

pub async fn register(w: &World, name: &str, section: &str, near: u128) -> anyhow::Result<String> {
    let id: String = w
        .backer
        .call(w.c.id(), "register_agent")
        .args_json(json!({"name": name, "section": section, "prompt_hash": "a".repeat(64),
                          "knowledge_base": "dated-context", "tools": ["sputnik.history"]}))
        .deposit(NearToken::from_yoctonear(near * N))
        .max_gas()
        .transact()
        .await?
        .into_result()?
        .json()?;
    Ok(id)
}

pub async fn job(w: &World, id: u64) -> anyhow::Result<Value> {
    Ok(w.c.view("get_job").args_json(json!({"job_id": id})).await?.json()?)
}

/// Fast-forward until the chain clock passes `t_ns`.
pub async fn wait_until(w: &World, t_ns: u64) -> anyhow::Result<()> {
    for _ in 0..40 {
        let now = w.worker.view_block().await?.timestamp();
        if now > t_ns {
            return Ok(());
        }
        // sandbox blocks are ~1s apart when fast-forwarded; overshoot a little
        w.worker.fast_forward((t_ns - now) / 1_000_000_000 + 5).await?;
    }
    anyhow::bail!("chain clock never reached {t_ns}")
}

pub fn skip() -> bool {
    if std::env::var_os("VETWORK_SKIP_SANDBOX").is_some() {
        eprintln!("VETWORK_SKIP_SANDBOX set: skipping sandbox test");
        return true;
    }
    false
}

