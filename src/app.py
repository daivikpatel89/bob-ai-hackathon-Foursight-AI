"""
app.py — FourSight AI · Military Vehicle HUMS Dashboard
========================================================
Reads the CSV produced by hums_gen.cpp and renders a Streamlit web dashboard
with a prioritized maintenance work-order list.

CSV schema (from hums_gen.cpp):
  timestamp, vehicle_id, engine_temp_c, vibration_mm_s, run_hours, status

Run:
    streamlit run src/app.py -- --csv data/hums_data.csv

The --csv flag is optional; the dashboard lets the operator upload or type a
path if it isn't supplied on the command line.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Thresholds — kept in sync with hums_gen.cpp constants
# ---------------------------------------------------------------------------

WARN_TEMP_C     = 105.0   # °C
FAULT_TEMP_C    = 120.0   # °C
WARN_VIBRATION  =   8.0   # mm/s
FAULT_VIBRATION =  14.0   # mm/s

# Run-hours threshold that triggers a mandatory scheduled-maintenance flag
RUN_HOURS_SERVICE_INTERVAL = 500.0   # hours between services

# Scoring weights (tunable)
WEIGHT_STATUS    = 0.50   # heaviest — discrete fault state
WEIGHT_TEMP      = 0.25   # continuous temperature excess
WEIGHT_VIBRATION = 0.25   # continuous vibration excess

# Priority bands derived from composite score (0–100)
PRIORITY_CRITICAL  = 75
PRIORITY_HIGH      = 50
PRIORITY_MODERATE  = 25

# ---------------------------------------------------------------------------
# Section 1 — CLI argument parsing (Streamlit forwards args after '--')
# ---------------------------------------------------------------------------

def _parse_cli_csv() -> Optional[str]:
    """Return the --csv path if supplied on the command line, else None."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--csv", default=None)
    # Streamlit passes its own flags before '--'; only parse what follows.
    try:
        args, _ = parser.parse_known_args(sys.argv[1:])
        return args.csv
    except SystemExit:
        return None


# ---------------------------------------------------------------------------
# Section 2 — Data loading & validation
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {
    "timestamp", "vehicle_id", "engine_temp_c",
    "vibration_mm_s", "run_hours", "status",
}


@st.cache_data(show_spinner="Loading HUMS data…")
def load_csv(path: str) -> pd.DataFrame:
    """Load and lightly validate the HUMS CSV file."""
    df = pd.read_csv(path, parse_dates=["timestamp"])

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        st.error(f"CSV is missing required columns: {missing}")
        st.stop()

    df["status"] = df["status"].str.upper().str.strip()
    df["engine_temp_c"]   = pd.to_numeric(df["engine_temp_c"],   errors="coerce")
    df["vibration_mm_s"]  = pd.to_numeric(df["vibration_mm_s"],  errors="coerce")
    df["run_hours"]        = pd.to_numeric(df["run_hours"],        errors="coerce")
    df = df.dropna(subset=["engine_temp_c", "vibration_mm_s", "run_hours"])

    return df


# ---------------------------------------------------------------------------
# Section 3 — Per-vehicle aggregation & composite risk scoring
# ---------------------------------------------------------------------------

STATUS_SCORE = {"NOMINAL": 0, "WARNING": 50, "FAULT": 100}


def _latest_status_score(statuses: pd.Series) -> float:
    """Numeric score for the most recent reading's status field."""
    last = statuses.iloc[-1] if not statuses.empty else "NOMINAL"
    return float(STATUS_SCORE.get(last, 0))


def _temp_excess_score(temps: pd.Series) -> float:
    """
    Normalised 0–100 score based on how far the latest temperature sits
    above the warning threshold.  Clamped to 100 at the fault threshold.
    """
    latest = float(temps.iloc[-1])
    if latest < WARN_TEMP_C:
        return 0.0
    return min(100.0, (latest - WARN_TEMP_C) / (FAULT_TEMP_C - WARN_TEMP_C) * 100.0)


def _vib_excess_score(vibs: pd.Series) -> float:
    """
    Normalised 0–100 score based on how far the latest vibration reading
    sits above the warning threshold.
    """
    latest = float(vibs.iloc[-1])
    if latest < WARN_VIBRATION:
        return 0.0
    return min(100.0, (latest - WARN_VIBRATION) / (FAULT_VIBRATION - WARN_VIBRATION) * 100.0)


def compute_work_orders(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate the raw HUMS rows into one work-order row per vehicle.

    Returns a DataFrame sorted by descending composite_score with columns:
      vehicle_id, latest_timestamp, latest_status,
      latest_temp_c, latest_vibration_mm_s, total_run_hours,
      fault_count, warning_count, service_due, composite_score, priority
    """
    df_sorted = df.sort_values("timestamp")
    groups = df_sorted.groupby("vehicle_id", sort=False)

    records = []
    for vid, grp in groups:
        latest = grp.iloc[-1]

        status_score = _latest_status_score(grp["status"])
        temp_score   = _temp_excess_score(grp["engine_temp_c"])
        vib_score    = _vib_excess_score(grp["vibration_mm_s"])

        composite = (
            WEIGHT_STATUS    * status_score
            + WEIGHT_TEMP      * temp_score
            + WEIGHT_VIBRATION * vib_score
        )

        # Scheduled service: flag when run_hours since last service exceeds interval.
        # Without explicit service log we flag when run_hours % interval < 10 h
        # OR when the vehicle has accumulated > interval hours.
        total_hours  = float(latest["run_hours"])
        service_due  = (total_hours % RUN_HOURS_SERVICE_INTERVAL) < 10.0 or \
                       total_hours >= RUN_HOURS_SERVICE_INTERVAL

        fault_count   = int((grp["status"] == "FAULT").sum())
        warning_count = int((grp["status"] == "WARNING").sum())

        records.append({
            "vehicle_id":               vid,
            "latest_timestamp":         latest["timestamp"],
            "latest_status":            latest["status"],
            "latest_temp_c":            round(float(latest["engine_temp_c"]),  2),
            "latest_vibration_mm_s":    round(float(latest["vibration_mm_s"]), 2),
            "total_run_hours":          round(total_hours, 1),
            "fault_count":              fault_count,
            "warning_count":            warning_count,
            "service_due":              service_due,
            "composite_score":          round(composite, 1),
        })

    wo = pd.DataFrame(records).sort_values("composite_score", ascending=False)
    wo = wo.reset_index(drop=True)
    wo.index += 1  # 1-based work-order rank

    # Assign human-readable priority band
    def _band(score: float) -> str:
        if score >= PRIORITY_CRITICAL:  return "🔴 CRITICAL"
        if score >= PRIORITY_HIGH:      return "🟠 HIGH"
        if score >= PRIORITY_MODERATE:  return "🟡 MODERATE"
        return "🟢 LOW"

    wo["priority"] = wo["composite_score"].apply(_band)
    return wo


# ---------------------------------------------------------------------------
# Section 4 — Streamlit UI helpers
# ---------------------------------------------------------------------------

STATUS_COLOURS = {
    "FAULT":   "#d62728",
    "WARNING": "#ff7f0e",
    "NOMINAL": "#2ca02c",
}

PRIORITY_COLOURS = {
    "🔴 CRITICAL": "#d62728",
    "🟠 HIGH":     "#ff7f0e",
    "🟡 MODERATE": "#f0c040",
    "🟢 LOW":      "#2ca02c",
}


def _colour_status(val: str) -> str:
    colour = STATUS_COLOURS.get(val, "#888")
    return f"color: {colour}; font-weight: bold"


def _colour_priority(val: str) -> str:
    colour = PRIORITY_COLOURS.get(val, "#888")
    return f"color: {colour}; font-weight: bold"


def _colour_score(val: float) -> str:
    if val >= PRIORITY_CRITICAL:  return "background-color: #ffd5d5"
    if val >= PRIORITY_HIGH:      return "background-color: #ffecd5"
    if val >= PRIORITY_MODERATE:  return "background-color: #fffbd5"
    return ""


def render_kpi_row(df: pd.DataFrame, wo: pd.DataFrame) -> None:
    total     = wo.shape[0]
    critical  = (wo["priority"] == "🔴 CRITICAL").sum()
    high      = (wo["priority"] == "🟠 HIGH").sum()
    faults    = (df["status"] == "FAULT").sum()
    warnings  = (df["status"] == "WARNING").sum()
    svc_due   = wo["service_due"].sum()

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Vehicles",         total)
    c2.metric("🔴 Critical",      int(critical))
    c3.metric("🟠 High",          int(high))
    c4.metric("FAULT readings",   int(faults))
    c5.metric("WARNING readings", int(warnings))
    c6.metric("Service Due",      int(svc_due))


def render_work_order_table(wo: pd.DataFrame) -> None:
    display_cols = [
        "priority", "vehicle_id", "latest_status",
        "latest_temp_c", "latest_vibration_mm_s",
        "total_run_hours", "fault_count", "warning_count",
        "service_due", "composite_score", "latest_timestamp",
    ]

    rename_map = {
        "priority":              "Priority",
        "vehicle_id":            "Vehicle",
        "latest_status":         "Status",
        "latest_temp_c":         "Temp (°C)",
        "latest_vibration_mm_s": "Vibration (mm/s)",
        "total_run_hours":       "Run Hours",
        "fault_count":           "# Faults",
        "warning_count":         "# Warnings",
        "service_due":           "Svc Due",
        "composite_score":       "Risk Score",
        "latest_timestamp":      "Last Reading",
    }

    table = wo[display_cols].rename(columns=rename_map)

    styled = (
        table.style
        .map(_colour_status,   subset=["Status"])
        .applymap(_colour_priority, subset=["Priority"])
        .applymap(_colour_score,    subset=["Risk Score"])
        .format({
            "Temp (°C)":        "{:.1f}",
            "Vibration (mm/s)": "{:.2f}",
            "Run Hours":        "{:.1f}",
            "Risk Score":       "{:.1f}",
        })
    )

    st.dataframe(styled, use_container_width=True, height=520)


def render_charts(df: pd.DataFrame, wo: pd.DataFrame) -> None:
    st.subheader("Fleet Overview")

    col_a, col_b = st.columns(2)

    # --- Status distribution bar chart ---
    with col_a:
        status_counts = df["status"].value_counts().reindex(
            ["FAULT", "WARNING", "NOMINAL"], fill_value=0
        )
        status_df = status_counts.reset_index()
        status_df.columns = ["Status", "Count"]
        st.bar_chart(status_df.set_index("Status"), color="#3b82d4")
        st.caption("All-time readings by status")

    # --- Risk score distribution ---
    with col_b:
        priority_counts = wo["priority"].value_counts().reset_index()
        priority_counts.columns = ["Priority", "Count"]
        st.bar_chart(priority_counts.set_index("Priority"), color="#7c5cd8")
        st.caption("Vehicles by priority band")

    # --- Temperature trend for top-5 worst vehicles ---
    st.subheader("Engine Temperature Trend — Top 5 Risk Vehicles")
    top5_ids = wo.head(5)["vehicle_id"].tolist()
    trend_df = (
        df[df["vehicle_id"].isin(top5_ids)]
        .sort_values("timestamp")
        .pivot_table(index="timestamp", columns="vehicle_id",
                     values="engine_temp_c", aggfunc="mean")
    )
    st.line_chart(trend_df)

    # --- Vibration trend for top-5 worst vehicles ---
    st.subheader("Vibration Trend — Top 5 Risk Vehicles")
    vib_df = (
        df[df["vehicle_id"].isin(top5_ids)]
        .sort_values("timestamp")
        .pivot_table(index="timestamp", columns="vehicle_id",
                     values="vibration_mm_s", aggfunc="mean")
    )
    st.line_chart(vib_df)


# ---------------------------------------------------------------------------
# Section 5 — Page layout & main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="FourSight AI — HUMS Dashboard",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ---- Sidebar ---------------------------------------------------------------
    with st.sidebar:
        st.title("🛡️ FourSight AI")
        st.caption("Military Vehicle Health & Usage Monitoring")
        st.divider()

        # Data source selection
        st.subheader("Data Source")
        cli_csv = _parse_cli_csv()
        if cli_csv:
            st.info(f"Using CLI path:\n`{cli_csv}`")
            csv_path: Optional[str] = cli_csv
        else:
            typed_path = st.text_input(
                "CSV file path", value="data/hums_data.csv",
                help="Path to the CSV generated by hums_gen.cpp"
            )
            uploaded = st.file_uploader(
                "…or upload CSV", type=["csv"],
                help="Upload a CSV file directly"
            )
            csv_path = None
            if uploaded is not None:
                # Write to a temp file so load_csv can cache by path
                tmp = Path("/tmp/hums_upload.csv")
                tmp.write_bytes(uploaded.read())
                csv_path = str(tmp)
            elif typed_path:
                csv_path = typed_path

        st.divider()

        # Filters
        st.subheader("Filters")
        min_score = st.slider(
            "Minimum risk score", min_value=0, max_value=100, value=0, step=5
        )
        show_svc_only = st.checkbox("Show service-due vehicles only", value=False)

        st.divider()
        st.caption("Thresholds")
        st.caption(f"Warn temp: {WARN_TEMP_C} °C | Fault temp: {FAULT_TEMP_C} °C")
        st.caption(f"Warn vib: {WARN_VIBRATION} mm/s | Fault vib: {FAULT_VIBRATION} mm/s")
        st.caption(f"Service interval: {RUN_HOURS_SERVICE_INTERVAL} h")

    # ---- Main content ----------------------------------------------------------
    st.title("🛡️ Military Vehicle HUMS — Maintenance Work-Order Dashboard")

    if not csv_path:
        st.info("Provide a CSV path in the sidebar or pass `--csv <path>` on the command line.")
        st.stop()

    if not Path(csv_path).exists():
        st.error(f"File not found: `{csv_path}`")
        st.stop()

    # Load data
    df = load_csv(csv_path)

    if df.empty:
        st.warning("The CSV file is empty or contained no parseable rows.")
        st.stop()

    # Compute work orders
    wo = compute_work_orders(df)

    # Apply sidebar filters
    wo_filtered = wo[wo["composite_score"] >= min_score]
    if show_svc_only:
        wo_filtered = wo_filtered[wo_filtered["service_due"]]

    # ---- KPI row ---------------------------------------------------------------
    st.subheader("Fleet Summary")
    render_kpi_row(df, wo)

    st.divider()

    # ---- Work-order table -------------------------------------------------------
    st.subheader(
        f"Prioritized Maintenance Work Orders"
        f"  — {len(wo_filtered)} vehicle(s)"
        + (" (filtered)" if len(wo_filtered) < len(wo) else "")
    )
    st.caption(
        "Sorted by **Risk Score** (0–100). "
        "Score = 50 % status + 25 % temperature excess + 25 % vibration excess."
    )

    if wo_filtered.empty:
        st.info("No vehicles match the current filter settings.")
    else:
        render_work_order_table(wo_filtered)

        # CSV export
        csv_bytes = wo_filtered.to_csv(index_label="rank").encode()
        st.download_button(
            label="⬇ Export work orders as CSV",
            data=csv_bytes,
            file_name="work_orders.csv",
            mime="text/csv",
        )

    st.divider()

    # ---- Charts -----------------------------------------------------------------
    render_charts(df, wo)

    # ---- Raw data explorer -------------------------------------------------------
    with st.expander("🔍 Raw HUMS data explorer"):
        vehicles = sorted(df["vehicle_id"].unique().tolist())
        selected = st.multiselect("Filter by vehicle", options=vehicles, default=[])
        raw_view = df[df["vehicle_id"].isin(selected)] if selected else df
        st.dataframe(raw_view.sort_values("timestamp", ascending=False),
                     use_container_width=True, height=300)

    st.caption(
        "FourSight AI · HUMS Dashboard · "
        f"Loaded {len(df):,} readings across {df['vehicle_id'].nunique()} vehicles "
        f"from `{Path(csv_path).name}`"
    )


if __name__ == "__main__":
    main()
