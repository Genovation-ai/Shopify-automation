# Reliability update

Stop the agent before replacing files. Copy these three root files into your
project folder, replacing their existing copies:

- agent_support.py
- amazon_batch.py
- amazon_verify.py

Copy tests/test_reliability.py into your existing tests folder.
Keep your .env, approved_targets.json, catalogue, audit logs and other scripts.

Run offline checks from PowerShell in the project folder:

    python -m unittest discover -s tests -v

With the provided workflow and colour-fallback tests, this is 40 tests.
Additional tests you have added may increase that count.

Then validate the remaining iPhone 12 group in preview:

    python run_agent.py --model "iPhone 12" --start 3 --limit 3

When Amazon needs manual action, complete it in the opened browser window.
Enter rechecks readiness; q stops safely. Readiness checks still require a
visible, enabled, editable search box. This does not bypass Amazon prompts.

CSV saves now retry a permission-denied rename for about 3.5 seconds. A
persistent lock still stops the run and retains pending data in a .csv.tmp
file. Do not open agent output CSV files in Excel while the agent is running.
If a save fails during apply, consult the audit log and preview live prices
before retrying; Shopify writes themselves are never automatically retried.

Validation: 40 offline tests passed. No live Amazon or Shopify run was performed
while preparing this patch. This patch does not add catalogue-wide execution,
resume support, Best Buy integration, scheduling or notifications.
