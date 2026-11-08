"""
Federal Funds Matchmaker - staff-facing website (Person A's layout, wired to scoring output).

Reads:  {DATA_DIR}/scored/{agency}/latest.json   (written by scoring.py)
Writes: {DATA_DIR}/decisions/{agency}.json       (one entry added per Pursue/Maybe/Pass click)

Run locally:  streamlit run app.py
"""

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))

# Department shown in the menu -> agency ID used in file names
DEPARTMENTS = {
    "NCDHHS - Health and Human Services": "ncdhhs",
    "NCDIT - Information Technology": "ncdit",
    "NCDNCR - Natural and Cultural Resources": "ncdncr",
}

FLAG_LABELS = {
    "closing_soon": "Closing soon",
    "forecasted_watch": "Forecast (not open yet)",
    "stale_forecast": "Old forecast",
    "eligibility_unknown": "Eligibility unknown",
    "exclude_term": "Check: excluded term",
}

DECISIONS = ["Not reviewed", "Pursue", "Maybe", "Pass"]

st.set_page_config(page_title="Federal Funds Matchmaker", layout="wide")
st.title("Federal Funds Matchmaker")


# ---------- loading ----------

@st.cache_data
def load_scored(agency, file_time):
    """Read the scored file and flatten it into a table. file_time makes the cache refresh when the file changes."""
    with open(DATA_DIR / "scored" / agency / "latest.json", encoding="utf-8") as f:
        scored = json.load(f)
    rows = []
    for it in scored["items"]:
        info = it.get("decision_info") or {}
        rows.append({**it, **{
            "close_date": info.get("close_date"),
            "award_ceiling": info.get("award_ceiling"),
            "award_floor": info.get("award_floor"),
            "expected_awards": info.get("expected_awards"),
            "cost_sharing": info.get("cost_sharing"),
        }})
    df = pd.DataFrame(rows)
    df["close"] = pd.to_datetime(df["close_date"], errors="coerce")
    return scored, df


def load_decisions(agency):
    path = DATA_DIR / "decisions" / f"{agency}.json"
    if not path.exists():
        return {"agency_id": agency, "items": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_decision(decisions, opportunity_id):
    """Newest entry for this grant counts."""
    for entry in reversed(decisions["items"]):
        if entry["opportunity_id"] == opportunity_id:
            return entry["decision"]
    return "Not reviewed"


def save_decision(agency, row, widget_key):
    """Called when a decision button changes: add one entry, never edit old ones."""
    decisions = load_decisions(agency)
    decisions["items"].append({
        "opportunity_id": row["opportunity_id"],
        "decision": st.session_state[widget_key],
        "note": "",
        "decided_by": "staff",
        "decided_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "score_at_decision": row["score"],
    })
    path = DATA_DIR / "decisions" / f"{agency}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(decisions, f, indent=2)


def money(value):
    return f"${value:,.0f}" if pd.notna(value) and value else "Not stated"


def stated(value):
    return value if value not in (None, "", []) and not (isinstance(value, float) and pd.isna(value)) else "Not stated"


# ---------- sidebar ----------

with st.sidebar:
    dept_label = st.selectbox("Department", list(DEPARTMENTS))
    agency = DEPARTMENTS[dept_label]
    scored_path = DATA_DIR / "scored" / agency / "latest.json"
    if not scored_path.exists():
        st.error(f"No results yet for {agency}. Run: python scoring.py --data-dir {DATA_DIR} --agency {agency}")
        st.stop()
    scored, df = load_scored(agency, scored_path.stat().st_mtime)

    st.header("Filters")
    confidence = st.multiselect("Match confidence", ["High", "Medium", "Low"], default=["High", "Medium"])
    as_of = st.date_input("Deadlines as of", value=date.today())
    only_open = st.checkbox("Only open listings", value=True)
    within_days = st.slider("Closing within (days, 0 = any)", 0, 180, 0)
    show_filtered = st.checkbox("Also show grants filtered out (not eligible / closed)", value=False)
    funders = st.multiselect("Federal funders", sorted(df["agency"].dropna().unique()))
    sort = st.selectbox("Sort by", ["Best match", "Closing soonest", "Largest award"])

# ---------- header ----------

st.caption(f"Last refreshed: {scored['generated_at']} · {scored['counts']['total']:,} grants checked · "
           f"{scored['counts']['passing_filters']:,} open to state agencies")
if scored.get("mode") != "full":
    st.warning("AI matching was unavailable for this refresh; scores use keywords and funders only.")

# ---------- filtering ----------

view = df.copy()
if not show_filtered:
    view = view[view["passes_filters"]]
view = view[view["confidence"].isin(confidence)]
days_left = (view["close"] - pd.Timestamp(as_of)).dt.days
if only_open:
    view = view[view["close"].isna() | (days_left >= 0)]
    days_left = (view["close"] - pd.Timestamp(as_of)).dt.days
if within_days:
    view = view[days_left.between(0, within_days)]
if funders:
    view = view[view["agency"].isin(funders)]
q = st.text_input("Search", placeholder="Search title or summary")
if q:
    text = (view["title"].fillna("") + " " + view["summary"].fillna("")).str.lower()
    view = view[text.str.contains(q.lower(), regex=False)]

if sort == "Best match":
    view = view.sort_values("rank")
elif sort == "Closing soonest":
    view = view.sort_values("close", na_position="last")
else:
    view = view.sort_values("award_ceiling", ascending=False, na_position="last")

st.write(f"Grants ranked by fit with **{dept_label.split(' - ')[0]}** strategic goals. Staff make the final call.")
c1, c2, c3 = st.columns(3)
c1.metric("Matching grants", f"{len(view):,}")
c2.metric("High confidence", int((view["confidence"] == "High").sum()))
c3.metric("Closing within 30 days", int(((view["close"] - pd.Timestamp(as_of)).dt.days.between(0, 30)).sum()))
if len(view) and not (view["confidence"] == "High").any():
    st.info("No high-confidence matches for this department right now. Medium and low matches are shown for review.")

# ---------- results ----------

decisions = load_decisions(agency)

for _, r in view.head(25).iterrows():
    with st.container(border=True):
        left, right = st.columns([4, 1])
        with left:
            st.markdown(f"**#{int(r['rank'])} · {r['title']}**")
            if pd.notna(r["close"]):
                days = (r["close"] - pd.Timestamp(as_of)).days
                close_label = f"closes {r['close'].strftime('%b %d, %Y')} ({days} days)"
                if 0 <= days <= 14:
                    close_label = f":red[{close_label}]"
            else:
                close_label = "close date not stated"
            st.markdown(f"Score **{r['score']:.0f}** · {r['confidence']} confidence · "
                        f"Goal {stated(r['matched_goal_id'])}: {stated(r['matched_goal_title'])}")
            st.caption(f"{r['agency']} · {close_label} · award up to {money(r['award_ceiling'])}")
            tags = [FLAG_LABELS.get(f, f) for f in (r["flags"] or [])] + list(r["filter_reasons"] or [])
            if tags:
                st.caption("⚑ " + " · ".join(tags))

            with st.expander("Why this match, and details"):
                st.write("**Why it fits:**", stated(r.get("why_it_fits")) if r.get("why_it_fits") else r["reason"])
                st.write("**Summary:**", stated(r.get("summary")))
                also = r.get("also_relevant_to") or []
                if also:
                    confirmed = set(r.get("also_fits") or [])
                    st.write("**Also relevant to:**", ", ".join(
                        f"{a['goal_id']} ({a['strength']}{', confirmed' if a['goal_id'] in confirmed else ''})" for a in also))
                b = r["breakdown"]
                st.write("**Score breakdown:**", " · ".join(
                    f"{k} {v:.2f}" if v is not None else f"{k} n/a" for k, v in b.items()))
                if r["matched_keywords"]:
                    st.write("**Matched terms:**", ", ".join(r["matched_keywords"]))
                st.write("**Award:**", f"{money(r['award_floor'])} to {money(r['award_ceiling'])}",
                         "· **Expected awards:**", stated(r["expected_awards"]),
                         "· **Cost sharing:**", {True: "Yes", False: "No"}.get(r["cost_sharing"], "Not stated"))
                st.write("**Eligibility note:**", stated(r.get("eligibility_note")))
                st.write("**Risk:**", stated(r.get("risk_note")))
                if r["url"]:
                    st.link_button("View official listing", r["url"])
        with right:
            key = f"d_{agency}_{r['opportunity_id']}"
            current = latest_decision(decisions, r["opportunity_id"])
            st.radio("Decision", DECISIONS, index=DECISIONS.index(current), key=key,
                     label_visibility="collapsed",
                     on_change=save_decision, args=(agency, r.to_dict(), key))

# ---------- export ----------

export = view[["rank", "title", "agency", "score", "confidence", "matched_goal_id", "close_date", "award_ceiling", "url"]]
st.download_button("Export list (CSV)", export.to_csv(index=False), f"{agency}_matches.csv", "text/csv")
