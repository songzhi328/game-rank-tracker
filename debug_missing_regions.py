# debug_missing_regions.py
# Check if specific app_ids appear in the chart data for regions where games showed "未上榜"
import sys
sys.path.insert(0, ".")

from tracker import _fetch_region_charts, _find_rank

# Test regions where 火炬之光 showed 未上榜
regions_to_check = ["us", "gb", "jp", "kr", "sg", "de", "fr", "hk", "mo"]
target_app_id = "1593130084"  # Torchlight infinite US/intl ID
target_name = "Torchlight"

print("=== Checking if app_id", target_app_id, "is in charts ===\n")

for region in regions_to_check:
    charts = _fetch_region_charts(region)
    free_list = charts.get("top-free", [])
    gross_list = charts.get("top-grossing", [])

    # Check by ID
    free_rank_by_id = _find_rank(free_list, target_app_id, target_name)
    gross_rank_by_id = _find_rank(gross_list, target_app_id, target_name)

    # Also print first few app names to see what names look like
    free_names = [n for _, _, n in free_list[:10]]
    gross_names = [n for _, _, n in gross_list[:10]]

    print(f"Region: {region}")
    print(f"  free:  rank={free_rank_by_id}  (list has {len(free_list)} apps)")
    print(f"  gross: rank={gross_rank_by_id}  (list has {len(gross_list)} apps)")
    if free_rank_by_id == -1:
        print(f"  sample free names: {free_names}")
    if gross_rank_by_id == -1:
        print(f"  sample gross names: {gross_names}")
    print()
