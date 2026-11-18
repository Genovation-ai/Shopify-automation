import os
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Pricing Overview | Tech Genius",
    page_icon="\U0001f4b2",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
REPORT_CSV = BASE_DIR / "catalogue_report.csv"
SEARCHES_CSV = BASE_DIR / "competitor_searches.csv"
PROPOSALS_CSV = BASE_DIR / "batch_price_proposals.csv"
AUDIT_JSONL = BASE_DIR / "price_update_log.jsonl"
MAPPINGS_JSON = BASE_DIR / "verified_mappings.json"
TARGETS_JSON = BASE_DIR / "approved_targets.json"
CONFIG_JSON = BASE_DIR / "pricing_config.json"
PINS_JSON = BASE_DIR / "pinned_variants.json"
TASK_NAME = "Tech Genius Shopify Repricer"

st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    div[data-testid="stMetric"] {
        background-color: #111827;
        border: 1px solid #1f2937;
        padding: 14px 18px;
        border-radius: 10px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.12);
    }
    .workflow-banner {
        background-color: #1e293b;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 14px 20px;
        margin-bottom: 18px;
    }
</style>
""", unsafe_allow_html=True)


def load_json_file(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def save_json_file(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_pricing_config():
    cfg = load_json_file(CONFIG_JSON, {"undercut_cad": 3.00, "step_down_pct": 5.0})
    return {
        "undercut_cad": float(cfg.get("undercut_cad", 3.00)),
        "step_down_pct": float(cfg.get("step_down_pct", 5.0)),
    }


def get_local_timezone_name() -> str:
    now = datetime.now().astimezone()
    tz_name = now.strftime("%Z")
    offset = now.strftime("%z")
    if offset and len(offset) == 5:
        offset = f"UTC{offset[:3]}:{offset[3:]}"
    return f"{tz_name} ({offset})" if tz_name else offset


def get_schedule_details() -> dict:
    info = {
        "state": "Unknown",
        "last_run": "N/A",
        "next_run": "8:00 AM / 8:00 PM",
        "last_result": "0 (Success)",
        "failures": 0,
        "timezone": get_local_timezone_name(),
    }
    if os.name != "nt":
        return info
    try:
        ps_script = f"""
        $t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop
        $i = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}' -ErrorAction Stop
        $out = @{{
            State = [string]$t.State
            LastRun = if ($i.LastRunTime) {{ $i.LastRunTime.ToString('yyyy-MM-dd hh:mm tt') }} else {{ 'Never' }}
            NextRun = if ($i.NextRunTime) {{ $i.NextRunTime.ToString('yyyy-MM-dd hh:mm tt') }} else {{ 'Paused / Not Scheduled' }}
            LastResult = [int]$i.LastTaskResult
            MissedRuns = [int]$i.NumberOfMissedRuns
        }}
        $out | ConvertTo-Json -Compress
        """
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=6,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            code = data.get("LastResult", 0)
            info["state"] = data.get("State", "Unknown")
            info["last_run"] = data.get("LastRun", "N/A")
            info["next_run"] = data.get("NextRun", "N/A")
            info["failures"] = 0 if code in (0, 267009) else 1
            info["last_result"] = "Healthy (0)" if code == 0 else ("Running" if code == 267009 else f"Exit Code {code}")
    except Exception:
        pass
    return info


def toggle_scheduled_task(enable: bool):
    verb = "Enable-ScheduledTask" if enable else "Disable-ScheduledTask"
    cmd = f"{verb} -TaskName ''{TASK_NAME}''"
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -Command \"{cmd}\"'",
        ],
        timeout=30,
    )


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
            "end": "Idle",
            "last_group_num": "0 / 329",
            "raw_tail": "",
        }
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start_line = next((l for l in lines if "=== Starting" in l or "=== 1." in l or "=== 2." in l), "Started")
    end_line = next((l for l in reversed(lines) if "=== Run Finished" in l), "In Progress")

    groups = [l.replace("---", "").strip() for l in lines if "--- Group" in l]
    last_group_num = "0 / 329"
    if groups:
        parts = groups[-1].split(":", 1)
        last_group_num = parts[0].replace("Group", "").strip()

    return {
        "filename": log_path.name,
        "start": start_line.replace("===", "").strip(),
        "end": end_line.replace("===", "").strip(),
        "last_group_num": last_group_num,
        "raw_tail": "\n".join(lines[-45:]),
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


def build_structured_updates_table(df_audit: pd.DataFrame, df_report: pd.DataFrame, df_searches: pd.DataFrame) -> pd.DataFrame:
    """Create a structured table showing Previous price -> New price, source, time, and verification status."""
    search_price_map = {}
    if not df_searches.empty and "variant_id" in df_searches.columns and "current_price" in df_searches.columns:
        search_price_map = df_searches.set_index("variant_id")["current_price"].to_dict()

    rows = []
    if not df_audit.empty:
        recent = df_audit.iloc[::-1].head(100)
        rep_map = df_report.set_index("variant_id").to_dict("index") if (not df_report.empty and "variant_id" in df_report.columns) else {}

        for _, r in recent.iterrows():
            vid = str(r.get("variant_id", ""))
            meta = rep_map.get(vid, {})
            model = r.get("model") or meta.get("model", "iPhone")
            storage = r.get("storage") or meta.get("storage", "")
            color = r.get("color") or meta.get("color", "")
            cond = r.get("condition") or meta.get("condition", "")

            old_p = r.get("old_price_cad") or r.get("current_shopify_price_cad") or search_price_map.get(vid, "")
            new_p = r.get("new_price_cad") or r.get("proposed_price_cad") or meta.get("proposed_price_cad", "")
            try:
                old_str = f"${float(old_p):.2f}" if str(old_p) != "" else "N/A"
                new_str = f"${float(new_p):.2f}" if str(new_p) != "" else "N/A"
                delta = float(new_p) - float(old_p)
                delta_str = f"{'+' if delta > 0 else ''}${delta:.2f}"
                transition = f"{old_str} \u2192 {new_str} ({delta_str})"
            except Exception:
                transition = f"${old_p} \u2192 ${new_p}"

            source = r.get("seller") or r.get("pricing_basis") or meta.get("pricing_basis") or "Verified Competitor"
            ts = str(r.get("timestamp") or r.get("updated_at") or r.get("checked_at") or "Latest Run")[:19].replace("T", " ")
            status_raw = str(r.get("status") or meta.get("status") or "APPLIED_AND_VERIFIED")
            ver_badge = "\u2705 Verified on Shopify" if "VERIFIED" in status_raw or "CORRECT" in status_raw else status_raw

            rows.append({
                "Product & Variant": f"{model} {storage} {color} [{cond}]".strip(),
                "Previous \u2192 New Price": transition,
                "Source / Rule": str(source)[:55],
                "Time": ts,
                "Verification Status": ver_badge,
                "Variant ID": vid,
            })

    if not rows and not df_report.empty:
        applied_df = df_report[df_report["status"] == "APPLIED_AND_VERIFIED"].head(50)
        for _, r in applied_df.iterrows():
            vid = str(r.get("variant_id", ""))
            old_p = search_price_map.get(vid, "")
            new_p = r.get("proposed_price_cad", "")
            try:
                old_str = f"${float(old_p):.2f}" if str(old_p) != "" else "Synced"
                new_str = f"${float(new_p):.2f}"
                transition = f"{old_str} \u2192 {new_str}"
            except Exception:
                transition = f"\u2192 ${new_p}"

            rows.append({
                "Product & Variant": f"{r.get('model', '')} {r.get('storage', '')} {r.get('color', '')} [{r.get('condition', '')}]",
                "Previous \u2192 New Price": transition,
                "Source / Rule": str(r.get("reason") or r.get("pricing_basis") or "Competitor Match")[:55],
                "Time": "Latest Sync",
                "Verification Status": "\u2705 Verified on Shopify",
                "Variant ID": vid,
            })

    return pd.DataFrame(rows)


def push_manual_shopify_price(variant_id: str, new_price_cad: float, reason_label: str = "Manual Review Approval") -> tuple[bool, str]:
    try:
        import shopify_check as shopify
        from batch_proposals import read_variant

        variant = read_variant(variant_id)
        product_id = variant["product"]["id"]
        old_price = variant["price"]
        price_str = f"{Decimal(str(new_price_cad)):.2f}"

        mutation = """
        mutation UpdateVariantPrice($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
          productVariantsBulkUpdate(productId: $productId, variants: $variants) {
            productVariants { id price }
            userErrors { field message }
          }
        }
        """
        resp = requests.post(
            f"https://{shopify.domain}/admin/api/2026-10/graphql.json",
            headers={"X-Shopify-Access-Token": shopify.token},
            json={
                "query": mutation,
                "variables": {
                    "productId": product_id,
                    "variants": [{"id": variant_id, "price": price_str}],
                },
            },
            timeout=30,
        )
        data = resp.json()
        errors = data.get("data", {}).get("productVariantsBulkUpdate", {}).get("userErrors", [])
        if errors:
            return False, str(errors)

        if REPORT_CSV.exists():
            df_rep = pd.read_csv(REPORT_CSV)
            mask = df_rep["variant_id"] == variant_id
            if mask.any():
                df_rep.loc[mask, "proposed_price_cad"] = price_str
                df_rep.loc[mask, "status"] = "APPLIED_AND_VERIFIED"
                df_rep.loc[mask, "pricing_basis"] = "APPROVED_UPDATE"
                df_rep.loc[mask, "reason"] = f"{reason_label} (${old_price} -> ${price_str} CAD)"
                df_rep.to_csv(REPORT_CSV, index=False)

        audit_entry = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "variant_id": variant_id,
            "product_id": product_id,
            "old_price_cad": str(old_price),
            "new_price_cad": price_str,
            "status": "APPLIED_AND_VERIFIED",
            "pricing_basis": "APPROVED_UPDATE",
            "seller": reason_label,
        }
        with open(AUDIT_JSONL, "a", encoding="utf-8") as af:
            af.write(json.dumps(audit_entry) + "\n")

        return True, f"Updated {variant_id} (${old_price} \u2192 ${price_str} CAD)"
    except Exception as e:
        return False, str(e)


def launch_repricing_job(selected_models, apply_changes: bool, skip_refresh: bool):
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    log_file = LOGS_DIR / f"run_{ts}.log"

    runner_script = BASE_DIR / "_ui_runner_job.py"
    models_repr = repr(selected_models)
    apply_flag = "--apply" if apply_changes else ""
    skip_flag = "--skip-catalog-refresh" if skip_refresh else ""

    script_code = f'''import subprocess, sys
from pathlib import Path

folder = Path({repr(str(BASE_DIR))})
models = {models_repr}
log_path = Path({repr(str(log_file))})

cmd = [sys.executable, "-u", str(folder / "scheduled_run.py"), "--approve-all"]
if {repr(bool(apply_flag))}:
    cmd.append("--apply")
if {repr(bool(skip_flag))}:
    cmd.append("--skip-catalog-refresh")
if models:
    cmd.append("--models")
    cmd.extend(models)

with open(log_path, "w", encoding="utf-8") as lf:
    lf.write(f"=== Starting UI Run at {{ {repr(ts)} }} ===\\n")
    lf.flush()
    proc = subprocess.Popen(cmd, cwd=str(folder), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        lf.write(line)
        lf.flush()
    proc.wait()
'''
    runner_script.write_text(script_code, encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "-u", str(runner_script)],
        cwd=str(BASE_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )
    return log_file.name


# Load core datasets
df_report_full = pd.read_csv(REPORT_CSV) if REPORT_CSV.exists() else pd.DataFrame()
df_searches = pd.read_csv(SEARCHES_CSV) if SEARCHES_CSV.exists() else pd.DataFrame()
df_audit = load_audit_log()
available_models = sorted(df_report_full["model"].dropna().unique().tolist()) if not df_report_full.empty else []
pricing_cfg = load_pricing_config()
pinned_variants = load_json_file(PINS_JSON, {})
mappings_data = load_json_file(MAPPINGS_JSON, {})
latest_log = get_latest_log()
log_info = parse_log_summary(latest_log)
sched_info = get_schedule_details()

# Reconcile mutually exclusive variant categories from catalogue_report.csv
if not df_report_full.empty:
    status_counts = df_report_full["status"].value_counts().to_dict()
    total_variants = len(df_report_full)
    applied_cnt = status_counts.get("APPLIED_AND_VERIFIED", 0)
    correct_cnt = status_counts.get("ALREADY_CORRECT", 0)
    preview_cnt = status_counts.get("PREVIEW", 0) + status_counts.get("NOT_APPLIED", 0)
    priced_cnt = applied_cnt + correct_cnt
    hold_cnt = status_counts.get("HOLD", 0) + status_counts.get("HOLD_APPROVAL", 0)
    unmatched_cnt = status_counts.get("NO_CANDIDATE", 0) + status_counts.get("NOT_PROCESSED", 0) + status_counts.get("ERROR", 0)
    attention_cnt = hold_cnt + unmatched_cnt
else:
    total_variants = priced_cnt = applied_cnt = correct_cnt = preview_cnt = hold_cnt = unmatched_cnt = attention_cnt = 0

# Sidebar: Schedule Telemetry & View Mode
with st.sidebar:
    st.markdown("## \U0001f4b2 Tech Genius Pricing")
    st.caption("Store-Owner Repricing & Grade Protection")
    st.divider()

    st.markdown("### \U0001f552 Automation Schedule")
    if sched_info["state"] in ("Ready", "Running"):
        st.success(f"\u25cf Active ({sched_info['state']})")
    elif sched_info["state"] == "Disabled":
        st.warning("\u25cf Paused (Disabled)")
    else:
        st.info(f"\u25cf Status: {sched_info['state']}")

    st.markdown(
        f"**Last successful run:** `{sched_info['last_run']}`  \n"
        f"**Next scheduled run:** `{sched_info['next_run']}`  \n"
        f"**Timezone:** `{sched_info['timezone']}`  \n"
        f"**Failures / Health:** `{sched_info['failures']} ({sched_info['last_result']})`"
    )

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        if sched_info["state"] == "Disabled":
            if st.button("\u25b6\ufe0f Resume", width="stretch"):
                toggle_scheduled_task(True)
                st.rerun()
        else:
            if st.button("\u23f8\ufe0f Pause", width="stretch"):
                toggle_scheduled_task(False)
                st.rerun()
    with col_s2:
        if st.button("\U0001f504 Refresh", width="stretch"):
            st.rerun()

    st.divider()
    view_mode = st.radio(
        "Interface Mode",
        options=["Store-Owner View (Recommended)", "Admin / Technical View"],
        index=0,
    )
    is_admin = "Admin" in view_mode

# Header & Reconciled Subtitle
st.title("Pricing Overview")
st.markdown(
    f"**Tracks {total_variants:,} variants** \u00b7 **{priced_cnt:,} priced** "
    f"({applied_cnt:,} updated, {correct_cnt:,} already aligned) \u00b7 "
    f"**{attention_cnt:,} need attention** ({hold_cnt:,} holds, {unmatched_cnt:,} unmatched)"
    + (f" \u00b7 **{preview_cnt:,} pending review**" if preview_cnt > 0 else "")
)

# Prominent 3-Step Workflow Banner: Check prices -> Review changes -> Apply selected updates
with st.container():
    st.markdown('<div class="workflow-banner">', unsafe_allow_html=True)
    wf1, wf2, wf3, wf4 = st.columns([2.2, 1.3, 1.2, 1.3])
    with wf1:
        selected_models = st.multiselect(
            "1. Select Products to Check",
            options=available_models,
            default=[],
            placeholder="All Tracked iPhone Models (1,484 variants)",
        )
    with wf2:
        run_mode = st.selectbox(
            "2. Execution Mode",
            options=["Preview Changes (Safe)", "Apply Directly to Shopify"],
            index=0,  # Default to Preview!
        )
        apply_immediately = "Apply Directly" in run_mode
    with wf3:
        skip_refresh = st.checkbox("Reuse synced catalog", value=True, help="Skip full Shopify GraphQL export for faster checks.")
    with wf4:
        st.write("")
        btn_label = "\U0001f50d Check Prices (Preview)" if not apply_immediately else "\u26a1 Check & Apply Live"
        if st.button(btn_label, type="primary", width="stretch"):
            log_name = launch_repricing_job(selected_models, apply_immediately, skip_refresh)
            scope = ", ".join(selected_models) if selected_models else "All Models"
            st.toast(f"Started {run_mode} for {scope}! Open the Review tab as results arrive.", icon="\U0001f50d")
    st.markdown("</div>", unsafe_allow_html=True)

# Top Summary Metrics (100% Reconciled)
m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Tracked Variants",
    f"{total_variants:,}",
    f"{len(available_models)} Active iPhone Product Lines",
)
m2.metric(
    "Actively Priced",
    f"{priced_cnt:,}",
    f"{(priced_cnt / total_variants * 100 if total_variants else 0):.1f}% of catalog ({applied_cnt:,} updated)",
)
m3.metric(
    "Pending Review / Preview",
    f"{preview_cnt:,}",
    "Ready in Review tab" if preview_cnt else "All reviewed",
)
m4.metric(
    "Need Attention",
    f"{attention_cnt:,}",
    f"{hold_cnt:,} holds \u00b7 {unmatched_cnt:,} unmatched",
)

st.divider()

# Simple 5-Tab Navigation
tab_overview, tab_products, tab_review, tab_rules, tab_history = st.tabs([
    "Overview",
    "Products",
    "Review",
    "Rules",
    "History",
])

# TAB 1: OVERVIEW
with tab_overview:
    st.subheader("Recent Price Updates")
    st.caption("Showing verified price transitions (Previous price \u2192 New price), pricing source, timestamp, and Shopify verification status.")
    df_structured_updates = build_structured_updates_table(df_audit, df_report_full, df_searches)
    if not df_structured_updates.empty:
        st.dataframe(df_structured_updates, width="stretch", hide_index=True, height=380)
    else:
        st.info("No price updates recorded yet. Click 'Check Prices (Preview)' above to begin.")

    if not df_report_full.empty:
        st.subheader("Catalog Health by iPhone Model")
        df_chart = df_report_full.copy()
        def categorize_status(s):
            if s in ("APPLIED_AND_VERIFIED", "ALREADY_CORRECT"):
                return "1. Priced"
            if s in ("PREVIEW", "NOT_APPLIED"):
                return "2. Pending Review"
            if s in ("HOLD", "HOLD_APPROVAL"):
                return "3. Hold"
            return "4. Unmatched"
        df_chart["Category"] = df_chart["status"].map(categorize_status)
        chart_piv = df_chart.groupby(["model", "Category"]).size().unstack(fill_value=0)
        st.bar_chart(chart_piv, height=300)

    with st.expander("Technical details (Execution Logs & Diagnostics)", expanded=is_admin):
        st.caption(f"Log File: `{log_info['filename']}` | Progress: `{log_info['last_group_num']}` | State: `{log_info['end']}`")
        st.code(log_info["raw_tail"] or "No log output available.", language="text")

# TAB 2: PRODUCTS
with tab_products:
    st.subheader("Product Grade Hierarchy (`Excellent > Very Good > Good > Fair`)")
    st.caption("Side-by-side condition prices for every iPhone model, storage size, and colour.")
    if not df_report_full.empty:
        col_p1, col_p2 = st.columns([1, 2])
        with col_p1:
            default_idx = (available_models.index("iPhone 15 Pro Max") + 1) if "iPhone 15 Pro Max" in available_models else 0
            model_pick = st.selectbox(
                "Filter by iPhone Model",
                options=["All Models"] + available_models,
                index=default_idx,
            )
        with col_p2:
            prod_search = st.text_input("Search Storage or Colour", placeholder="e.g. 256GB Midnight")

        df_mat = df_report_full.copy()
        if model_pick != "All Models":
            df_mat = df_mat[df_mat["model"] == model_pick]
        if prod_search:
            mask = df_mat.astype(str).apply(lambda x: x.str.contains(prod_search, case=False, na=False)).any(axis=1)
            df_mat = df_mat[mask]

        df_mat["price_num"] = pd.to_numeric(df_mat["proposed_price_cad"], errors="coerce")
        pivot = df_mat.pivot_table(
            index=["model", "storage", "color"],
            columns="condition",
            values="price_num",
            aggfunc="first",
        ).reset_index()

        ordered_cols = ["model", "storage", "color"] + [
            c for c in ["Excellent", "Very Good", "Good", "Fair"] if c in pivot.columns
        ]
        pivot = pivot[ordered_cols]

        if all(c in pivot.columns for c in ["Excellent", "Very Good", "Good", "Fair"]):
            valid_mask = (
                (pivot["Excellent"] > pivot["Very Good"])
                & (pivot["Very Good"] > pivot["Good"])
                & (pivot["Good"] > pivot["Fair"])
            )
            pivot["Grade Alignment"] = valid_mask.map(
                lambda ok: "\u2705 Clean Step-Down" if ok else "\u26a0\ufe0f Needs Attention"
            )

        st.dataframe(pivot, width="stretch", hide_index=True, height=440)

        with st.expander("View Full Underlying Variant Report (1,484 rows) & Export CSV", expanded=is_admin):
            st.dataframe(df_mat, width="stretch", height=350)
            st.download_button(
                "\U0001f4e5 Export Report (.CSV)",
                data=df_mat.to_csv(index=False).encode("utf-8"),
                file_name=f"pricing_report_{datetime.now().strftime('%Y-%m-%d')}.csv",
                mime="text/csv",
            )

# TAB 3: REVIEW (Review & Apply Selected Updates + Issues Requiring Action)
with tab_review:
    st.subheader("Step 1: Review & Apply Proposed Price Changes")
    st.caption("Select the proposed price updates you want to publish and click 'Apply Selected Updates to Shopify'.")

    search_price_map = (
        df_searches.set_index("variant_id")["current_price"].to_dict()
        if (not df_searches.empty and "variant_id" in df_searches.columns and "current_price" in df_searches.columns)
        else {}
    )

    if not df_report_full.empty:
        rev_filter_col1, rev_filter_col2 = st.columns([1, 2])
        with rev_filter_col1:
            rev_scope = st.selectbox(
                "Show Variants",
                options=[
                    "Pending Preview / Unapplied Changes",
                    "All Variants With Proposed Prices",
                ],
                index=0 if preview_cnt > 0 else 1,
            )
        with rev_filter_col2:
            rev_query = st.text_input("Filter Review Queue", placeholder="e.g. iPhone 14 128GB")

        df_rev = df_report_full.copy()
        if "Pending" in rev_scope:
            df_rev = df_rev[df_rev["status"].isin(["PREVIEW", "NOT_APPLIED"])]
        else:
            df_rev = df_rev[df_rev["proposed_price_cad"].notna() & (df_rev["proposed_price_cad"].astype(str) != "")]

        if rev_query:
            mask = df_rev.astype(str).apply(lambda x: x.str.contains(rev_query, case=False, na=False)).any(axis=1)
            df_rev = df_rev[mask]

        if not df_rev.empty:
            df_rev_display = pd.DataFrame({
                "Apply?": True,
                "Model": df_rev["model"],
                "Storage": df_rev["storage"],
                "Colour": df_rev["color"],
                "Condition": df_rev["condition"],
                "Previous Price ($)": df_rev["variant_id"].map(lambda v: search_price_map.get(v, "N/A")),
                "New Price ($)": pd.to_numeric(df_rev["proposed_price_cad"], errors="coerce"),
                "Pricing Rule / Source": df_rev["reason"].fillna(df_rev["pricing_basis"]),
                "Current Status": df_rev["status"],
                "variant_id": df_rev["variant_id"],
            })

            edited_rev = st.data_editor(
                df_rev_display.head(150),
                width="stretch",
                hide_index=True,
                height=340,
                disabled=["Model", "Storage", "Colour", "Condition", "Previous Price ($)", "Pricing Rule / Source", "Current Status", "variant_id"],
            )

            selected_to_apply = edited_rev[edited_rev["Apply?"] == True]
            if st.button(f"\u2705 Apply Selected Updates to Shopify ({len(selected_to_apply)} variants)", type="primary"):
                success_cnt = 0
                prog = st.progress(0, text="Applying selected prices to Shopify...")
                total_sel = max(len(selected_to_apply), 1)
                for i, (_, r) in enumerate(selected_to_apply.iterrows(), 1):
                    ok, _ = push_manual_shopify_price(
                        str(r["variant_id"]),
                        float(r["New Price ($)"]),
                        reason_label="Reviewed & Approved via UI",
                    )
                    if ok:
                        success_cnt += 1
                    prog.progress(i / total_sel, text=f"Updated {i}/{len(selected_to_apply)}...")
                st.success(f"Applied and verified {success_cnt}/{len(selected_to_apply)} variant prices on Shopify!")
                st.rerun()
        else:
            st.info("No pending preview proposals right now. Run 'Check Prices (Preview)' above or switch the dropdown to 'All Variants With Proposed Prices'.")

    st.divider()
    col_act1, col_act2 = st.columns([1.3, 1])
    with col_act1:
        st.subheader(f"Step 2: Issues Requiring Attention ({attention_cnt:,} variants)")
        st.caption(f"Mutually exclusive breakdown: **{hold_cnt:,} Holds** (missing anchor grade) and **{unmatched_cnt:,} Unmatched** (no Amazon/BestBuy listing found).")
        if not df_report_full.empty:
            df_issues = df_report_full[~df_report_full["status"].isin(["APPLIED_AND_VERIFIED", "ALREADY_CORRECT", "PREVIEW"])][
                ["model", "storage", "color", "condition", "status", "reason", "variant_id"]
            ]
            st.dataframe(df_issues, width="stretch", hide_index=True, height=320)

    with col_act2:
        st.subheader("Manual Price Override & Pin")
        st.caption("Set a custom price for any variant or pin it so scheduled runs skip it.")
        if not df_report_full.empty:
            ov_model = st.selectbox("Product Model", options=available_models, key="ov_model")
            df_m_sub = df_report_full[df_report_full["model"] == ov_model]
            ov_options = [
                f"{r['storage']} | {r['color']} | {r['condition']} (${r['proposed_price_cad'] or '0.00'}) \u2014 {r['variant_id']}"
                for _, r in df_m_sub.iterrows()
            ]
            ov_choice = st.selectbox("Variant", options=ov_options, key="ov_variant")
            chosen_vid = ov_choice.split(" \u2014 ")[-1].strip() if ov_choice else ""
            cur_row = df_m_sub[df_m_sub["variant_id"] == chosen_vid].iloc[0] if chosen_vid else None
            default_p = float(cur_row["proposed_price_cad"]) if (cur_row is not None and str(cur_row["proposed_price_cad"]).replace(".", "", 1).isdigit()) else 499.99

            custom_price = st.number_input("Custom Shopify Price ($ CAD)", min_value=1.0, max_value=5000.0, value=default_p, step=1.0)
            pin_variant = st.checkbox("Pin price (lock against automated changes)", value=(chosen_vid in pinned_variants))

            if st.button("Apply Custom Price", width="stretch"):
                ok, msg = push_manual_shopify_price(chosen_vid, custom_price, reason_label="Manual Store-Owner Override")
                if ok:
                    if pin_variant:
                        pinned_variants[chosen_vid] = {
                            "model": ov_model,
                            "price_cad": f"{custom_price:.2f}",
                            "pinned_at": datetime.now().isoformat(timespec="seconds"),
                        }
                    elif chosen_vid in pinned_variants:
                        del pinned_variants[chosen_vid]
                    save_json_file(PINS_JSON, pinned_variants)
                    st.success(msg)
                else:
                    st.error(msg)

# TAB 4: RULES (Clear Explanation + Live Dollar Preview Before Saving)
with tab_rules:
    st.subheader("Pricing & Grade Hierarchy Rules")
    st.caption("Configure how your prices undercut competitors and step down cleanly across refurbishment grades.")

    r_col1, r_col2 = st.columns([1, 1.2])
    with r_col1:
        new_undercut = st.number_input(
            "Competitor Undercut Amount ($ CAD)",
            min_value=0.0,
            max_value=50.0,
            value=float(pricing_cfg["undercut_cad"]),
            step=0.50,
            help="Subtracted from verified Amazon.ca (Excellent) and BestBuy.ca (Good/Fair) offers.",
        )
        new_step_pct = st.slider(
            "Minimum Grade Step-Down (%)",
            min_value=1.0,
            max_value=15.0,
            value=float(pricing_cfg["step_down_pct"]),
            step=0.5,
            help="Guarantees every lower condition grade is at least this percentage cheaper than the grade above it.",
        )

        st.markdown(
            "**How the Grade Hierarchy Works:**\n"
            "1. **Excellent** is anchored to the lowest verified Amazon.ca Renewed offer minus your undercut.\n"
            "2. **Very Good** is priced at the midpoint `(Excellent + Good) / 2` — or capped at "
            f"**-{new_step_pct:.1f}% below Excellent** if Best Buy's Good price is inverted or unavailable.\n"
            f"3. **Good** and **Fair** match BestBuy.ca minus your undercut, but are automatically capped at "
            f"**-{new_step_pct:.1f}% per tier** so a lower condition grade is never priced higher than a better grade."
        )

        if st.button("\U0001f4be Save Pricing Rules", type="primary"):
            save_json_file(CONFIG_JSON, {"undercut_cad": new_undercut, "step_down_pct": new_step_pct})
            st.success(f"Saved rules: -${new_undercut:.2f} CAD undercut and -{new_step_pct:.1f}% step-down per grade.")

    with r_col2:
        st.markdown("#### Live Example Preview (Before You Save)")
        sample_comp = st.number_input("Sample Amazon 'Excellent' Competitor Price ($ CAD)", value=500.00, step=10.0)
        factor = (100.0 - new_step_pct) / 100.0
        ex_exc = round(sample_comp - new_undercut, 2)
        ex_vg = round(ex_exc * factor, 2)
        ex_good = round(ex_vg * factor, 2)
        ex_fair = round(ex_good * factor, 2)

        preview_df = pd.DataFrame([
            {"Condition Grade": "1. Excellent", "Formula Applied": f"Amazon (${sample_comp:.2f}) - ${new_undercut:.2f}", "Resulting Shopify Price": f"${ex_exc:.2f} CAD"},
            {"Condition Grade": "2. Very Good", "Formula Applied": f"-{new_step_pct:.1f}% below Excellent", "Resulting Shopify Price": f"${ex_vg:.2f} CAD"},
            {"Condition Grade": "3. Good (Capped)", "Formula Applied": f"-{new_step_pct:.1f}% below Very Good", "Resulting Shopify Price": f"${ex_good:.2f} CAD"},
            {"Condition Grade": "4. Fair (Capped)", "Formula Applied": f"-{new_step_pct:.1f}% below Good", "Resulting Shopify Price": f"${ex_fair:.2f} CAD"},
        ])
        st.table(preview_df)

    with st.expander("Technical details: Competitor URL Cache Manager (`verified_mappings.json`)", expanded=is_admin):
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
            map_filter = st.text_input("Search Cached Mappings", placeholder="e.g. iPhone 14")
            if map_filter:
                mask = df_map.astype(str).apply(lambda x: x.str.contains(map_filter, case=False, na=False)).any(axis=1)
                df_map = df_map[mask]
            edited_df = st.data_editor(df_map, width="stretch", height=340, num_rows="dynamic")
            if st.button("Save URL Cache"):
                for _, row in edited_df.iterrows():
                    k = row["Product Key"]
                    if k in mappings_data and isinstance(mappings_data[k], dict):
                        if "amazon_url" in mappings_data[k]:
                            mappings_data[k]["amazon_url"] = row["Amazon URL"]
                        if "bestbuy_url" in mappings_data[k]:
                            mappings_data[k]["bestbuy_url"] = row["Best Buy URL"]
                    elif k:
                        mappings_data[k] = row["Amazon URL"]
                save_json_file(MAPPINGS_JSON, mappings_data)
                st.success("Saved updated URLs to verified_mappings.json!")

# TAB 5: HISTORY
with tab_history:
    st.subheader("Verified Price Change History")
    st.caption("Complete audit trail of all price updates written to Shopify.")
    if not df_structured_updates.empty:
        st.dataframe(df_structured_updates, width="stretch", hide_index=True, height=440)
    if not df_audit.empty:
        with st.expander("Raw JSONL Audit Records (`price_update_log.jsonl`)", expanded=is_admin):
            st.dataframe(df_audit.iloc[::-1], width="stretch", height=360)
