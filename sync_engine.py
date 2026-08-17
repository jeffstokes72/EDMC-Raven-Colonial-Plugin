"""Pure journal/API mapping helpers for Raven Colonial cargo sync.

Commodity keys follow the Raven Colonial API: lowercase, language-agnostic
names such as ``liquidoxygen`` rather than ``$liquidoxygen_name;``.
Fleet Carrier cargo deltas follow SrvSurvey / Ravencolonial EDMC:

* Main ship ``tocarrier`` increases FC cargo; ``toship`` decreases it.
* SRV ``toship`` is cargo leaving the SRV toward the carrier; ``tosrv`` is
  cargo coming off the carrier into the SRV.
"""


def normalize_commodity_key(name):
    """Return the Raven Colonial commodity key for a journal/CAPI name."""
    if not name:
        return ""
    return str(name).replace("$", "").replace("_name;", "").replace("_name", "").strip().lower()


def coerce_market_id(value):
    """Parse a MarketID from journal/state, or return None if missing/invalid."""
    if value is None or value == "":
        return None
    try:
        mid = int(value)
    except (TypeError, ValueError):
        return None
    if mid == 0:
        return None
    return mid


def normalize_cargo_map(cargo):
    """Merge a cargo dict onto normalized commodity keys, summing quantities."""
    out = {}
    if not cargo:
        return out
    try:
        items = cargo.items()
    except AttributeError:
        return out
    for raw_key, raw_value in items:
        key = normalize_commodity_key(raw_key)
        if not key:
            continue
        try:
            amount = int(raw_value)
        except (TypeError, ValueError):
            continue
        if amount:
            out[key] = out.get(key, 0) + amount
    return out


def commander_in_srv(state):
    """True when EDMC state indicates the commander is driving an SRV."""
    if not state:
        return False
    ship_type = str(state.get("ShipType") or "").lower()
    return "buggy" in ship_type


def _transfer_count(transfer):
    try:
        count = int(transfer.get("Count", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0
    return count if count > 0 else 0


def fc_cargo_diff_from_transfers(transfers, is_srv=False):
    """Signed FC cargo delta from a CargoTransfer ``Transfers`` list.

    Positive values are added to the carrier hold; negative values are removed.
    """
    cargo_diff = {}
    if not isinstance(transfers, list):
        return cargo_diff
    for transfer in transfers:
        if not isinstance(transfer, dict):
            continue
        commodity = normalize_commodity_key(transfer.get("Type") or "")
        count = _transfer_count(transfer)
        if not commodity or not count:
            continue
        direction = str(transfer.get("Direction") or "").lower()
        toward_carrier = (is_srv and direction == "toship") or (not is_srv and direction == "tocarrier")
        from_carrier = (is_srv and direction == "tosrv") or (not is_srv and direction == "toship")
        if toward_carrier:
            cargo_diff[commodity] = cargo_diff.get(commodity, 0) + count
        elif from_carrier:
            cargo_diff[commodity] = cargo_diff.get(commodity, 0) - count
    return {key: value for key, value in cargo_diff.items() if value}


def ship_delta_from_transfers(transfers):
    """Signed ship-hold delta from CargoTransfer (carrier/SRV moves only)."""
    diff = {}
    if not isinstance(transfers, list):
        return diff
    for transfer in transfers:
        if not isinstance(transfer, dict):
            continue
        commodity = normalize_commodity_key(transfer.get("Type") or "")
        count = _transfer_count(transfer)
        if not commodity or not count:
            continue
        direction = str(transfer.get("Direction") or "").lower()
        if direction == "toship":
            diff[commodity] = diff.get(commodity, 0) + count
        elif direction in ("tocarrier", "tosrv"):
            diff[commodity] = diff.get(commodity, 0) - count
    return {key: value for key, value in diff.items() if value}


def apply_cargo_delta(cargo, delta):
    """Return a new cargo map after applying a signed delta (no negatives)."""
    out = dict(cargo or {})
    for raw_key, raw_value in (delta or {}).items():
        key = normalize_commodity_key(raw_key)
        if not key:
            continue
        try:
            amount = int(raw_value)
        except (TypeError, ValueError):
            continue
        new_qty = int(out.get(key, 0)) + amount
        if new_qty > 0:
            out[key] = new_qty
        else:
            out.pop(key, None)
    return out


def cargo_from_journal_inventory(inventory):
    """Build a normalized cargo map from a Cargo event Inventory list."""
    out = {}
    if not isinstance(inventory, list):
        return out
    for item in inventory:
        if not isinstance(item, dict):
            continue
        key = normalize_commodity_key(item.get("Name") or "")
        if not key:
            continue
        try:
            count = int(item.get("Count", 0) or 0)
        except (TypeError, ValueError):
            continue
        if count:
            out[key] = out.get(key, 0) + count
    return out


def cargo_from_edmc_state(state):
    """Build a normalized cargo map from EDMC ``state['Cargo']``."""
    if not state:
        return {}
    return normalize_cargo_map(state.get("Cargo") or {})


def ship_cargo_from_cargo_event(entry, state=None):
    """Resolve the current ship hold from a Cargo journal event.

    Full Inventory snapshots win. Sparse events (Count only; details in
    Cargo.json / EDMC state) fall back to ``state['Cargo']``. A Count of 0
    is an authoritative empty hold. Returns ``(cargo_dict, confident)``.
    """
    entry = entry or {}
    inventory = entry.get("Inventory")
    try:
        count = int(entry.get("Count", 0) or 0)
    except (TypeError, ValueError):
        count = 0

    if isinstance(inventory, list) and inventory:
        return cargo_from_journal_inventory(inventory), True
    if count == 0:
        return {}, True
    fallback = cargo_from_edmc_state(state)
    if fallback:
        return fallback, True
    return {}, False


def contributions_from_entry(entry):
    """Normalize ColonisationContribution ``Contributions`` into a cargo diff."""
    out = {}
    contributions = (entry or {}).get("Contributions") or []
    if not isinstance(contributions, list):
        return out
    for row in contributions:
        if not isinstance(row, dict):
            continue
        commodity = normalize_commodity_key(row.get("Name") or "")
        try:
            amount = int(row.get("Amount", 0) or 0)
        except (TypeError, ValueError):
            continue
        if commodity and amount > 0:
            out[commodity] = out.get(commodity, 0) + amount
    return out


def remaining_need_from_depot(entry):
    """Remaining construction need from ColonisationConstructionDepot.

    Includes zero-remaining commodities so fulfilled items can be cleared
    on the server instead of being left at a stale positive amount.
    """
    remaining = {}
    resources = (entry or {}).get("ResourcesRequired") or []
    if not isinstance(resources, list):
        return remaining
    for req in resources:
        if not isinstance(req, dict):
            continue
        name = normalize_commodity_key(req.get("Name") or "")
        if not name:
            continue
        try:
            required = int(req.get("RequiredAmount", 0) or 0)
        except (TypeError, ValueError):
            required = 0
        try:
            provided = int(req.get("ProvidedAmount", 0) or 0)
        except (TypeError, ValueError):
            provided = 0
        remaining[name] = max(0, required - provided)
    return remaining


def market_demands_from_items(items):
    """Remaining buy-demand from a Market.json / CAPI items list."""
    demands = {}
    if not isinstance(items, list):
        return demands
    for item in items:
        if not isinstance(item, dict):
            continue
        demand = item.get("demand")
        if demand is None:
            demand = item.get("Demand", 0)
        try:
            demand = int(demand or 0)
        except (TypeError, ValueError):
            continue
        if demand <= 0:
            continue
        name = normalize_commodity_key(item.get("name") or item.get("Name") or "")
        if name:
            demands[name] = demand
    return demands


def fc_diff_from_market_trade(entry, is_buy):
    """FC cargo delta for MarketBuy (leave FC) or MarketSell (enter FC)."""
    commodity = normalize_commodity_key((entry or {}).get("Type") or "")
    try:
        count = int((entry or {}).get("Count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    if not commodity or count <= 0:
        return {}
    return {commodity: -count if is_buy else count}


def extract_fc_market_ids(payload):
    """Collect MarketIDs from /fc/all, /active, or a linkedFC list."""
    ids = set()

    def add_fc(row):
        if isinstance(row, dict):
            mid = coerce_market_id(row.get("marketId") if row.get("marketId") is not None else row.get("MarketID"))
            if mid:
                ids.add(mid)
        else:
            mid = coerce_market_id(row)
            if mid:
                ids.add(mid)

    if payload is None:
        return ids
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and isinstance(row.get("linkedFC"), list):
                for fc in row["linkedFC"]:
                    add_fc(fc)
            else:
                add_fc(row)
        return ids
    if isinstance(payload, dict):
        for key in ("fleetCarriers", "fleetcarriers", "fcs", "linkedFC"):
            nested = payload.get(key)
            if nested is not None:
                ids.update(extract_fc_market_ids(nested))
        add_fc(payload)
    return ids


def is_fleet_carrier_station(station_type):
    return str(station_type or "") == "FleetCarrier"
