# Tech Genius supervised price agent update

Install all six Python files from this folder together beside your existing
`shopify_check.py` and `competitor_searches.csv`. This update now replaces
`batch_proposals.py` too. Put both test files in the `tests` subfolder.
Keep your existing credentials and dependencies. Back up your project first.
Do not run old and new copies together. Use the exact filenames in this package.

## First check (no browser, Shopify access, or price changes)

```powershell
python run_agent.py --model "iPhone 12 Pro Max" --all --list-targets
```

## Preview one model with approved colour fallback

```powershell
python run_agent.py --model "iPhone 12 Pro Max" --all
```

`--all` means the remaining entries of this exact model, starting at `--start`.
It does not select every model. `--limit` remains the batch size (1 to 5).
With `--all`, colours are grouped by model, storage and condition. Searches
still use at most `--limit` targets at a time. All candidates in one storage
group are then verified before its proposals are prepared. This lets a missing
colour use the lowest fresh verified peer from another search batch in the same
storage group, rather than depending on catalogue order. No earlier-run cache
is used. If manual prompts take too long, stale source offers remain excluded.
Amazon prompts still require you. The end-of-browser close prompt is skipped by
the runner so results move directly to validation. Standalone browser scripts
still pause on closing unless you specify `--auto-close`.

Review `run_report.csv`. Every selected target has a row: PREVIEW,
ALREADY_CORRECT, HOLD_APPROVAL, HOLD, NO_CANDIDATE, ERROR, NOT_PROCESSED,
or an update outcome.
Each run archives inputs, outputs and per-batch reports under `agent_runs`.
`pricing_basis` is `EXACT_COLOUR` or `COLOUR_FALLBACK`; `source_color` and
`source_variant_id` identify the actual source. A fallback target may have
`verified_count=0`: it was not falsely marked as directly verified.
An empty or failed search cannot reuse earlier offers. A search error stops the
run; an ordinary no-match or an individual failed verification leaves that
target pending and allows later batches to continue.

## Approve reviewed catalogue identities without code edits

Existing IDs in `batch_update.py` remain approved. After reviewing a model's
target list, explicitly approve its identities with:

```powershell
python run_agent.py --model "iPhone 12 Pro Max" --all --approve-targets
```

For just one selection, omit `--all` and use `--start` and `--limit`.
This writes `approved_targets.json` locally; it makes no network calls or price
changes. It authorizes future fresh prices for those exact variant IDs,
models, storage sizes, colours and Excellent condition. It does not approve a
fixed price. Review and retain this configuration. Unapproved proposals are
held individually; no-change variants can be confirmed without write approval.

## Apply only when ready

```powershell
python run_agent.py --model "iPhone 12 Pro Max" --all --apply
```

This performs a NEW search and verification before each batch's updates.
Prices may differ from an earlier preview. The rule remains item price minus
CAD $3, including increases. Explicit delivery fees are excluded from the item
price as in your latest verifier. The 15-minute offer limit is unchanged.
An exact-colour usable offer always takes priority. Only when none is usable
does the target use the lowest fresh VERIFIED offer from another colour with
the same exact model, storage, Excellent condition, and Shopify product.
The raw verification checks for unlocked status and required SIM compatibility
still apply to every source. A HOLD offer, old Shopify price, proposal, or
another fallback can never be used as a verified source. Source Shopify identity
is validated independently. Writes affect only the target variant, and the
audit records the source colour, variant, URL and verification time.
Model, storage, colour, Excellent condition, unlocked evidence, iPhone 14 SIM
matching, store, currency, live Shopify identity/price checks, audit entries,
and independent price read-back remain required.

For the current small batch, `python batch_update.py` previews fresh results;
`python batch_update.py --apply` applies eligible approved changes. In an apply
run, approved targets may update while unapproved targets remain on HOLD.
Review the per-target report, not just the command's exit status.

`UPDATE_UNCONFIRMED` means a write may have happened without confirmed
read-back. Stop and inspect Shopify and `price_update_log.jsonl` before retrying.
API writes are not retried automatically.

## Diagnostics and limits

```powershell
python amazon_verify.py --debug
```

This shows captured offer text and detailed checks. Normal output names each
target and provides specific failed checks, instead of only a combined message.
Visible non-Excellent offers remain on HOLD. Carrier-locked or contradictory
provider evidence overrides an unlocked title.

This is still a supervised Amazon workflow. Best Buy, scheduling, notifications,
and fully unattended handling are not included. No live Amazon or Shopify test
was run in this development environment. Test on your PC in preview mode first.
Keep your existing working `shopify_check.py`, credentials and catalogue CSV.
`batch_targets.csv` is generated automatically by the runner and scopes fallback
targets to the selected group. Standalone proposal preparation without that
file remains exact-colour only. Use the runner for the colour-fallback workflow.

## Offline regression checks

```powershell
python -m unittest discover -s tests -v
```

Tests simulate Shopify requests and cannot send live updates.
