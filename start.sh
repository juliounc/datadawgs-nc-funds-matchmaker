#!/usr/bin/env sh
# One image, two roles (kit section 3):
#   MODE=app (default): run the staff website on port 8000
#   MODE=job:           re-score every agency, then exit (non-zero exit = Azure marks the run Failed)
set -e

if [ "$MODE" = "job" ]; then
  for agency in ncdhhs ncdit ncdncr; do
    python scoring.py --data-dir "$DATA_DIR" --agency "$agency"
  done
else
  exec streamlit run app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true
fi
