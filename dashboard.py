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
    page_icon="\u26a1",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
REPORT_CSV = BASE_DIR / "catalogue_report.csv"
SEARCHES_CSV = BASE_DIR / "competitor_searches.csv"
AUDIT_JSONL = BASE_DIR / "price_update_log.jsonl"
MAPPINGS_JSON = BASE_DIR / "verified_mappings.json"
TARGETS_JSON = BASE_DIR / "approved_targets.json"
TASK_NAME = "Tech Genius Shopify Repricer"

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


def get_task_status() -> str:
    if os.name != "nt":
        return "Unknown"
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop).State"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = res.stdout.strip()
        return state if state else "Not Found"
    except Exception:
        return "Unknown"


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
            "end": "In Progress / Not Finished",
            "last_group_num": "0 / 329",
            "last_group_name": "N/A",
            "updated_count": 0,
            "capped_count": 0,
            "holds_count": 0,
            "recent_updates": [],
            "raw_tail": "",
        }
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start_line = next((l for l in lines if "=== Starting" in l or "=== 1." in l or "=== 2." in l), "Started")
    end_line = next((l for l in reversed(lines) if "=== Run Finished" in l), "Currently Running")

    current_product = ""
    updates = []
    caps = []
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
            updates.append(f"{current_product} \u2014 {detail}" if current_product else detail)
        elif " CAP]" in l:
            caps.append(l.strip())
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
        "capped_count": len(caps),
        "holds_count": len(holds),
        "recent_updates": updates[-25:][::-1],
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


def load_json_file(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def launch_repricing_job(selected_models, apply_changes: bool, skip_refresh: bool):
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    log_file = LOGS_DIR / f"run_{ts}.log"

    runner_script = BASE_DIR / "_ui_runner_job.py"
    models_repr = repr(selected_models)
    apply_flag = "--apply" if apply_changes else ""
    skip_flag = "--skip-catalog-refresh" if skip_refresh else ""

    script_code = f'''import subprocess, sys
import pandas as pd
from pathlib import Path

folder = Path({repr(str(BASE_DIR))})
report_csv = folder / "catalogue_report.csv"
backup_csv = folder / "catalogue_report_ui_backup.csv"
models = {models_repr}
log_path = Path({repr(str(log_file))})

other_df = None
if models and report_csv.exists():
    full_df = pd.read_csv(report_csv)
    other_df = full_df[~full_df["model"].isin(models)]
    other_df.to_csv(backup_csv, index=False)

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

if other_df is not None and report_csv.exists():
    new_df = pd.read_csv(report_csv)
    merged = pd.concat([other_df, new_df], ignore_index=True)
    merged.to_csv(report_csv, index=False)
    if backup_csv.exists():
        backup_csv.unlink()
    print(f"\\nMerged targeted models into catalogue_report.csv (Total rows: {{len(merged)}})")
'''
    runner_script.write_text(script_code, encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "-u", str(runner_script)],
        cwd=str(BASE_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )
    return log_file.name


df_report_full = pd.read_csv(REPORT_CSV) if REPORT_CSV.exists() else pd.DataFrame()
available_models = sorted(df_report_full["model"].dropna().unique().tolist()) if not df_report_full.empty else []

with st.sidebar:
    st.markdown("## \u26a1 Tech Genius AI")
    st.caption("Automated Multi-Grade Shopify Repricing Suite")
    st.divider()

    latest_log = get_latest_log()
    log_info = parse_log_summary(latest_log)
    task_state = get_task_status()

    st.markdown("### \u23f0 Automation Schedule")
    if task_state == "Disabled":
        st.warning("Paused (`Disabled` in Task Scheduler)")
        if st.button("\u25b6\ufe0f Resume 8AM / 8PM Schedule", width="stretch"):
            toggle_scheduled_task(True)
            st.rerun()
    elif task_state in ("Ready", "Running"):
        st.success(f"Active ({task_state}): 8:00 AM & 8:00 PM Daily")
        if st.button("\u23f8\ufe0f Pause Scheduled Runs", width="stretch"):
            toggle_scheduled_task(False)
            st.rerun()
    else:
        st.info(f"Scheduler State: `{task_state}`")

    st.caption(f"Latest Log: `{log_info['filename']}`")
    st.divider()

    st.markdown("### \U0001f680 Run Repricing Agent")
    selected_models = st.multiselect(
        "Target Models (leave empty for Full Catalogue)",
        options=available_models,
        default=[],
        placeholder="All Models (1,476 variants)",
    )
    apply_live = st.checkbox("Apply live price updates to Shopify", value=True)
    skip_refresh = st.checkbox("Fast start (reuse synced Shopify catalog)", value=True)

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("\U0001f504 Refresh UI", width="stretch"):
            st.rerun()
    with col_b:
        run_now = st.button("\U0001f680 Launch Run", type="primary", width="stretch")

    if run_now:
        log_name = launch_repricing_job(selected_models, apply_live, skip_refresh)
        scope = ", ".join(selected_models) if selected_models else "Full Catalogue"
        st.toast(f"Launched {scope} run! Logging to {log_name}", icon="\U0001f680")

    st.divider()
    st.markdown("### \U0001f6e1\ufe0f Active Pricing Rules")
    st.caption(
        "\u2022 **Excellent:** Amazon.ca (`-$3.00 CAD`)\n"
        "\u2022 **Very Good:** `(Good + Excellent) / 2` (capped `-5%` below Excellent)\n"
        "\u2022 **Good:** BestBuy.ca (`-$3.00 CAD`, capped `-5%` below Very Good)\n"
        "\u2022 **Fair:** BestBuy.ca (`-$3.00 CAD`, capped `-5%` below Good)"
    )

st.title("\u26a1 Automated Shopify Repricing Dashboard")
st.caption("Real-time competitor price intelligence across Amazon.ca & BestBuy.ca with 5% multi-grade step-down protection.")

mappings_data = load_json_file(MAPPINGS_JSON)

if not df_report_full.empty:
    status_counts = df_report_full["status"].value_counts().to_dict()
    total_variants = len(df_report_full)
    applied_cnt = status_counts.get("APPLIED_AND_VERIFIED", 0)
    correct_cnt = status_counts.get("ALREADY_CORRECT", 0)
    active_priced = applied_cnt + correct_cnt
    hold_cnt = status_counts.get("HOLD", 0)
    no_cand_cnt = status_counts.get("NO_CANDIDATE", 0)
    coverage_pct = (active_priced / total_variants * 100) if total_variants else 0.0
else:
    total_variants = active_priced = applied_cnt = correct_cnt = hold_cnt = no_cand_cnt = 0
    coverage_pct = 0.0

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Actively Priced on Shopify",
    f"{active_priced:,} / {total_variants:,}",
    f"{coverage_pct:.1f}% Store Coverage ({applied_cnt:,} Updated, {correct_cnt:,} Correct)",
)
k2.metric(
    "Safety Holds Applied",
    f"{hold_cnt:,}",
    "5% Grade Hierarchy Cap Active",
)
k3.metric(
    "Unmatched / No Candidate",
    f"{no_cand_cnt:,}",
    f"Last Run: {log_info['last_group_num']}",
)
k4.metric(
    "Cached Competitor URLs",
    f"{len(mappings_data):,}",
    "Amazon.ca & BestBuy.ca",
)

st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "\U0001f4ca Live Overview & Logs",
    "\U0001f4b2 Grade Hierarchy Matrix",
    "\U0001f4cb Full Catalogue Report",
    "\U0001f517 Competitor URL Manager",
    "\U0001f4dc Verified Audit History",
])

with tab1:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("\u2705 Latest Verified Shopify Updates")
        if log_info["recent_updates"]:
            df_recent = pd.DataFrame({"Product, Grade, Variant & Verified Shopify Price": log_info["recent_updates"]})
            st.dataframe(df_recent, width="stretch", hide_index=True, height=420)
        elif not df_report_full.empty:
            df_applied = df_report_full[df_report_full["status"] == "APPLIED_AND_VERIFIED"][
                ["model", "storage", "color", "condition", "proposed_price_cad", "pricing_basis"]
            ]
            st.dataframe(df_applied, width="stretch", hide_index=True, height=420)
        else:
            st.info("No variant updates recorded yet.")

    with c2:
        st.subheader("\U0001f5a5\ufe0f Live Execution Log Tail")
        st.code(log_info["raw_tail"] or "No log output available.", language="text")

with tab2:
    st.subheader("\U0001f4b2 Storefront Grade Hierarchy Matrix (`Excellent > Very Good > Good > Fair`)")
    st.caption("Inspect side-by-side condition pricing for every model, storage, and colour to verify clean 5% tier step-downs.")
    if not df_report_full.empty:
        col_m1, col_m2 = st.columns([1, 2])
        with col_m1:
            model_pick = st.selectbox(
                "Select iPhone Model",
                options=["All Models"] + available_models,
                index=(available_models.index("iPhone 14") + 1) if "iPhone 14" in available_models else 0,
            )
        df_mat = df_report_full.copy()
        if model_pick != "All Models":
            df_mat = df_mat[df_mat["model"] == model_pick]

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
            pivot["Hierarchy Check"] = valid_mask.map(
                lambda ok: "\u2705 Valid Step-Down" if ok else "\u26a0\ufe0f Missing / Unpriced Tier"
            )

        st.dataframe(pivot, width="stretch", hide_index=True, height=460)
    else:
        st.warning("`catalogue_report.csv` not found.")

with tab3:
    st.subheader("\U0001f4cb Full Catalogue Repricing Report")
    if not df_report_full.empty:
        f_col1, f_col2 = st.columns([2, 1])
        with f_col1:
            search_q = st.text_input(
                "\U0001f50d Filter by Model, Storage, Colour, or Condition Grade",
                placeholder="e.g. iPhone 14 128GB Midnight",
            )
        with f_col2:
            status_filter = st.multiselect(
                "Filter by Status",
                options=sorted(df_report_full["status"].dropna().unique().tolist()),
                default=[],
            )
        df_filtered = df_report_full.copy()
        if status_filter:
            df_filtered = df_filtered[df_filtered["status"].isin(status_filter)]
        if search_q:
            mask = df_filtered.astype(str).apply(lambda x: x.str.contains(search_q, case=False, na=False)).any(axis=1)
            df_filtered = df_filtered[mask]
        st.dataframe(df_filtered, width="stretch", height=480)
        st.download_button(
            "\U0001f4e5 Export Filtered Report (.CSV)",
            data=df_filtered.to_csv(index=False).encode("utf-8"),
            file_name=f"repricing_report_{datetime.now().strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
        )
    else:
        st.warning("`catalogue_report.csv` not found in the directory yet.")

with tab4:
    st.subheader("\U0001f517 Interactive Competitor URL Manager (`verified_mappings.json`)")
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
        map_filter = st.text_input("\U0001f50d Search Cached Mappings", placeholder="e.g. iPhone 14")
        if map_filter:
            mask = df_map.astype(str).apply(lambda x: x.str.contains(map_filter, case=False, na=False)).any(axis=1)
            df_map = df_map[mask]
        edited_df = st.data_editor(df_map, width="stretch", height=420, num_rows="dynamic")
        if st.button("\U0001f4be Save URL Changes to Cache", type="primary"):
            for _, row in edited_df.iterrows():
                k = row["Product Key"]
                if k in mappings_data and isinstance(mappings_data[k], dict):
                    if "amazon_url" in mappings_data[k]:
                        mappings_data[k]["amazon_url"] = row["Amazon URL"]
                    if "bestbuy_url" in mappings_data[k]:
                        mappings_data[k]["bestbuy_url"] = row["Best Buy URL"]
                elif k:
                    mappings_data[k] = row["Amazon URL"]
            MAPPINGS_JSON.write_text(json.dumps(mappings_data, indent=2), encoding="utf-8")
            st.success("Saved updated URLs to verified_mappings.json!")
    else:
        st.info("No mappings found in `verified_mappings.json`.")

with tab5:
    st.subheader("\U0001f4dc Historical Price Change Audit (`price_update_log.jsonl`)")
    df_audit = load_audit_log()
    if not df_audit.empty:
        st.dataframe(df_audit.iloc[::-1], width="stretch", height=480)
    else:
        st.info("No audit entries found in `price_update_log.jsonl` yet.")
