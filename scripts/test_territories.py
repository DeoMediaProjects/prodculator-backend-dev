"""Quick smoke test for the Territory enum."""
from app.core.territories import Territory, resolve_territory, territory_to_iso, iso_to_territory

# Test basic properties
uk = Territory.UNITED_KINGDOM
print(f"UK: label={uk.label}, iso={uk.iso}, parent={uk.parent}")

scot = Territory.SCOTLAND
print(f"Scotland: label={scot.label}, iso={scot.iso}, parent={scot.parent}")

# Test resolution
for name in ["UK", "usa", "GB", "United Kingdom", "New York", "Malta",
             "Hungary", "France", "South Africa", "Gauteng", "ZA",
             "United States of America", "Czechia", "Canada"]:
    t = resolve_territory(name)
    print(f"  resolve({name!r}) -> {t.name if t else None} ({t.label if t else None})")

# Test derived maps
print(f"\nCountries: {len(Territory.countries())}")
print(f"territory_to_iso entries: {len(territory_to_iso())}")
print(f"iso_to_territory entries: {len(iso_to_territory())}")
print(f"UK sub-territories: {[t.label for t in Territory.sub_territories_of(Territory.UNITED_KINGDOM)]}")
print(f"US sub-territories: {[t.label for t in Territory.sub_territories_of(Territory.UNITED_STATES)]}")
print(f"CA sub-territories: {[t.label for t in Territory.sub_territories_of(Territory.CANADA)]}")
print(f"AU sub-territories: {[t.label for t in Territory.sub_territories_of(Territory.AUSTRALIA)]}")
print(f"ZA sub-territories: {[t.label for t in Territory.sub_territories_of(Territory.SOUTH_AFRICA)]}")
print(f"DE sub-territories: {[t.label for t in Territory.sub_territories_of(Territory.GERMANY)]}")
