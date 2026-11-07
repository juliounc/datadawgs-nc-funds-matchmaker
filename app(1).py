import streamlit as st
import re
import pandas as pd
from datetime import date

st.set_page_config(page_title="Federal Funds Matchmaker", layout="wide")
st.title("Federal Funds Matchmaker")

## Dictionary of Goals and Keywords Mapping for Federal Funds Matchmaker
## Department -> Goal Area -> Keywords (From Each Strategic Plan)
Goals = {
    "NCDHHS": {"Behavioral Health and well-being": ["behavioural health", "mental health", "substance", "addiction", "recovery", "well-being"],
               "Rural Health" : ["rural health", "rural health care", "rural health workforce", "rural health infrastructure"],
               "Child and family": ["child welfare", "foster", "adoption", "family support", "parenting support", "child care"],
               "Workforce": ["workforce", "health professions", "nursing", "physician", "social worker", "public health workforce"],
               "Access to care": ["access to care", "healthcare access", "primary care", "specialty care", "telehealth"],
               "Public health": ["public health", "public health infrastructure", "public health workforce", "public health policy", "public health research"]},
    "NCDIT": {"Broadband and digital equity": ["broadband", "broadband infrastructure", "broadband access", "broadband deployment", "digital equity", "internet access", "digital literacy"],
              "Cybersecurity and privacy": ["cybersecurity", "cybersecurity workforce", "cybersecurity infrastructure", "cybersecurity policy", "cybersecurity training", "cybersecurity research", "cyber", "privacy", "data privacy"],
              "AI and emerging technology": ["artificial intelligence", "machine learning", "data analytics", "data science", "data governance", "data infrastructure", "emerging technology"],
              "Public safety comms": ["911", "public safety communications", "emergency communications", "first responders", "dispatch", "emergency response"],
              "Digital government and data": ["digital government", "digital government services", "digital transformation", "data governance", "data infrastructure", "open data"]},
    "DNCR": {"Preservation, archives, and libraries": ["preservation", "archives", "historical records", "cultural heritage", "library", "museum", "library services", "library infrastructure"],
             "Education": ["education", "educational programs", "educational infrastructure", "educational policy", "educational research"],
             "Preservation, archives, and libraries": ["preservation", "archives", "historical records", "cultural heritage", "library", "museum", "library services", "library infrastructure"],
             "Arts and culture": ["arts", "culture", "cultural heritage", "cultural programs", "cultural infrastructure", "cultural policy", "cultural research", "folk", "performing arts", "theater", "artists", "music", "visual arts"],
             "Helene recovery": ["Helene recovery", "disaster recovery", "recovery programs", "recovery infrastructure", "recovery policy", "recovery research"]},
}

## Loading CSV file
@st.cache_data
def load(src):
    df = pd.read_csv(src)
    df["close"] = pd.to_datetime(df["close_date"], errors="coerce")
    df["text"] = (df["opportunity_title"].fillna("") + " " + df["summary_description"].fillna("")).str.lower()
    df["state_ok"] = df["applicant_types"].fillna("").str.contains("state_governments") | (df["applicant_types"].fillna("") == "unrestricted")
    return df.drop_duplicates(["top_level_agency_name", "opportunity_title"])

with st.sidebar:
    upload = st.file_uploader("Upload CSV file", type="csv")
    if upload is None:
        st.info("Upload a grants CSV file to begin.")
        st.stop()
    df = load(upload)
    st.write(f"Loaded {len(df)} opportunities.")
    st.header("Filters")
    dept = st.selectbox("Department", list(Goals))
    selected_areas = st.multiselect("Areas", Goals[dept])
    as_of = st.date_input("Deadlines as of", value=date.today())
    only_open = st.checkbox("Only open listings", value=True)
    state_only = st.checkbox("State governments only", value=True)
    funders = st.multiselect("Federal Funders", sorted(df["top_level_agency_name"].dropna().unique()))
    sort = st.selectbox("Sort by", ["Closing soonest", "Largest award"])

## Tag and match each listing with goal areas (Whole-word, ex. "arts" doesn't match "parts")
def tag(text, dept):
    return [area for area, keywords in Goals[dept].items() if any(re.search(r"\b" + re.escape(keyword) + r"\b", text) for keyword in keywords)]

view = df.copy()
view["areas"] = view["text"].apply(lambda text: tag(text, dept))
view = view[view["areas"].apply(lambda areas: any(area in areas for area in selected_areas))]
if only_open:
    view = view[view["close"] >= pd.Timestamp(as_of)]
if state_only:
    view = view[view["state_ok"]]
if funders:
    view = view[view["top_level_agency_name"].isin(funders)]
q = st.text_input("Search", placeholder = "Search title or description")
if q:
    view = view[view["text"].str.contains(q.lower(), regex=False)]
view = view.sort_values("close") if sort == "Closing soonest" else view.sort_values("award_ceiling", ascending=False)

## Header and Results
st.write(f"Grants that fit **{dept}** goal areas. Staff make the final call")
c1, c2 = st.columns(2)
c1.metric("Matching grants", f"{len(view):,}")
c2.metric("Closing within 30 days", int(((view["close"] - pd.Timestamp(as_of)).dt.days.between(0, 30)).sum()))

st.session_state.setdefault("decisions", {})
for _, r in view.head(25).iterrows():
    with st.container(border = True):
        left, right = st.columns([4, 1])
        with left:
            st.markdown(f"**{r['opportunity_title']}**")
            st.caption(f"{r['top_level_agency_name']} · {r['opportunity_number']} · closes {r['close']:%b %d, %Y}")
            with st.expander("Read more"):
                st.write(str(r["summary_description"])[:600])
                st.link_button("View listing", r["url"])
        with right:
            key = r["opportunity_number"]
            st.session_state.decisions[key] = st.radio(
                "Decision", ["Not reviewed", "Pursue", "Maybe", "Pass"], key=f"d_{key}",
                horizontal = False, label_visibility = "collapsed")

file_name = "federal_funds.csv"
mime = "text/csv"
st.download_button("Export list(CSV)", view[["opportunity_title", "top_level_agency_name", "opportunity_number", "close_date", "award_ceiling", "url"]].to_csv(index=False), file_name, mime)
