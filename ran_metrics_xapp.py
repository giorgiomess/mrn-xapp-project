"""
ran_metrics_xapp.py

xApp that subscribes to the custom PHY/MAC RAN Service Model (see
``ran_messages.proto``) exposed by every gNB connected to the near-RT RIC,
polls the resulting E2 Indication messages, and persists two time series
to CSV:

  * ``e2sm_data.csv``    -- per-gNB cell load (allocated vs. max PRBs)
  * ``e2smue_data.csv``  -- per-UE PHY/MAC measurements (RSRP, BER, MCS)

The implementation is organized around a small set of focused pieces:
a dataclass-based configuration, a dedicated CSV writer class, typed
parsing helpers kept independent from the RIC transport layer, and
structured logging instead of scattered ``print`` calls.

Usage:
    python ran_metrics_xapp.py [--poll-interval 0.5] [--out-dir .]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, TextIO

# --- RIC / E2AP transport -----------------------------------------------
import src.e2ap_xapp as e2ap_xapp
from ricxappframe.e2ap.asn1 import IndicationMsg

# --- Generated custom E2SM protobuf messages ----------------------------
sys.path.append("oai-oran-protolib/builds/")
import ran_messages_pb2 as ransm

LOGGER = logging.getLogger("ran_metrics_xapp")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class XappConfig:
    """Runtime configuration for the metrics-collection xApp."""

    poll_interval_s: float = 0.5
    cell_load_csv: Path = Path("e2sm_data.csv")
    ue_metrics_csv: Path = Path("e2smue_data.csv")
    requested_params: Sequence[int] = field(
        default_factory=lambda: (
            ransm.GNB_ID,
            ransm.UE_LIST,
            ransm.GLOBAL_PRB_ALLOC,
            ransm.MAX_PRB,
        )
    )


# ---------------------------------------------------------------------------
# CSV export layer
# ---------------------------------------------------------------------------

class MetricsCsvWriter:
    """Owns the two output CSV files and knows how to append rows to them.

    Kept as a small, single-purpose class so the parsing logic below never
    has to know about file handles, headers or flushing.
    """

    CELL_LOAD_HEADER = ["timestamp", "gnb_id", "allocated_prb", "max_prb", "load"]
    UE_METRICS_HEADER = [
        "timestamp", "gnb_id", "rnti", "rsrp_dbm",
        "ber_ul", "ber_dl", "mcs_ul", "mcs_dl",
    ]

    def __init__(self, config: XappConfig):
        self._cell_load_file: TextIO = open(config.cell_load_csv, "w", newline="")
        self._ue_metrics_file: TextIO = open(config.ue_metrics_csv, "w", newline="")
        self._cell_load_writer = csv.writer(self._cell_load_file)
        self._ue_metrics_writer = csv.writer(self._ue_metrics_file)
        self._cell_load_writer.writerow(self.CELL_LOAD_HEADER)
        self._ue_metrics_writer.writerow(self.UE_METRICS_HEADER)

    def write_cell_load(self, timestamp: str, gnb_id: Any, allocated_prb: int, max_prb: int) -> None:
        load = allocated_prb / max_prb if max_prb else float("nan")
        self._cell_load_writer.writerow([timestamp, gnb_id, allocated_prb, max_prb, load])
        self._cell_load_file.flush()

    def write_ue_metrics(self, timestamp: str, gnb_id: Any, ue: "ransm.UeInfo") -> None:
        self._ue_metrics_writer.writerow([
            timestamp, gnb_id, ue.rnti, ue.rsrp_dbm,
            ue.ber_ul, ue.ber_dl, ue.mcs_ul, ue.mcs_dl,
        ])

    def flush_ue_metrics(self) -> None:
        self._ue_metrics_file.flush()

    def close(self) -> None:
        self._cell_load_file.close()
        self._ue_metrics_file.close()

    def __enter__(self) -> "MetricsCsvWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Indication parsing
# ---------------------------------------------------------------------------

@dataclass
class CellSnapshot:
    """Result of parsing a single RanIndicationResponse."""

    gnb_id: Optional[str] = None
    max_prb: Optional[int] = None
    allocated_prb: Optional[int] = None
    ue_list: Sequence["ransm.UeInfo"] = field(default_factory=list)

    @property
    def has_complete_load_data(self) -> bool:
        return self.max_prb is not None and self.allocated_prb is not None


def parse_indication_response(response: "ransm.RanIndicationResponse") -> CellSnapshot:
    """Extract gNB id, PRB load and per-UE metrics from a decoded response.

    Isolated from I/O and from the transport layer so it can be unit
    tested on a bare ``RanIndicationResponse`` message.
    """
    snapshot = CellSnapshot()
    for entry in response.param_map:
        param_name = ransm.RanParameterId.Name(entry.key)

        if param_name == "GNB_ID" and entry.HasField("string_value"):
            snapshot.gnb_id = entry.string_value
        elif param_name == "MAX_PRB":
            snapshot.max_prb = entry.int64_value
        elif param_name == "GLOBAL_PRB_ALLOC":
            snapshot.allocated_prb = entry.int64_value
        elif param_name == "UE_LIST":
            snapshot.ue_list = list(entry.ue_list.ue_info)
        else:
            LOGGER.debug("Ignoring unrequested parameter %s in indication", param_name)

    return snapshot


def build_indication_request(params: Sequence[int]) -> bytes:
    """Serialize a RanMessage(INDICATION_REQUEST) asking for ``params``."""
    message = ransm.RanMessage()
    message.msg_type = ransm.INDICATION_REQUEST
    message.indication_request.target_params.extend(params)
    return message.SerializeToString()


# ---------------------------------------------------------------------------
# xApp orchestration
# ---------------------------------------------------------------------------

class RanMetricsXapp:
    """Subscribes to every connected gNB and streams PHY/MAC metrics to CSV."""

    def __init__(self, config: XappConfig, csv_writer: MetricsCsvWriter):
        self.config = config
        self.csv_writer = csv_writer
        self.connector = e2ap_xapp.e2apXapp()

    def discover_gnbs(self) -> list:
        gnb_ids = self.connector.get_gnb_id_list()
        LOGGER.info("%d gNB(s) connected to the RIC: %s", len(gnb_ids), gnb_ids)
        return gnb_ids

    def subscribe_all(self, gnb_ids: Sequence[Any]) -> None:
        request_buffer = build_indication_request(self.config.requested_params)
        for gnb_id in gnb_ids:
            self.connector.send_e2ap_sub_request(request_buffer, gnb_id)
            LOGGER.debug("Subscription request sent to gNB %s", gnb_id)

    def _handle_indication(self, raw_message: dict) -> None:
        meid = raw_message.get("meid")
        LOGGER.info("RIC Indication received from gNB %s, decoding E2SM payload", meid)

        indication = IndicationMsg()
        indication.decode(raw_message["payload"])

        response = ransm.RanIndicationResponse()
        response.ParseFromString(indication.indication_message)

        timestamp = datetime.now(timezone.utc).isoformat()
        snapshot = parse_indication_response(response)

        for ue in snapshot.ue_list:
            self.csv_writer.write_ue_metrics(timestamp, snapshot.gnb_id, ue)
        self.csv_writer.flush_ue_metrics()

        if snapshot.has_complete_load_data:
            self.csv_writer.write_cell_load(
                timestamp, snapshot.gnb_id, snapshot.allocated_prb, snapshot.max_prb
            )

    def _drain_queue_once(self) -> None:
        messages = self.connector.get_queued_rx_message()
        if not messages:
            LOGGER.debug("No messages received while waiting")
            return

        LOGGER.debug("%d message(s) received while waiting", len(messages))
        for message in messages:
            if message["message type"] == self.connector.RIC_IND_RMR_ID:
                self._handle_indication(message)
            else:
                LOGGER.warning(
                    "Unrecognized E2AP message received from gNB %s", message.get("meid")
                )

    def run_forever(self) -> None:
        gnb_ids = self.discover_gnbs()
        self.subscribe_all(gnb_ids)

        LOGGER.info("Entering polling loop (interval=%.3fs)", self.config.poll_interval_s)
        while True:
            self._drain_queue_once()
            _sleep(self.config.poll_interval_s)


def _sleep(seconds: float) -> None:
    # Wrapped in its own function so tests can monkeypatch/mock the delay
    # without pulling in a hard dependency on `time.sleep` throughout.
    from time import sleep
    sleep(seconds)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-interval", type=float, default=0.5,
                         help="Polling interval in seconds (default: 0.5)")
    parser.add_argument("--out-dir", type=Path, default=Path("."),
                         help="Directory where the CSV files are written")
    parser.add_argument("-v", "--verbose", action="store_true",
                         help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    config = XappConfig(
        poll_interval_s=args.poll_interval,
        cell_load_csv=args.out_dir / "e2sm_data.csv",
        ue_metrics_csv=args.out_dir / "e2smue_data.csv",
    )

    with MetricsCsvWriter(config) as csv_writer:
        xapp = RanMetricsXapp(config, csv_writer)
        xapp.run_forever()


if __name__ == "__main__":
    main()
