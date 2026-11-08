"""
Federal Funds Matchmaker - convert a Grants.gov CSV export into our data contract.

Input:  the CSV you download from simpler.grants.gov search results
Output: {data-dir}/opportunities/latest.json  (the file scoring.py reads)

Usage:
  python convert_csv.py newest_grants.csv --data-dir data
"""

import argparse
import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path


def text_or_none(value):
    """Blank cells become None (null in JSON), never an empty string."""
    value = (value or "").strip()
    return value if value else None


def number_or_none(value):
    """CSV numbers arrive as text ("36000000"); turn them into real numbers."""
    try:
        return float(value) if value else None
    except ValueError:
        return None


def true_false_or_none(value):
    """CSV says "True"/"False" as text. Text "False" would count as TRUE in Python, so convert it."""
    if value == "True":
        return True
    if value == "False":
        return False
    return None


def code_list(value):
    """'state_governments;county_governments' -> ['state governments', 'county governments']"""
    return [code.replace("_", " ") for code in (value or "").split(";") if code]


def days_until(close_date, today):
    if not close_date:
        return None
    return (date.fromisoformat(close_date[:10]) - today).days


def convert_row(row, today, now):
    """One CSV row -> one opportunity in our data contract."""
    close_date = text_or_none(row["close_date"])
    return {
        "id": row["opportunity_id"],
        "source": "grants.gov",
        "number": row["opportunity_number"],
        "title": row["opportunity_title"],
        "agency_code": row["agency_code"],
        "agency": row["agency_name"],
        "top_level_agency": row["top_level_agency_name"],
        "status": row["opportunity_status"],
        "open_date": text_or_none(row["post_date"]),
        "close_date": close_date,
        "forecasted_close_date": text_or_none(row["forecasted_close_date"]),
        "days_to_close": days_until(close_date, today),
        "eligibility": code_list(row["applicant_types"]),
        "eligibility_description": text_or_none(row["applicant_eligibility_description"]),
        "category": " ".join(code_list(row["funding_categories"])) or None,
        "award_ceiling": number_or_none(row["award_ceiling"]),
        "award_floor": number_or_none(row["award_floor"]),
        "estimated_total_funding": number_or_none(row["estimated_total_program_funding"]),
        "expected_awards": number_or_none(row["expected_number_of_awards"]),
        "cost_sharing": true_false_or_none(row["is_cost_sharing"]),
        "assistance_listings": [a for a in (row["opportunity_assistance_listings"] or "").split(";") if a],
        "description": text_or_none(row["summary_description"]),
        "url": row["url"],
        "ingested_at": now,
    }


def main():
    p = argparse.ArgumentParser(description="Convert a Grants.gov CSV export to opportunities/latest.json")
    p.add_argument("csv_file")
    p.add_argument("--data-dir", default="data")
    a = p.parse_args()

    today = date.today()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(a.csv_file, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    items, seen = [], set()
    for row in rows:
        if row["opportunity_id"] in seen:
            continue
        seen.add(row["opportunity_id"])
        items.append(convert_row(row, today, now))

    out_path = Path(a.data_dir) / "opportunities" / "latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": now, "source_file": Path(a.csv_file).name,
                   "count": len(items), "items": items}, f, indent=2, ensure_ascii=False)

    print(f"Converted {len(rows)} rows -> {len(items)} grants ({len(rows) - len(items)} duplicates removed)")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
