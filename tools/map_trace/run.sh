#!/usr/bin/env bash
# Rebuilds docs/mapdata.js from the five map PNGs in the repo root.
# Needs: pip install opencv-python-headless scikit-image shapely numpy
set -e
cd "$(dirname "$0")"
python reg.py && python extract.py && python extract2.py && python build_data.py
echo "Wrote docs/mapdata.js"
