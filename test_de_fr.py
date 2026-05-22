# test_de_fr.py
# Verify DE and FR chart extraction works after config fix
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from tracker import _fetch_region_charts

for region in ["de", "fr"]:
    print(f"\n=== Fetching {region} ===")
    charts = _fetch_region_charts(region)
    free_list = charts.get("top-free", [])
    gross_list = charts.get("top-grossing", [])
    print(f"  top-free apps: {len(free_list)}")
    print(f"  top-grossing apps: {len(gross_list)}")
    if free_list:
        print(f"  Sample free app: rank={free_list[0][0]}, id={free_list[0][1]}, name={free_list[0][2]}")
    if gross_list:
        print(f"  Sample grossing app: rank={gross_list[0][0]}, id={gross_list[0][1]}, name={gross_list[0][2]}")
