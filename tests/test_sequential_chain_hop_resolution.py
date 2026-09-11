import pytest
import tempfile
import os
from meshcore_tray.storage import Storage, NodeContact


def test_sequential_hop_chain_prioritises_last_repeater_heard_in_chain():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)

        # 1. Hop 0 candidate with unique prefix "cb"
        # Silverdale ST5 located in Lancashire (lat: 54.15, lon: -2.8)
        storage.save_contact(NodeContact("cb1122334455", "Silverdale ST5", latitude=54.15, longitude=-2.80, is_repeater=True))

        # 2. Hop 1 has duplicate prefix "a4":
        # Candidate A: Linux-Lad in Cumbria (lat: 54.20, lon: -2.85) -> Very close to Silverdale ST5 (~6.5 km)
        # Candidate B: Beeley Solar in Derbyshire (lat: 53.20, lon: -1.60) -> Distant from Silverdale ST5 (~130 km), but closer to a southern home
        storage.save_contact(NodeContact("a45479d85a2f", "Linux-Lad", latitude=54.20, longitude=-2.85, is_repeater=True))
        storage.save_contact(NodeContact("a4710b381506", "Beeley Solar", latitude=53.20, longitude=-1.60, is_repeater=True))

        # If we tested with home station in the south (e.g. 53.10, -1.50):
        # Old logic: Beeley Solar would win because it was closest to home (53.10, -1.50)!
        # New logic: Linux-Lad MUST win because the last repeater heard in the chain was Silverdale ST5 (54.15, -2.80)!
        results = storage.resolve_hop_chain_with_candidates(
            ["cb", "a4"],
            sender_coord=(54.10, -2.75),
            home_coord=(53.10, -1.50), # Southern home location
            user_station_prefix="M7NCY"
        )

        assert len(results) == 2
        # Hop 0 is Silverdale ST5
        assert results[0]["contact"].alias == "Silverdale ST5"
        # Hop 1 candidate chosen is Linux-Lad because it's closest to Silverdale ST5!
        assert results[1]["contact"].alias == "Linux-Lad"
        assert results[1]["is_ambiguous"] is True
        # Verify candidate list has Linux-Lad first with distance ~6-7 km
        assert results[1]["candidates"][0]["alias"] == "Linux-Lad"
        assert results[1]["candidates"][0]["dist_km"] < 15.0
        assert results[1]["candidates"][1]["alias"] == "Beeley Solar"
        assert results[1]["candidates"][1]["dist_km"] > 100.0

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_hop_chain_skips_repeater_without_gps_to_last_repeater_with_gps():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)

        # Hop 0: Silverdale ST5 (has GPS: 54.15, -2.80)
        storage.save_contact(NodeContact("cb1122334455", "Silverdale ST5", latitude=54.15, longitude=-2.80, is_repeater=True))

        # Hop 1: Winston RPTR (KNOWN REPEATER, BUT NO GPS!)
        storage.save_contact(NodeContact("9f2233445566", "Winston RPTR", latitude=None, longitude=None, is_repeater=True))

        # Hop 2 has duplicate prefix "a4":
        # Candidate A: Linux-Lad (lat: 54.20, lon: -2.85) -> Closest to Silverdale ST5 (~6.5 km)
        # Candidate B: Beeley Solar (lat: 53.20, lon: -1.60) -> Distant from Silverdale ST5 (~130 km), but closer to southern home
        storage.save_contact(NodeContact("a45479d85a2f", "Linux-Lad", latitude=54.20, longitude=-2.85, is_repeater=True))
        storage.save_contact(NodeContact("a4710b381506", "Beeley Solar", latitude=53.20, longitude=-1.60, is_repeater=True))

        # Hop 1 has no GPS, so Hop 2 MUST fall back to the last repeater in the chain that has GPS (Hop 0: Silverdale ST5)
        results = storage.resolve_hop_chain_with_candidates(
            ["cb", "9f", "a4"],
            sender_coord=None,
            home_coord=(53.10, -1.50), # Southern home
            user_station_prefix="M7NCY"
        )

        assert len(results) == 3
        assert results[0]["contact"].alias == "Silverdale ST5"
        assert results[1]["contact"].alias == "Winston RPTR"
        assert results[1]["contact"].latitude is None

        # Hop 2 successfully resolved against Silverdale ST5 (last repeater with GPS in the chain)
        assert results[2]["contact"].alias == "Linux-Lad"
        assert results[2]["is_ambiguous"] is True
        assert results[2]["ref_name"] == "Silverdale ST5"
        assert results[2]["candidates"][0]["alias"] == "Linux-Lad"
        assert results[2]["candidates"][0]["dist_km"] < 15.0

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
