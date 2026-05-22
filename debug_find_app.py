# debug_find_app.py
# Thoroughly check if the app is in the charts by searching the full list
import sys
sys.path.insert(0, ".")

from tracker import _fetch_region_charts

# Check Torchlight Infinite (火炬之光) in US region
# Its US app_id is 1593130084
target_id = "1593130084"
target_name = "Torchlight"

print(f"Searching for app_id={target_id}, name={target_name}\n")

for region in ["us", "gb", "jp", "kr", "sg", "de", "fr"]:
    charts = _fetch_region_charts(region)
    free_list = charts.get("top-free", [])
    gross_list = charts.get("top-grossing", [])

    # Search free list
    found_free = None
    for rank, aid, name in free_list:
        if str(aid) == target_id:
            found_free = (rank, aid, name)
            break

    # Search grossing list
    found_gross = None
    for rank, aid, name in gross_list:
        if str(aid) == target_id:
            found_gross = (rank, aid, name)
            break

    print(f"Region: {region}")
    if found_free:
        print(f"  FREE:  found at rank {found_free[0]}: id={found_free[1]}, name={found_free[2]}")
    else:
        print(f"  FREE:  NOT in top {len(free_list)}")

    if found_gross:
        print(f"  GROSS: found at rank {found_gross[0]}: id={found_gross[1]}, name={found_gross[2]}")
    else:
        # Try name match as fallback
        name_matches = [(r, a, n) for r, a, n in gross_list if n and "torch" in n.lower()]
        print(f"  GROSS: NOT in top {len(gross_list)} (name matches: {name_matches[:3]})")
    print()
