"""
Connectivity analysis — telling "the data link died" apart from "the system died".

The production checks in check_anomaly.py answer one question: is this plant
producing less than it should? They cannot answer the question the owner
actually asks first, because both look identical in the CSVs — a plant whose
WiFi or monitoring dongle is offline records 0.0000 kWh every day, and so does a
plant whose inverter has failed.

This module separates them on the one signal that does differ:

  * A production fault leaves the plant REPORTING. Numbers keep arriving, they
    are just too low. That is a RED/ORANGE production alert.
  * A dead link leaves NOTHING arriving at all — either no rows (the cloud API
    has no data for us) or an unbroken run of exact zeros. After `stale_days`
    of that, the honest conclusion is "we have lost contact", not "the system
    is dead". The solar system is usually fine.

So a plant whose data has not advanced for `stale_days` days is classified
LINK DOWN, and every consumer reports it as a connectivity problem, explicitly
not as a production fault.

Shared by check_connectivity.py (the CONNECTIVITY alert + fleet counts),
check_anomaly.py (so a dead link is never dressed up as a RED production alert)
and generate_admin_summary.py (the three headline counts).

Plant keys come from the CSV filenames, exactly as check_anomaly.load_daily_series
returns them, so both see the same fleet.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import exclusions

# A plant is given this many days of silence before we call the link down.
# Two collection runs a day, so 3 days is ~6 consecutive misses — well past a
# transient API hiccup, and still inside the week a customer would notice.
DEFAULT_STALE_DAYS = 3

# Kinds of silence, most diagnostic first.
KIND_REPORTING = "reporting"
KIND_NO_ROWS = "no_rows"            # the cloud has no data for us at all
KIND_ZEROS_ONLY = "zeros_only"      # rows still arrive, every one is 0.0000
KIND_NEVER_REPORTED = "never_reported"  # nothing but zeros for its whole history

# What each kind means in plain words, for the emails and the logs.
KIND_MEANING = {
    KIND_REPORTING: "reporting normally",
    KIND_NO_ROWS: "no data rows at all — the portal has nothing for this plant",
    KIND_ZEROS_ONLY: "rows still arriving but every reading is 0.0000 kWh",
    KIND_NEVER_REPORTED: "no non-zero reading has ever been recorded",
}


def select_platform(plants_daily: Dict[str, Dict[date, float]],
                    platform: Optional[str]) -> Dict[str, Dict[date, float]]:
    """
    Keep only the plants belonging to `platform`.

    NOT the same as check_anomaly.load_daily_series(platform_filter=...), which
    globs "<platform>-*.csv". That works for DessMonitor, whose files carry a
    "dessmonitor-" prefix, but matches NOTHING for ShineMonitor, whose files
    carry no prefix at all — so asking it for "shinemonitor" silently returns an
    empty fleet and every count reads zero.

    Every other module in the repo (get_customer_plants, parse_alarm_files)
    instead treats ShineMonitor as "everything that is not DessMonitor". Do the
    same, so the fleet counts can never quietly become 0.
    """
    if platform == "dessmonitor":
        return {k: v for k, v in plants_daily.items() if k.startswith("dessmonitor-")}
    # ShineMonitor, or no platform given: everything that is not DessMonitor.
    return {k: v for k, v in plants_daily.items() if not k.startswith("dessmonitor-")}


@dataclass(frozen=True)
class LinkStatus:
    """What we know about one plant's data link."""
    plant_key: str
    kind: str
    link_down: bool
    days_stale: Optional[int]          # days since the last non-zero reading
    days_since_data: Optional[int]     # days since the last row of any value
    last_nonzero_date: Optional[date]
    last_data_date: Optional[date]
    stale_days: int

    @property
    def meaning(self) -> str:
        return KIND_MEANING.get(self.kind, self.kind)

    def as_dict(self) -> dict:
        return {
            "plant_key": self.plant_key,
            "kind": self.kind,
            "meaning": self.meaning,
            "link_down": self.link_down,
            "days_stale": self.days_stale,
            "days_since_data": self.days_since_data,
            "last_nonzero_date": str(self.last_nonzero_date) if self.last_nonzero_date else None,
            "last_data_date": str(self.last_data_date) if self.last_data_date else None,
            "stale_days_threshold": self.stale_days,
        }


@dataclass
class FleetStatus:
    """The whole fleet, split into the three buckets the owner asks about."""
    reporting: List[LinkStatus] = field(default_factory=list)
    link_down: List[LinkStatus] = field(default_factory=list)
    excluded: List[dict] = field(default_factory=list)
    stale_days: int = DEFAULT_STALE_DAYS

    @property
    def fleet_wide_outage(self) -> bool:
        """
        Every single monitored plant has gone quiet.

        That is almost never 20-odd customers unplugging their routers on the
        same day. It is our own collector, our credentials, or the manufacturer
        cloud. Reporting it as N separate customer link failures would send the
        engineer to the wrong place and burn every plant's alert budget on a
        fault that has nothing to do with them.
        """
        return len(self.link_down) > 1 and len(self.reporting) == 0

    @property
    def counts(self) -> dict:
        return {
            "reporting": len(self.reporting),
            "link_down": len(self.link_down),
            "excluded": len(self.excluded),
            # What the website may honestly claim is monitored: everything we
            # still watch, whether or not it is talking to us today.
            "monitored": len(self.reporting) + len(self.link_down),
        }

    def counts_lines(self) -> List[str]:
        """The three headline numbers, one per line, for an email or a log."""
        c = self.counts
        return [
            f"Plants reporting:        {c['reporting']:>3}",
            f"Plants with a dead link: {c['link_down']:>3}   (no data for {self.stale_days}+ days — connectivity, not a fault)",
            f"Plants excluded:         {c['excluded']:>3}   (deliberately off the platform)",
            f"Plants monitored:        {c['monitored']:>3}   (reporting + dead link)",
        ]

    def as_dict(self) -> dict:
        return {
            "stale_days": self.stale_days,
            "counts": self.counts,
            "reporting": [s.as_dict() for s in self.reporting],
            "link_down": [s.as_dict() for s in self.link_down],
            "excluded": self.excluded,
        }


def plant_link_status(plant_key: str,
                      daily: Dict[date, float],
                      today: date,
                      stale_days: int = DEFAULT_STALE_DAYS) -> LinkStatus:
    """
    Classify one plant's link from its daily kWh series.

    `daily` is {date: kwh}, as check_anomaly.load_daily_series produces. Rows in
    the future (a timezone edge on the collector) are ignored so they cannot
    make a dead plant look fresh.
    """
    dates = [d for d in daily if d <= today]
    nonzero_dates = [d for d in dates if daily.get(d, 0.0) > 0]

    last_data_date = max(dates) if dates else None
    last_nonzero_date = max(nonzero_dates) if nonzero_dates else None

    days_since_data = (today - last_data_date).days if last_data_date else None

    if last_nonzero_date:
        days_stale = (today - last_nonzero_date).days
    elif dates:
        # Never produced anything we have on record — measure from the first row
        # we hold, which is how long we have been watching it say nothing.
        days_stale = (today - min(dates)).days
    else:
        days_stale = None

    if days_stale is None:
        # No usable rows at all. We are not in contact, by definition.
        return LinkStatus(
            plant_key=plant_key, kind=KIND_NO_ROWS, link_down=True,
            days_stale=None, days_since_data=None,
            last_nonzero_date=None, last_data_date=None, stale_days=stale_days,
        )

    link_down = days_stale >= stale_days

    if not link_down:
        kind = KIND_REPORTING
    elif last_nonzero_date is None:
        kind = KIND_NEVER_REPORTED
    elif days_since_data is not None and days_since_data >= stale_days:
        # The rows themselves stopped: the failure is upstream of the panels.
        kind = KIND_NO_ROWS
    else:
        kind = KIND_ZEROS_ONLY

    return LinkStatus(
        plant_key=plant_key, kind=kind, link_down=link_down,
        days_stale=days_stale, days_since_data=days_since_data,
        last_nonzero_date=last_nonzero_date, last_data_date=last_data_date,
        stale_days=stale_days,
    )


def classify_fleet(plants_daily: Dict[str, Dict[date, float]],
                   today: date,
                   stale_days: int = DEFAULT_STALE_DAYS,
                   apply_exclusions: bool = True) -> FleetStatus:
    """
    Split every plant into reporting / link down / excluded.

    Excluded plants are taken out first: they are off the platform, so they are
    neither "reporting" nor "a problem to chase". An exclusion that matches no
    data file is still counted — the plant is off the platform whether or not
    its CSVs have aged out of the repo.
    """
    fleet = FleetStatus(stale_days=stale_days)
    matched_entries = set()

    def entry_id(e):
        return (e["plant_norm"], e["customer_norm"])

    for plant_key in sorted(plants_daily):
        entry = exclusions.match_for_plant(plant_key) if apply_exclusions else None
        if entry:
            matched_entries.add(entry_id(entry))
            fleet.excluded.append({
                "plant_key": plant_key,
                "reason": entry["reason"],
                "since": entry["since"],
                "in_data": True,
            })
            continue

        status = plant_link_status(plant_key, plants_daily[plant_key], today, stale_days)
        (fleet.link_down if status.link_down else fleet.reporting).append(status)

    if apply_exclusions:
        # Entries whose data files are gone still count as excluded plants.
        for entry in exclusions.entries():
            if entry_id(entry) in matched_entries:
                continue
            fleet.excluded.append({
                "plant_key": entry["plant"] or entry["customer"],
                "reason": entry["reason"],
                "since": entry["since"],
                "in_data": False,
            })

    fleet.link_down.sort(key=lambda s: (-(s.days_stale or 0), s.plant_key))
    return fleet
