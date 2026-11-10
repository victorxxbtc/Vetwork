// HOT near-connect 0.12 wallet, loaded as an ES module from a CDN (no JS build step).
const btn = document.getElementById("wallet");
const contractId = btn?.dataset.contract || "";
const network = btn?.dataset.network || "testnet";
let connector = null;

async function getConnector() {
  if (connector) return connector;
  const { NearConnector } = await import("https://cdn.jsdelivr.net/npm/@hot-labs/near-connect@0.12.0/+esm");
  connector = new NearConnector({ network });
  connector.on("wallet:signIn", (t) => { btn.textContent = t.accounts[0].accountId; });
  connector.on("wallet:signOut", () => { btn.textContent = "Connect wallet"; });
  return connector;
}

btn?.addEventListener("click", async () => {
  try {
    const c = await getConnector();
    await c.connect();
  } catch (e) {
    btn.textContent = "Wallet unavailable";
    btn.title = String(e);
  }
});

// Agents page: register on chain (needs SWARM_CONTRACT_ID).
document.getElementById("onchain-add")?.addEventListener("click", async (ev) => {
  const form = ev.target.closest("form");
  const fd = new FormData(form);
  const prompt = fd.get("prompt") || "";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(prompt));
  const promptHash = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  const deposit = BigInt(Math.round(Number(fd.get("deposit")) * 100)) * 10n ** 22n;
  try {
    const c = await getConnector();
    const wallet = await c.wallet();
    await wallet.signAndSendTransaction({
      receiverId: contractId,
      actions: [{
        type: "FunctionCall",
        params: {
          methodName: "register_agent",
          args: { name: fd.get("name"), section: fd.get("section"), prompt_hash: promptHash,
                  knowledge_base: fd.get("knowledge_base"), tools: fd.getAll("tools"), public_key: null },
          gas: "100000000000000",
          deposit: deposit.toString(),
        },
      }],
    });
  } catch (e) {
    alert("On-chain registration failed: " + e);
  }
});
