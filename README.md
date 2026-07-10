# mrn-xapp-project

PHY/MAC metrics collection xApp for O-RAN, built for the MRN course at Politecnico di Milano (Project 1). It collects per-UE RSRP, uplink/downlink BER, uplink/downlink MCS and cell load from a gNB emulator and logs everything to CSV every 500 ms.

The RAN Service Model here is custom and not E2SM-KPM compliant: no ASN.1, just Protocol Buffers over UDP. That's on purpose, the goal of this project is to get the whole xApp/RIC/gNB loop working end to end, not to be standards compliant.

## What's in here

- `ran_messages.proto` - the protobuf schema shared by the xApp and the gNB emulator.
- `ran_metrics_xapp.py` - the xApp. Subscribes to every gNB connected to the RIC, polls every 500 ms, writes two CSVs.
- `gnb_message_handlers.c` / `gnb_message_handlers.h` - the gNB emulator side, decodes requests and answers with randomly generated but RSRP-consistent measurements.
- `visualize_ran_metrics.py` - turns the CSV output into plots (cell load over time, RSRP distribution, BER vs RSRP, MCS distribution).
- `report_final.pdf` - short write-up of the design choices.

## How it fits together

```
gNB emulator (C, protobuf-c)  <--UDP-->  near-RT RIC (unmodified)  <--E2AP-->  xApp (Python, protobuf)
                                                                                     |
                                                                                     v
                                                                          e2sm_data.csv / e2smue_data.csv
```

The xApp asks for four parameters (`GNB_ID`, `UE_LIST`, `GLOBAL_PRB_ALLOC`, `MAX_PRB`), the gNB answers with a snapshot of every connected UE plus the current PRB allocation, and the xApp appends everything to CSV with a UTC timestamp.

## Sample output

RSRP, BER and MCS are sampled from three signal-quality tiers (strong / medium / weak), so BER and RSRP end up clearly correlated:

![BER vs RSRP](/plots/ber_vs_rsrp.png)

## Known limitations

- Single cell, fixed-size UE fleet (no attach/detach simulation).
- BER and MCS are sampled independently from the same RSRP tier instead of being derived from a shared SNR estimate.
- Not interoperable with a standards-compliant near-RT RIC, this is a PoC service model, not E2SM-KPM.

## Author

Giorgio Messina, Telecommunication Engineering MSc, Politecnico di Milano

[giorgiomessina.eu](https://giorgiomessina.eu)
