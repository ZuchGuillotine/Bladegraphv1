# Minimum viable compute and costs

Run the controller locally initially. Thirteen API-based workers require little local
memory; there are no local model weights. SQLite and standard-library Python need no
database service. A small persistent Linux VM is optional for unattended runs, not a
prerequisite for the MVP. No cloud resources have been provisioned.

If a VM becomes useful, use one shared-vCPU instance with roughly 2 GB RAM, Ubuntu,
and a persistent disk. Compare the provider's current smallest offerings at purchase;
[Hetzner Cloud](https://www.hetzner.com/cloud/) is one simple candidate. Set an explicit
monthly ceiling around $10 including disk and backups. No GPU, Kubernetes, Cloudflare
Sandbox fleet, managed graph database or per-agent VM is needed for this capability
sandbox. Stop/delete the VM after the experiment if it is no longer useful.

The controller requires outbound HTTPS only to TypeSafe and OpenRouter; it needs no
public inbound port. Keep SSH restricted to the researcher. Transfer a reviewed source
archive or private checkout to the controller, put `.env` outside shared volumes with
mode 0600, persist `private/spend.sqlite` across runs, and copy research logs back over
SSH. Never expose the local researcher demo over a public server. The worker is only
an inference conversation, with no VM or repository mount.

The first configured worker is `google/gemini-2.5-flash-lite` and reviewer is
`anthropic/claude-sonnet-4.6`, from different families. The OpenRouter model catalog on
2026-09-24 lists input/output prices of $0.10/$0.40 and $3/$15 per million tokens,
respectively. [Catalog](https://openrouter.ai/api/v1/models).
[TypeSafe's model page](https://docs.typesafe.ai/models) lists `jev-1.13.0` at
$0.042 per million input tokens, with free output. Recheck all prices before future
paid work. The `.env` fields are explicit so pricing does not silently follow aliases.

At 13 workers × 4 cycles, the pilot permits at most 52 worker calls. A rough 2,000 input
+ 300 output tokens per worker call is about $0.017 at these worker prices. A 36-call
Jev development diagnostic at 4,000 input tokens each is about $0.006. Nine reviewer
calls at 2,000 input + 400 output tokens each are about $0.108. These are estimates,
not bills; the actual ledger is authoritative for observed/estimated API spend.

Every provider call reserves a conservative byte-based upper estimate before sending.
No automatic retries. On uncertain network/usage failures the reservation remains
charged against available budget. A returned cost over the reservation halts work
after recording the overage. Configure provider-level caps too: application estimates
cannot guarantee a ceiling if provider pricing or reported usage is wrong.

Spend components: worker_agents, Jev, reviewer, simulation, storage, other. Local
fixture external spend is zero; local electricity and development-assistant usage are
not measured. Unprovisioned storage/VM cost is zero. Any future purchased infrastructure
must be recorded in the same ledger before experiments use the remaining allowance.
