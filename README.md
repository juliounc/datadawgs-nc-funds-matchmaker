# Federal Funds Matchmaker

A prototype that helps North Carolina state agencies find federal grant opportunities that fit their strategic goals. It ranks every open Grants.gov opportunity against an agency's profile.

Built for the UNC Solve-a-thon (OSBM Federal Funds Matchmaker challenge), November 2026.

**Live demo:** https://app-ffm-web.whiteriver-d5ac87b1.eastus2.azurecontainerapps.io/

---

## How it works

```
Grants.gov CSV export
   → convert_csv.py   cleans it into data/opportunities/latest.json
   → scoring.py       scores every grant against each agency profile
                      → data/scored/{agency}/latest.json
   → app.py           Streamlit website: ranked list, explanations, decisions
                      → data/decisions/{agency}.json
```

Scoring has three layers:

1. **Filters (yes/no).** Grants a state agency can't apply for, or that have closed, are marked `passes_filters: false` and shown only on request. Uncertain cases (eligibility not listed, old forecasts) pass with a warning flag instead of being removed.
2. **Fit score (0–100).** A weighted average of:
   - **Meaning match, 65%:** Azure OpenAI embeddings (`text-embedding-3-small`) compare each grant's text to each agency goal (cosine similarity, min-max rescaled across the batch). The closest goal becomes the "matched goal."
   - **Keyword match, 25%:** whole-word matches of profile keywords in the grant text (3+ matches = full marks).
   - **Funder alignment, 10%:** the funder is one the agency usually works with.

   If Azure OpenAI is unavailable, the score uses keyword and funder only (weights re-scaled) and the output is labeled `mode: rules_only`.
3. **Decision info (shown, never scored).** Deadline, award size, expected number of awards, and cost sharing are displayed next to the score so staff weigh those trade-offs themselves.

For the top 15 grants per agency, `gpt-4.1-mini` writes a short summary, why it fits, eligibility and risk notes, and which other goals it also serves. The prompt restricts the model to the grant text ("not stated" instead of guessing), and the code discards any goal IDs the model claims that it wasn't offered. Every grant also gets a rule-based `reason` built only from measured facts.

## Repository contents

| File | Purpose |
|---|---|
| `convert_csv.py` | Converts a Grants.gov CSV export (simpler.grants.gov search download) into `data/opportunities/latest.json` |
| `scoring.py` | Filters, scores, ranks, and summarizes grants for one agency |
| `app.py` | Staff-facing Streamlit website |
| `data/profiles/*.json` | Agency profiles: goals, keywords, program areas, preferred funders, drawn from each agency's strategic plan |
| `data/opportunities/latest.json` | Cleaned grants |
| `data/scored/{agency}/latest.json` | Ranked results the website reads |
| `data/scored/{agency}/baseline.json` | Keyword-count-only ranking, used as the comparison in evaluation |
| `Dockerfile`, `start.sh`, `requirements.txt` | Container for Azure. `MODE=app` runs the website; `MODE=job` re-scores all agencies |
| `azure_setup_code` | Azure CLI commands used to create the resources |

## Running locally

```bash
pip install -r requirements.txt

# 1. Convert a fresh Grants.gov export
python convert_csv.py newest_grants.csv --data-dir data

# 2. Score each agency (set the two variables for AI matching; omit them, or add --no-ai, for rules-only)
export AOAI_ENDPOINT="https://<your-resource>.openai.azure.com/"
export AOAI_KEY="<your key>"          # never commit this
python scoring.py --data-dir data --agency ncdhhs
python scoring.py --data-dir data --agency ncdit
python scoring.py --data-dir data --agency ncdncr

# 3. Run the website
streamlit run app.py
```

Embeddings are cached in `data/cache/embeddings.json` (git-ignored), so re-runs only embed new or changed text. A full run for all three agencies costs roughly $0.10 in Azure OpenAI usage.

## Deploying to Azure

Resources (all in resource group `rg-ffm-team`, region `eastus2`): Blob Storage, Azure OpenAI (`embed` and `chat` deployments), Azure Container Registry, Container Apps environment, and the Container App `app-ffm-web`. See `azure_setup_code` for creation commands. To redeploy after changes:

```bash
az acr build -r <registry> -t ffm:v1 .
az containerapp update -n app-ffm-web -g rg-ffm-team --image <registry>.azurecr.io/ffm:v1
```

The website needs no AI key; it only reads pre-scored files.

## Adding an agency

1. Create `data/profiles/<agency_id>.json` with `goals` (each with `goal_id`, `title`, `description`, `keywords`) and `preferred_funders` (Grants.gov agency codes such as `CDC`, `HRSA`, `NPS`). `keywords`, `program_areas`, and `exclude_keywords` are optional.
2. Run `python scoring.py --data-dir data --agency <agency_id>`.
3. Add the agency to `DEPARTMENTS` in `app.py`.

No scoring code changes are needed.

## Known limitations

- **Geographic restrictions aren't detected.** A grant limited to certain states (e.g., the RESTORE Act, Gulf Coast states only) passes the state-government filter.
- **Overseas U.S. embassy grants** can pass the filter and match cultural or technology goals.
- **Applicant type is fixed** to state governments (`APPLICANT_TYPES` in `scoring.py`).
- **Meaning scores are relative** to each batch (min-max scaling), so they aren't comparable across days. Fixed anchors calibrated on labeled data would fix this.
- **AI summaries are spot-checked by hand,** not verified automatically.
- **Grant data is refreshed manually** from a Grants.gov CSV export. The `MODE=job` container re-scores but doesn't download new grants.
- **Decisions are stored in the container's file system** and are lost when the app restarts. A pilot would store them in Blob Storage.
- **Small evaluation sample:** hand labels cover a few dozen grants, not hundreds.

## Roadmap

- Automated daily download from the Grants.gov API, scheduled as a Container Apps job.
- Weekly email digest per agency via Power Automate ("new high-confidence matches; deadlines in 14 days").
- Per-agency settings (minimum award, cost-sharing tolerance) and a standard profile template.
- Use staff Pursue/Pass decisions to tune weights and confidence thresholds.
- Link Assistance Listing numbers (already stored) to SAM.gov program data.
- Move secrets to Azure Key Vault and decisions to Blob Storage.

## How this was built

Built in one day by a team of three, with substantial help from Claude (Anthropic) for writing code and Azure setup. Details on AI use, key prompts, and errors we caught are in our Field Notes.
