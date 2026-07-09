"""
visualize_ran_metrics.py

Data-visualization tool for the CSV time series produced by the PHY/MAC
metrics-collection xApp (cell-load and per-UE measurement logs).

Column names are normalized on load, so the tool tolerates minor naming
variants (e.g. gNB/gnb_id, RSRP/rsrp_dbm) between different CSV exports.

Outputs a set of PNG figures plus a short textual summary to stdout.

Usage:
    python visualize_ran_metrics.py \
        --cell-load-csv e2sm_data.csv \
        --ue-metrics-csv e2smue_data.csv \
        --out-dir plots/
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless-safe backend, no display required
import matplotlib.pyplot as plt
import pandas as pd

# ---------------------------------------------------------------------------
# Column normalization
# ---------------------------------------------------------------------------

CELL_LOAD_ALIASES = {"gnb": "gnb_id", "allocated": "allocated_prb", "max": "max_prb"}
UE_METRICS_ALIASES = {"gnb": "gnb_id", "rsrp": "rsrp_dbm"}


def _load_csv(path: Path, aliases: dict) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.rename(columns=aliases)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def load_cell_load_data(path: Path) -> pd.DataFrame:
    return _load_csv(path, CELL_LOAD_ALIASES)


def load_ue_metrics_data(path: Path) -> pd.DataFrame:
    return _load_csv(path, UE_METRICS_ALIASES)


# ---------------------------------------------------------------------------
# Individual plots
# ---------------------------------------------------------------------------

def plot_cell_load_timeseries(cell_load: pd.DataFrame, out_dir: Path) -> Optional[Path]:
    if cell_load.empty:
        return None
    fig, ax = plt.subplots(figsize=(10, 4))
    for gnb_id, group in cell_load.groupby("gnb_id"):
        ax.plot(group["timestamp"], group["load"] * 100.0, label=f"gNB {gnb_id}", linewidth=1)
    ax.set_xlabel("Time")
    ax.set_ylabel("Cell load [%]")
    ax.set_title("Cell load over time (allocated / max PRBs)")
    ax.set_ylim(0, 105)
    ax.legend(loc="upper right", fontsize="small")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out_path = out_dir / "cell_load_timeseries.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_rsrp_distribution(ue_metrics: pd.DataFrame, out_dir: Path) -> Optional[Path]:
    if ue_metrics.empty or "rsrp_dbm" not in ue_metrics.columns:
        return None
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(ue_metrics["rsrp_dbm"].dropna(), bins=40, color="#3465a4", edgecolor="white")
    ax.set_xlabel("RSRP [dBm]")
    ax.set_ylabel("Sample count")
    ax.set_title("RSRP distribution across all UE reports")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out_path = out_dir / "rsrp_distribution.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_ber_vs_rsrp(ue_metrics: pd.DataFrame, out_dir: Path) -> Optional[Path]:
    required = {"rsrp_dbm", "ber_ul", "ber_dl"}
    if ue_metrics.empty or not required.issubset(ue_metrics.columns):
        return None
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.scatter(ue_metrics["rsrp_dbm"], ue_metrics["ber_dl"], s=4, alpha=0.25, label="BER DL", color="#4e9a06")
    ax.scatter(ue_metrics["rsrp_dbm"], ue_metrics["ber_ul"], s=4, alpha=0.25, label="BER UL", color="#cc0000")
    ax.set_yscale("log")
    ax.set_xlabel("RSRP [dBm]")
    ax.set_ylabel("BER (log scale)")
    ax.set_title("Bit Error Rate vs. RSRP")
    ax.legend(loc="upper right", fontsize="small")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    out_path = out_dir / "ber_vs_rsrp.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_mcs_distribution(ue_metrics: pd.DataFrame, out_dir: Path) -> Optional[Path]:
    required = {"mcs_ul", "mcs_dl"}
    if ue_metrics.empty or not required.issubset(ue_metrics.columns):
        return None
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    axes[0].hist(ue_metrics["mcs_dl"].dropna(), bins=range(0, 29), color="#5c3566", edgecolor="white")
    axes[0].set_title("MCS index - downlink")
    axes[1].hist(ue_metrics["mcs_ul"].dropna(), bins=range(0, 29), color="#ce5c00", edgecolor="white")
    axes[1].set_title("MCS index - uplink")
    for ax in axes:
        ax.set_xlabel("MCS index")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("Sample count")
    fig.tight_layout()
    out_path = out_dir / "mcs_distribution.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_summary_dashboard(cell_load: pd.DataFrame, ue_metrics: pd.DataFrame, out_dir: Path) -> Path:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))

    if not cell_load.empty:
        for gnb_id, group in cell_load.groupby("gnb_id"):
            axes[0, 0].plot(group["timestamp"], group["load"] * 100.0, label=f"gNB {gnb_id}", linewidth=1)
        axes[0, 0].set_title("Cell load [%]")
        axes[0, 0].legend(fontsize="x-small")
        axes[0, 0].set_ylim(0, 105)

    if "rsrp_dbm" in ue_metrics.columns:
        axes[0, 1].hist(ue_metrics["rsrp_dbm"].dropna(), bins=40, color="#3465a4")
        axes[0, 1].set_title("RSRP distribution [dBm]")

    if {"rsrp_dbm", "ber_dl"}.issubset(ue_metrics.columns):
        axes[1, 0].scatter(ue_metrics["rsrp_dbm"], ue_metrics["ber_dl"], s=3, alpha=0.2, color="#4e9a06")
        axes[1, 0].set_yscale("log")
        axes[1, 0].set_title("BER DL vs RSRP")

    if "mcs_dl" in ue_metrics.columns:
        axes[1, 1].hist(ue_metrics["mcs_dl"].dropna(), bins=range(0, 29), color="#5c3566")
        axes[1, 1].set_title("MCS DL distribution")

    for ax in axes.flat:
        ax.grid(alpha=0.3)

    fig.suptitle("RAN PHY/MAC metrics - summary dashboard")
    fig.tight_layout()
    out_path = out_dir / "summary_dashboard.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Textual summary
# ---------------------------------------------------------------------------

def print_summary(cell_load: pd.DataFrame, ue_metrics: pd.DataFrame) -> None:
    print("=== Cell load ===")
    if cell_load.empty:
        print("  (no data)")
    else:
        print(f"  samples        : {len(cell_load)}")
        print(f"  gNBs           : {sorted(cell_load['gnb_id'].unique().tolist())}")
        print(f"  avg load       : {cell_load['load'].mean() * 100:.1f}%")
        print(f"  max load       : {cell_load['load'].max() * 100:.1f}%")

    print("=== UE metrics ===")
    if ue_metrics.empty:
        print("  (no data)")
        return
    print(f"  samples        : {len(ue_metrics)}")
    print(f"  distinct UEs   : {ue_metrics['rnti'].nunique()}")
    if "rsrp_dbm" in ue_metrics.columns:
        print(f"  RSRP range     : {ue_metrics['rsrp_dbm'].min():.1f} .. {ue_metrics['rsrp_dbm'].max():.1f} dBm")
    if "ber_dl" in ue_metrics.columns:
        print(f"  avg BER DL/UL  : {ue_metrics['ber_dl'].mean():.4f} / {ue_metrics['ber_ul'].mean():.4f}")
    if "mcs_dl" in ue_metrics.columns:
        print(f"  avg MCS DL/UL  : {ue_metrics['mcs_dl'].mean():.1f} / {ue_metrics['mcs_ul'].mean():.1f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell-load-csv", type=Path, default=Path("e2sm_data.csv"))
    parser.add_argument("--ue-metrics-csv", type=Path, default=Path("e2smue_data.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("plots"))
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    cell_load = load_cell_load_data(args.cell_load_csv) if args.cell_load_csv.exists() else pd.DataFrame()
    ue_metrics = load_ue_metrics_data(args.ue_metrics_csv) if args.ue_metrics_csv.exists() else pd.DataFrame()

    print_summary(cell_load, ue_metrics)

    generated = [
        plot_cell_load_timeseries(cell_load, args.out_dir),
        plot_rsrp_distribution(ue_metrics, args.out_dir),
        plot_ber_vs_rsrp(ue_metrics, args.out_dir),
        plot_mcs_distribution(ue_metrics, args.out_dir),
        plot_summary_dashboard(cell_load, ue_metrics, args.out_dir),
    ]
    print("\nGenerated figures:")
    for path in generated:
        if path is not None:
            print(f"  {path}")


if __name__ == "__main__":
    main()
