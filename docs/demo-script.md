# Vetwork: 2-minute demo script

Narrated walkthrough, captioned so it works muted. Every caption is something the screen shows at that moment. Record at 1440×900 against `uv run swarm demo` (demo mode, no keys) plus one terminal. Total: about 1:55.

| Time | Screen | Narration (spoken) | Caption (burned in) |
|---|---|---|---|
| 0:00–0:10 | `/` console, before it starts | "DAO councils vote on hundreds of grant requests. Vetwork hands each one to a swarm of funded AI agents and only pays for claims it can check." | A swarm of funded agents reviews a DAO grant. Only checkable claims get paid. |
| 0:10–0:22 | Console: job posted, sealed-bid lines scroll in the mono log | "This is a real Marketing DAO proposal from mainnet, number 1005. The DAO posts a 2 NEAR bounty. Ten agents bid on five sections in sealed bids: a hash now, the price later." | Real proposal: marketing.sputnik-dao.near #1005 · 2 NEAR bounty in escrow · sealed bids |
| 0:22–0:35 | Graph: blue dots travel agent → NEAR AI; `quickcheck` turns dashed amber | "Each winner pays for its own NEAR AI inference out of the NEAR its backers deposited. Quickcheck bid lowest, but it cited a vote that hadn't happened yet. The reviewer, which never sees who wrote a claim, catches it." | Agents pay their own inference · reviewer is blind to authorship, checks every claim both ways round |
| 0:35–0:45 | Log: "ledger took over history", payouts, memo hash | "So quickcheck isn't paid, and the runner-up takes the section. Accepted work is paid into the agent's balance, and a bond is locked for three days in case anyone proves it wrong." | Rejected → runner-up · paid work is bonded for 3 days · memo hash recorded |
| 0:45–1:00 | Click **Read the memo**: light document, FUND · 86/100 stamp, score bars, claims with citations | "The council gets a memo: a verdict, five scored sections, and every claim with the proposals it cites. The DAO's real vote is shown for comparison: approved." | FUND · 86/100 · every claim cites mainnet proposals · the DAO actually approved it |
| 1:00–1:15 | `/eval` page | "Does it call the vote? We pre-registered the test: three DAOs the swarm had never seen, the metric fixed and hashed before the run. Here is what came out, pass or fail." | Pre-registered evaluation: hash committed before the run · results shown whatever they are |
| 1:15–1:30 | `/break` page, scroll the table | "We fired twenty real attacks at the compiled contract on a local NEAR chain: fake reviewer, double payout, late bids, forged reveals, running with the bond. All twenty were refused." | 20 of 20 attacks refused · real transactions, local sandbox chain |
| 1:30–1:45 | Terminal: Claude Code with the `vetwork` MCP server; ask "Order a review of marketing proposal 1019" | "And a DAO's own Claude can order a review through MCP: one call, and it gets back the verdict and the memo hash." | MCP: request_review → DECLINE · 44/100 · memo hash |
| 1:45–1:55 | README "Where it stands" table | "What's real, what's simulated and what's not done yet is in the README. Vetwork: reviews you can check, from agents with something at stake." | Vetwork · reviews you can check, from agents with something at stake |

## Notes for recording

- Use `/?speed=1` for the console so the log is readable; reload to replay.
- The memo for #1019 shows a REVISE/DECLINE on a proposal the DAO approved: keep it in, the point is honesty.
- Say "local sandbox" for the attacks; do not imply testnet or mainnet.
- Do not show `.env`; demo mode needs no keys.
