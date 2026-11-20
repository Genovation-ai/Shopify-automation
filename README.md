# Tech Genius Shopify Repricer & Pricing Dashboard

A Streamlit pricing overview and repricing workflow tool built for **Tech Genius** to review competitor pricing, enforce condition grade hierarchies, and apply verified price updates to Shopify.

## Features

- **Store-Owner View (Default):** Simplified flow focused on checking competitor prices, reviewing proposed changes, and applying selected updates.
- **Admin / Technical View:** Includes GraphQL variant IDs, cached competitor URL management (`verified_mappings.json`), execution flags, and raw execution logs (`logs/run_*.log`).
- **Condition Grade Rules:** Configurable competitor undercut and minimum percentage step-down across condition tiers (`Excellent > Very Good > Good > Fair`).
- **Audit & Overrides:** Tracks updates in `price_update_log.jsonl` and supports manual price overrides and pinned variants (`pinned_variants.json`).

## Local Setup

1. Install dependencies:
   pip install -r requirements.txt

2. Configure your Shopify and environment credentials (avoid committing secrets or tokens to Git).

3. Run the Streamlit dashboard:
   streamlit run dashboard.py

## Deployment Notes

- **Source Control:** Code lives in GitHub (`Genovation-ai/Shopify-automation`). Keep runtime CSVs, logs, and secrets out of version control.
- **Cloud Hosting Options:** Because Streamlit runs as a persistent Python app and the repricer relies on files/scheduled runs, platforms like **Render** or **Railway** fit better than static/serverless frontend hosts like Vercel.
