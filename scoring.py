"""
Federal Funds Matchmaker - scoring (Person C).

Three layers:
  1. Filters        - drop grants the agency can't use (not eligible, already closed).
                      Filtered grants are kept in the output with a reason, never deleted.
  2. Fit score      - 0-100: how well the grant matches the agency's strategic goals.
                      meaning match 65% + keyword overlap 25% + funder alignment 10%.
                      This is the only number we rank on and evaluate against hand labels.
  3. Decision info  - award size, competitiveness, cost sharing, deadline. Shown next to
                      the score, never mixed into it. Humans weigh these trade-offs.

Runs with or without Azure OpenAI:
  - With AOAI_ENDPOINT / AOAI_KEY set: embeddings for meaning match + AI summaries.
  - Without (or with --no-ai): rules only; weights are re-scaled over the signals we have.

Usage:
  python scoring.py --data-dir sample_data --agency ncdhhs
  python scoring.py --data-dir sample_data --agency ncdhhs --no-ai

Writes:
  {data-dir}/scored/{agency}/latest.json    the ranked list the website reads
  {data-dir}/scored/{agency}/baseline.json  keyword-count-only ranking (for evaluation)
"""

import argparse
import hashlib
import json
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

WEIGHTS = {"semantic": 0.65, "keyword": 0.25, "funder": 0.10}
CONFIDENCE = {"high": 70, "medium": 50}
KEYWORD_HITS_FOR_FULL_SCORE = 3
CLOSING_SOON_DAYS = 14
STALE_FORECAST_DAYS = 365
TOP_N_SUMMARIES = 15
APPLICANT_TYPES = ["state governments"]
RELATED_GOAL_MIN = 0.6         
RELATED_GOAL_STRONG = 0.8     
RELATED_MIN_KEYWORD_HITS = 2   
RELATED_GOALS_MAX = 3 

# ---------- small helpers ----------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_date(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def grant_text(opp):
    parts = [opp.get("title"), opp.get("description"), opp.get("category")]
    return " ".join(p for p in parts if p)


def goal_text(goal):
    return f"{goal.get('title', '')}. {goal.get('description', '')} Keywords: {', '.join(goal.get('keywords', []))}"


def contains_term(text, term):
    return re.search(r"\b" + re.escape(term.lower()) + r"\b", text.lower()) is not None


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------- layer 1: filters ----------

def apply_filters(opp, profile, today):
    """Returns (passes, reasons, flags)."""
    reasons, flags = [], []

    allowed = APPLICANT_TYPES
    eligibility = [e.lower() for e in (opp.get("eligibility") or [])]
    if not eligibility:
        flags.append("eligibility_unknown")
    elif not any(a in e or "unrestricted" in e for e in eligibility for a in allowed):
        reasons.append("not_eligible")

    close = parse_date(opp.get("close_date"))
    if close and close < today:
        reasons.append("closed")

    opened = parse_date(opp.get("open_date"))
    if opp.get("status") == "forecasted" and opened and (today - opened).days > STALE_FORECAST_DAYS:
        flags.append("stale_forecast")

    return (len(reasons) == 0), reasons, flags


# ---------- layer 2: fit score ----------

def keyword_signal(opp, profile):
    """Best goal by keyword hits. Score = hits / 3, capped at 1."""
    text = grant_text(opp)
    agency_terms = profile.get("keywords", []) + profile.get("program_areas", [])
    agency_hits = [t for t in agency_terms if contains_term(text, t)]

    best = {"goal": None, "hits": []}
    per_goal = []                                   #
    for goal in profile.get("goals", []):
        hits = [k for k in goal.get("keywords", []) if contains_term(text, k)]
        per_goal.append((goal, hits))               
        if len(hits) > len(best["hits"]):
            best = {"goal": goal, "hits": hits}

    all_hits = list({h.lower(): h for h in reversed(best["hits"] + agency_hits)}.values())[::-1]
    score = min(len(all_hits) / KEYWORD_HITS_FOR_FULL_SCORE, 1.0)
    return score, all_hits, best["goal"], per_goal

def funder_signal(opp, profile):
    code = (opp.get("agency_code") or "").upper()
    name = opp.get("agency") or ""
    for f in profile.get("preferred_funders", []):
        f = f.upper()
        if code == f or f in code.split("-") or contains_term(name, f):
            return 1.0
    return 0.0

def semantic_signals(opps, profile, client, embed_model, cache_path):
    cache = load_json(cache_path) if Path(cache_path).exists() else {}

    def embed_all(texts):
        missing = [t for t in texts if hashlib.sha256(t.encode()).hexdigest() not in cache]
        for i in range(0, len(missing), 100):
            batch = [t[:8000] for t in missing[i:i + 100]]
            resp = client.embeddings.create(model=embed_model, input=batch)
            for t, d in zip(missing[i:i + 100], resp.data):
                cache[hashlib.sha256(t.encode()).hexdigest()] = d.embedding
        return [cache[hashlib.sha256(t.encode()).hexdigest()] for t in texts]

    goals = profile.get("goals", [])
    goal_vecs = embed_all([goal_text(g) for g in goals])
    opp_vecs = embed_all([grant_text(o) for o in opps])
    save_json(cache_path, cache)

    all_sims = {}
    for opp, vec in zip(opps, opp_vecs):
        all_sims[opp["id"]] = [cosine(vec, gv) for gv in goal_vecs]

    best_raw = [max(sims) for sims in all_sims.values()]
    lo, hi = min(best_raw), max(best_raw)
    span = (hi - lo) or 1.0

    def scale(s):
        return min(max((s - lo) / span, 0.0), 1.0)

    out = {}
    for oid, sims in all_sims.items():
        best_i = max(range(len(sims)), key=lambda i: sims[i])
        out[oid] = {
            "score": scale(sims[best_i]),
            "raw": sims[best_i],
            "goal": goals[best_i],
            "goal_scores": [(g, scale(s), s) for g, s in zip(goals, sims)],
        }
    return out

def related_goals(best_goal, sem, per_goal_hits):
    """Other goals this grant also serves, beyond the best one."""
    best_id = best_goal["goal_id"] if best_goal else None
    related = []
    if sem:
        for goal, scaled, raw in sem["goal_scores"]:
            if goal["goal_id"] != best_id and scaled >= RELATED_GOAL_MIN:
                related.append({
                    "goal_id": goal["goal_id"], "goal_title": goal["title"],
                    "basis": "meaning", "similarity": round(scaled, 3),
                    "strength": "strong" if scaled >= RELATED_GOAL_STRONG else "moderate",
                    "_sort": scaled,
                })
    else:
        for goal, hits in per_goal_hits:
            if goal["goal_id"] != best_id and len(hits) >= RELATED_MIN_KEYWORD_HITS:
                related.append({
                    "goal_id": goal["goal_id"], "goal_title": goal["title"],
                    "basis": "keywords", "keyword_hits": hits,
                    "strength": "moderate", "_sort": len(hits),
                })
    related.sort(key=lambda r: -r["_sort"])
    for r in related:
        del r["_sort"]
    return related[:RELATED_GOALS_MAX]

def combine(signals):
    used = {k: WEIGHTS[k] for k, v in signals.items() if v is not None}
    total = sum(used.values())
    return round(100 * sum(signals[k] * w for k, w in used.items()) / total, 1)


def confidence(score):
    if score >= CONFIDENCE["high"]:
        return "High"
    if score >= CONFIDENCE["medium"]:
        return "Medium"
    return "Low"


def rule_reason(goal, hits, funder):
    parts = []
    if goal:
        parts.append(f"Closest goal: {goal['goal_id']} ({goal['title']})")
    if hits:
        parts.append("matched terms: " + ", ".join(hits[:5]))
    if funder:
        parts.append("funder usually supports this mission")
    return "; ".join(parts) + "." if parts else "No strong match to any goal."


# ---------- AI summaries (top N only) ----------

SUMMARY_PROMPT = """You help a North Carolina state grants coordinator triage federal grants.
Use ONLY the grant text below. If something is not in the text, write "not stated". Do not guess.

Main agency goal this grant was matched to:
{goal}

Other agency goals our matching says it may also serve:
{other_goals}

Grant:
Title: {title}
Funder: {agency}
Text: {text}

Reply in JSON with exactly these keys:
"summary": 2-3 plain sentences on what the grant funds and for whom,
"why_it_fits": one sentence linking the grant to the MAIN goal, or "weak fit" if it does not fit,
"also_fits": a list of goal IDs, chosen ONLY from the "other goals" above, that the grant text clearly supports; [] if none,
"eligibility_note": who can apply, per the text,
"risk_note": one requirement or risk that could make applying hard (e.g. cost sharing, tight deadline)."""


def ai_summary(client, chat_model, opp, goal, other_goals):
    others = "\n".join(f"- {g['goal_id']}: {goal_text(g)}" for g in other_goals) or "none"
    prompt = SUMMARY_PROMPT.format(
        goal=goal_text(goal) if goal else "not matched",
        other_goals=others,
        title=opp.get("title"), agency=opp.get("agency"),
        text=(opp.get("description") or "")[:6000],
    )
    resp = client.chat.completions.create(
        model=chat_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    result = json.loads(resp.choices[0].message.content)

    # Guard: keep only goal IDs we actually offered. The model may invent or misspell IDs.
    offered = {g["goal_id"] for g in other_goals}
    claimed = result.get("also_fits") or []
    result["also_fits"] = [gid for gid in claimed if gid in offered]
    dropped = [gid for gid in claimed if gid not in offered]
    if dropped:
        print(f"NOTE: model claimed goals we didn't offer for {opp.get('id')}: {dropped} (removed)")
    return result

# ---------- main ----------

def make_client(no_ai):
    if no_ai or not (os.getenv("AOAI_ENDPOINT") and os.getenv("AOAI_KEY")):
        return None
    from openai import AzureOpenAI
    return AzureOpenAI(azure_endpoint=os.environ["AOAI_ENDPOINT"],
                       api_key=os.environ["AOAI_KEY"], api_version="2024-10-21")


def score_agency(data_dir, agency, no_ai=False, top_n=TOP_N_SUMMARIES, today=None):
    data_dir = Path(data_dir)
    today = today or date.today()
    profile = load_json(data_dir / "profiles" / f"{agency}.json")
    opps = load_json(data_dir / "opportunities" / "latest.json")["items"]

    client = make_client(no_ai)
    embed_model = os.getenv("AOAI_EMBED_DEPLOYMENT", "embed")
    chat_model = os.getenv("AOAI_CHAT_DEPLOYMENT", "chat")

    semantic = {}
    if client:
        try:
            semantic = semantic_signals(opps, profile, client, embed_model,
                                        data_dir / "cache" / "embeddings.json")
        except Exception as e:
            print(f"WARNING: embeddings failed, continuing rules-only: {e}")

    items = []
    for opp in opps:
        passes, filter_reasons, flags = apply_filters(opp, profile, today)
        kw_score, hits, kw_goal, per_goal_hits = keyword_signal(opp, profile)
        fund = funder_signal(opp, profile)
        sem = semantic.get(opp["id"])
        goal = sem["goal"] if sem else kw_goal
        also = related_goals(goal, sem, per_goal_hits)

        signals = {"semantic": sem["score"] if sem else None, "keyword": kw_score, "funder": fund}        
        
        score = combine(signals)

        close = parse_date(opp.get("close_date"))
        days = (close - today).days if close else None
        if days is not None and 0 <= days <= CLOSING_SOON_DAYS:
            flags.append("closing_soon")
        if opp.get("status") == "forecasted":
            flags.append("forecasted_watch")
        if any(contains_term(grant_text(opp), t) for t in profile.get("exclude_keywords", [])):
            flags.append("exclude_term")

        items.append({
            "opportunity_id": opp["id"],
            "title": opp.get("title"),
            "agency": opp.get("agency"),
            "status": opp.get("status"),
            "url": opp.get("url"),
            "score": score,
            "confidence": confidence(score),
            "breakdown": {k: (round(v, 3) if v is not None else None) for k, v in signals.items()},
            "semantic_raw_cosine": round(sem["raw"], 4) if sem else None,
            "matched_goal_id": goal["goal_id"] if goal else None,
            "matched_goal_title": goal["title"] if goal else None,
            "also_relevant_to": also,
            "matched_keywords": hits,
            "reason": rule_reason(goal, hits, fund),
            "passes_filters": passes,
            "filter_reasons": filter_reasons,
            "flags": flags,
            "decision_info": {
                "close_date": opp.get("close_date"),
                "days_to_close": days,
                "award_ceiling": opp.get("award_ceiling"),
                "award_floor": opp.get("award_floor"),
                "estimated_total_funding": opp.get("estimated_total_funding"),
                "expected_awards": opp.get("expected_awards"),
                "cost_sharing": opp.get("cost_sharing"),
            },
            "summary": None, "why_it_fits": None, "also_fits": [], "eligibility_note": None, "risk_note": None,
            "_goal": goal, "_opp": opp,
            "_others": [g for g in profile.get("goals", [])
                        if g.get("goal_id") in {a["goal_id"] for a in also}],
        })

    # Rank: grants that pass filters first, then by fit score.
    items.sort(key=lambda x: (not x["passes_filters"], -x["score"]))
    for i, it in enumerate(items, 1):
        it["rank"] = i

    # AI summaries for the top N passing grants; fallback = first 300 characters.
    for it in [x for x in items if x["passes_filters"]][:top_n]:
        if client:
            try:
                it.update(ai_summary(client, chat_model, it["_opp"], it["_goal"], it["_others"]))
                continue
            except Exception as e:
                print(f"WARNING: summary failed for {it['opportunity_id']}: {e}")
        desc = it["_opp"].get("description") or ""
        it["summary"] = desc[:300] + ("..." if len(desc) > 300 else "") or None

    for it in items:
        del it["_goal"], it["_opp"], it["_others"]

    mode = "full" if semantic else "rules_only"
    out = {
        "agency_id": agency,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "weights": WEIGHTS,
        "confidence_thresholds": CONFIDENCE,
        "counts": {"total": len(items), "passing_filters": sum(i["passes_filters"] for i in items)},
        "items": items,
    }
    save_json(data_dir / "scored" / agency / "latest.json", out)

    # Baseline for evaluation: rank by raw keyword hit count only, no filters, no AI.
    baseline = sorted(
        ({"opportunity_id": it["opportunity_id"], "title": it["title"],
          "keyword_hits": len(it["matched_keywords"])} for it in items),
        key=lambda x: -x["keyword_hits"])
    for i, b in enumerate(baseline, 1):
        b["rank"] = i
    save_json(data_dir / "scored" / agency / "baseline.json",
              {"agency_id": agency, "method": "keyword hit count", "items": baseline})

    print(f"{agency}: scored {len(items)} grants ({out['counts']['passing_filters']} pass filters), mode={mode}")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Score grants against an agency profile.")
    p.add_argument("--data-dir", default="sample_data")
    p.add_argument("--agency", default="ncdhhs")
    p.add_argument("--no-ai", action="store_true", help="rules only, no Azure OpenAI calls")
    p.add_argument("--top-n", type=int, default=TOP_N_SUMMARIES)
    a = p.parse_args()
    score_agency(a.data_dir, a.agency, no_ai=a.no_ai, top_n=a.top_n)
