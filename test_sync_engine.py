from sync_engine import (
    apply_cargo_delta,
    cargo_from_edmc_state,
    cargo_from_journal_inventory,
    coerce_market_id,
    commander_in_srv,
    contributions_from_entry,
    extract_fc_market_ids,
    fc_cargo_diff_from_transfers,
    fc_diff_from_market_trade,
    is_fleet_carrier_station,
    market_demands_from_items,
    normalize_commodity_key,
    remaining_need_from_depot,
    ship_cargo_from_cargo_event,
    ship_delta_from_transfers,
)


def test_normalize_commodity_key_strips_journal_localization():
    assert normalize_commodity_key("$liquidoxygen_name;") == "liquidoxygen"
    assert normalize_commodity_key("LiquidOxygen") == "liquidoxygen"
    assert normalize_commodity_key("aluminium") == "aluminium"
    assert normalize_commodity_key("") == ""
    assert normalize_commodity_key(None) == ""


def test_coerce_market_id_rejects_zero_and_junk():
    assert coerce_market_id("3700000001") == 3700000001
    assert coerce_market_id(0) is None
    assert coerce_market_id("0") is None
    assert coerce_market_id(None) is None
    assert coerce_market_id("not-a-number") is None


def test_ship_to_carrier_transfer_increases_fc_cargo():
    transfers = [
        {"Type": "steel", "Count": 400, "Direction": "tocarrier"},
        {"Type": "$aluminium_name;", "Count": 72, "Direction": "tocarrier"},
    ]
    assert fc_cargo_diff_from_transfers(transfers, is_srv=False) == {
        "steel": 400,
        "aluminium": 72,
    }
    assert ship_delta_from_transfers(transfers) == {
        "steel": -400,
        "aluminium": -72,
    }


def test_carrier_to_ship_transfer_decreases_fc_cargo():
    transfers = [{"Type": "titanium", "Count": 50, "Direction": "toship"}]
    assert fc_cargo_diff_from_transfers(transfers, is_srv=False) == {"titanium": -50}
    assert ship_delta_from_transfers(transfers) == {"titanium": 50}


def test_mixed_transfers_net_per_commodity():
    transfers = [
        {"Type": "tea", "Count": 1, "Direction": "tocarrier"},
        {"Type": "gold", "Count": 1, "Direction": "toship"},
        {"Type": "tea", "Count": 2, "Direction": "toship"},
    ]
    assert fc_cargo_diff_from_transfers(transfers, is_srv=False) == {
        "tea": -1,
        "gold": -1,
    }


def test_srv_toship_is_cargo_onto_the_carrier():
    transfers = [{"Type": "grain", "Count": 8, "Direction": "toship"}]
    assert fc_cargo_diff_from_transfers(transfers, is_srv=True) == {"grain": 8}


def test_srv_tosrv_is_cargo_off_the_carrier():
    transfers = [{"Type": "grain", "Count": 2, "Direction": "tosrv"}]
    assert fc_cargo_diff_from_transfers(transfers, is_srv=True) == {"grain": -2}
    assert ship_delta_from_transfers(transfers) == {"grain": -2}


def test_ignores_invalid_transfer_rows():
    transfers = [
        {"Type": "steel", "Count": 0, "Direction": "tocarrier"},
        {"Type": "", "Count": 10, "Direction": "tocarrier"},
        {"Count": 10, "Direction": "tocarrier"},
        "not-a-dict",
        {"Type": "steel", "Count": "nope", "Direction": "tocarrier"},
    ]
    assert fc_cargo_diff_from_transfers(transfers) == {}
    assert fc_cargo_diff_from_transfers(None) == {}


def test_commander_in_srv_detects_buggy_shiptype():
    assert commander_in_srv({"ShipType": "testbuggy"}) is True
    assert commander_in_srv({"ShipType": "Type9"}) is False
    assert commander_in_srv(None) is False


def test_apply_cargo_delta_never_goes_negative():
    hold = {"steel": 10, "aluminium": 5}
    assert apply_cargo_delta(hold, {"steel": -4, "titanium": 3}) == {
        "steel": 6,
        "aluminium": 5,
        "titanium": 3,
    }
    assert apply_cargo_delta(hold, {"aluminium": -99}) == {"steel": 10}


def test_cargo_inventory_normalizes_localized_names():
    inventory = [
        {"Name": "$steel_name;", "Count": 200},
        {"Name": "aluminium", "Count": 16},
    ]
    assert cargo_from_journal_inventory(inventory) == {"steel": 200, "aluminium": 16}


def test_sparse_cargo_event_uses_edmc_state_instead_of_empty_hold():
    entry = {"event": "Cargo", "Count": 216, "Vessel": "Ship"}
    state = {"Cargo": {"$steel_name;": 200, "aluminium": 16}}
    cargo, confident = ship_cargo_from_cargo_event(entry, state)
    assert confident is True
    assert cargo == {"steel": 200, "aluminium": 16}


def test_sparse_cargo_without_state_is_not_confident():
    cargo, confident = ship_cargo_from_cargo_event({"Count": 50, "Inventory": []}, {})
    assert cargo == {}
    assert confident is False


def test_zero_count_cargo_event_is_authoritative_empty():
    cargo, confident = ship_cargo_from_cargo_event({"Count": 0}, {"Cargo": {"steel": 9}})
    assert cargo == {}
    assert confident is True


def test_full_inventory_wins_over_state():
    entry = {"Count": 5, "Inventory": [{"Name": "gold", "Count": 5}]}
    cargo, confident = ship_cargo_from_cargo_event(entry, {"Cargo": {"steel": 99}})
    assert confident is True
    assert cargo == {"gold": 5}


def test_contributions_from_colonisation_event():
    entry = {
        "event": "ColonisationContribution",
        "Contributions": [
            {"Name": "$steel_name;", "Name_Localised": "Steel", "Amount": 400},
            {"Name": "aluminium", "Amount": 72},
            {"Name": "titanium", "Amount": 0},
        ],
    }
    assert contributions_from_entry(entry) == {"steel": 400, "aluminium": 72}


def test_depot_remaining_includes_zeroed_commodities():
    entry = {
        "ResourcesRequired": [
            {"Name": "$steel_name;", "RequiredAmount": 1000, "ProvidedAmount": 400},
            {"Name": "aluminium", "RequiredAmount": 72, "ProvidedAmount": 72},
        ]
    }
    assert remaining_need_from_depot(entry) == {"steel": 600, "aluminium": 0}


def test_market_demands_use_normalized_keys():
    items = [
        {"name": "$steel_name;", "demand": 12},
        {"Name": "gold", "Demand": 0},
        {"name": "titanium", "demand": 3},
    ]
    assert market_demands_from_items(items) == {"steel": 12, "titanium": 3}


def test_fc_market_trade_signs():
    sell = {"Type": "$steel_name;", "Count": 20}
    buy = {"Type": "aluminium", "Count": 5}
    assert fc_diff_from_market_trade(sell, is_buy=False) == {"steel": 20}
    assert fc_diff_from_market_trade(buy, is_buy=True) == {"aluminium": -5}
    assert fc_diff_from_market_trade({"Type": "steel", "Count": 0}, is_buy=False) == {}


def test_extract_fc_market_ids_from_fc_all_and_active_projects():
    assert extract_fc_market_ids([{"marketId": 111}, {"MarketID": "222"}]) == {111, 222}
    active = [
        {"buildId": "abc", "linkedFC": [{"marketId": 333}, {"marketId": 0}]},
        {"linkedFC": [{"MarketID": "444"}]},
    ]
    assert extract_fc_market_ids(active) == {333, 444}
    assert extract_fc_market_ids({"fleetCarriers": [{"marketId": 555}]}) == {555}


def test_is_fleet_carrier_station():
    assert is_fleet_carrier_station("FleetCarrier") is True
    assert is_fleet_carrier_station("Coriolis") is False


def test_cargo_from_edmc_state_sums_duplicate_keys():
    state = {"Cargo": {"$Steel_name;": 10, "steel": 5}}
    assert cargo_from_edmc_state(state) == {"steel": 15}
