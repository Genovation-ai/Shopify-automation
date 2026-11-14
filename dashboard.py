import os
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Tech Genius | Automated Shopify Repricing Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
REPORT_CSV = BASE_DIR / "catalogue_report.csv"
AUDIT_JSONL = BASE_DIR / "price_update_log.jsonl"
MAPPINGS_JSON = BASE_DIR / "verified_mappings.json"
TARGETS_JSON = BASE_DIR / "approved_targets.json"

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    div[data-testid="stMetric"] {
        background-color: #111827;
        border: 1px solid #1f2937;
        padding: 16px 20px;
        border-radius: 10px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);
    }
</style>
""", unsafe_allow_html=True)

def get_latest_log():
    if not LOGS_DIR.exists():
        return None
    log_files = sorted(LOGS_DIR.glob("run_*.log"), key=os.path.getmtime, reverse=True)
    return log_files[0] if log_files else None

def parse_log_summary(log_path: Path):
    if not log_path or not log_path.exists():
        return {
            "filename": "No log found",
            "start": "N/A",
            "end": "In Progress / Not Finished",
            "last_group_num": "0 / 329",
            "last_group_name": "N/A",
            "updated_count": 0,
            "holds_count": 0,
            "recent_updates": [],
            "raw_tail": "",
        }
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start_line = next((l for l in lines if "=== Starting" in l), "Started")
    end_line = next((l for l in reversed(lines) if "=== Run Finished" in l), "Currently Running")
    
    current_product = ""
    updates = []
    holds = []
    groups = []

    for l in lines:
        if "--- Group" in l:
            clean_g = l.replace("---", "").strip()
            groups.append(clean_g)
            if ":" in clean_g:
                current_product = clean_g.split(":", 1)[1].split("(")[0].strip()
        elif "UPDATED AND VERIFIED:" in l:
            detail = l.replace("UPDATED AND VERIFIED:", "").strip()
            updates.append(f"{current_product} — {detail}" if current_product else detail)
        elif "[VERY GOOD HOLD]" in l:
            holds.append(l.replace("[VERY GOOD HOLD]", "").strip())

    last_group_num = "0 / 329"
    last_group_name = "Completed"
    if groups:
        last_g = groups[-1]
        parts = last_g.split(":", 1)
        last_group_num = parts[0].replace("Group", "").strip()
        if len(parts) > 1:
            last_group_name = parts[1].strip()

    return {
        "filename": log_path.name,
        "start": start_line.replace("===", "").strip(),
        "end": end_line.replace("===", "").strip(),
        "last_group_num": last_group_num,
        "last_group_name": last_group_name,
        "updated_count": len(updates),
        "holds_count": len(holds),
        "recent_updates": updates[-20:][::-1],
        "raw_tail": "\n".join(lines[-40:]),
    }

def load_audit_log():
    if not AUDIT_JSONL.exists():
        return pd.DataFrame()
    records = []
    with open(AUDIT_JSONL, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return pd.DataFrame(records)

def load_json_file(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}

with st.sidebar:
    st.markdown("## ⚡ Tech Genius AI")
    st.caption("Automated Multi-Grade Shopify Repricing Suite")
    st.divider()

    latest_log = get_latest_log()
    log_info = parse_log_summary(latest_log)

    st.markdown("**Automation Schedule**")
    st.success("⏰ Active: 8:00 AM & 8:00 PM Daily")
    st.caption(f"Latest Log: `{log_info['filename']}`")
    st.divider()

    st.markdown("### Manual Controls")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔄 Refresh UI", width="stretch"):
            st.rerun()
    with col_b:
        run_now = st.button("🚀 Run Agent", type="primary", width="stretch")

    if run_now:
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "scheduled_run.py")],
            cwd=str(BASE_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        )
        st.toast("Background repricing run launched!", icon="🚀")

st.title("⚡ Automated Shopify Repricing Dashboard")
st.caption("Real-time competitor price intelligence across Amazon & Best Buy with automated multi-grade protection.")

mappings_data = load_json_file(MAPPINGS_JSON)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Shopify Variants Updated", f"{log_info['updated_count']:,}", "Verified via GraphQL")
k2.metric("Catalogue Groups Processed", log_info["last_group_num"], log_info["end"][:22])
k3.metric("Safety Holds Applied", f"{log_info['holds_count']:,}", "Midpoint & Margin Guard")
k4.metric("Cached Competitor Mappings", f"{len(mappings_data):,}", "Amazon & Best Buy URLs")

st.divider()

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Live Overview & Logs",
    "💲 Catalogue Price Report",
    "🔗 Competitor URL Manager",
    "📜 Verified Audit History",
])

with tab1:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("✅ Latest Verified Shopify Updates")
        if log_info["recent_updates"]:
            df_recent = pd.DataFrame({"Product, Grade, Variant & Verified Shopify Price": log_info["recent_updates"]})
            st.dataframe(df_recent, width="stretch", hide_index=True, height=420)
        else:
            st.info("No variant updates recorded in the latest log yet.")

    with c2:
        st.subheader("🖥️ Live Execution Log Tail")
        st.code(log_info["raw_tail"] or "No log output available.", language="text")

with tab2:
    st.subheader("💲 Full Catalogue Repricing Report")
    if REPORT_CSV.exists():
        df_report = pd.read_csv(REPORT_CSV)
        search_q = st.text_input("🔍 Filter by Model, Storage, Colour, or Condition Grade", placeholder="e.g. iPhone 15 Pro 256GB Good")
        if search_q:
            mask = df_report.astype(str).apply(lambda x: x.str.contains(search_q, case=False, na=False)).any(axis=1)
            df_report = df_report[mask]
        st.dataframe(df_report, width="stretch", height=480)
        st.download_button(
            "📥 Export Filtered Report (.CSV)",
            data=df_report.to_csv(index=False).encode("utf-8"),
            file_name=f"repricing_report_{datetime.now().strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
        )
    else:
        st.warning("`catalogue_report.csv` not found in the directory yet.")

with tab3:
    st.subheader("🔗 Interactive Competitor URL Manager (`verified_mappings.json`)")
    st.caption("Double-click any cell below to view or update cached Amazon & Best Buy product URLs, then click Save.")
    if mappings_data:
        rows = []
        for key, val in mappings_data.items():
            if isinstance(val, dict):
                rows.append({
                    "Product Key": key,
                    "Amazon URL": val.get("amazon_url") or val.get("amazon") or "",
                    "Best Buy URL": val.get("bestbuy_url") or val.get("bestbuy") or "",
                })
            else:
                rows.append({"Product Key": key, "Amazon URL": str(val), "Best Buy URL": ""})
        df_map = pd.DataFrame(rows)
        map_filter = st.text_input("🔍 Search Cached Mappings", placeholder="e.g. iPhone 14")
        if map_filter:
            mask = df_map.astype(str).apply(lambda x: x.str.contains(map_filter, case=False, na=False)).any(axis=1)
            df_map = df_map[mask]
        edited_df = st.data_editor(df_map, width="stretch", height=420, num_rows="dynamic")
        if st.button("💾 Save URL Changes to Cache", type="primary"):
            for _, row in edited_df.iterrows():
                k = row["Product Key"]
                if k in mappings_data and isinstance(mappings_data[k], dict):
                    if "amazon_url" in mappings_data[k]:
                        mappings_data[k]["amazon_url"] = row["Amazon URL"]
                    if "bestbuy_url" in mappings_data[k]:
                        mappings_data[k]["bestbuy_url"] = row["Best Buy URL"]
            MAPPINGS_JSON.write_text(json.dumps(mappings_data, indent=2), encoding="utf-8")
            st.success("Saved updated URLs to verified_mappings.json!")
    else:
        st.info("No mappings found in `verified_mappings.json`.")

with tab4:
    st.subheader("📜 Historical Price Change Audit (`price_update_log.jsonl`)")
    df_audit = load_audit_log()
    if not df_audit.empty:
        st.dataframe(df_audit.iloc[::-1], width="stretch", height=480)
    else:
        st.info("No audit entries found in `price_update_log.jsonl` yet.")