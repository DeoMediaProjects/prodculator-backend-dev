"""Quick DB state check for territory data coverage."""
import psycopg2

conn = psycopg2.connect(
    host="127.0.0.1",
    port=5432,
    dbname="prodculator",
    user="prodculator",
    password="prodculator2222",
)
cur = conn.cursor()

print("=== INCENTIVE STATUS VALUES ===")
cur.execute("SELECT status, COUNT(*) FROM incentive_programs GROUP BY status ORDER BY status")
for row in cur.fetchall():
    print(f"  {row[0]!r}: {row[1]}")

print("\n=== INCENTIVE TERRITORIES ===")
cur.execute("SELECT territory, COUNT(*) FROM incentive_programs GROUP BY territory ORDER BY territory")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

print("\n=== MALTA + HUNGARY INCENTIVES ===")
cur.execute("""
    SELECT territory, program, rate, status
    FROM incentive_programs
    WHERE territory IN ('Malta', 'Hungary')
    ORDER BY territory
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} | rate={row[2]} | status={row[3]}")

print("\n=== UK INCENTIVES ===")
cur.execute("""
    SELECT territory, program, rate, status
    FROM incentive_programs
    WHERE territory = 'United Kingdom'
    ORDER BY program
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} | rate={row[2]} | status={row[3]}")

print("\n=== FRANCE INCENTIVES ===")
cur.execute("""
    SELECT territory, program, rate, status
    FROM incentive_programs
    WHERE territory = 'France'
    ORDER BY program
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} | rate={row[2]} | status={row[3]}")

print("\n=== CREW COSTS COVERAGE (country → count) ===")
cur.execute("SELECT country, COUNT(*) FROM crew_costs GROUP BY country ORDER BY country")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

print("\n=== COMPARABLE PRODUCTIONS PRIMARY_TERRITORY ===")
cur.execute("""
    SELECT primary_territory, COUNT(*)
    FROM comparable_productions
    GROUP BY primary_territory
    ORDER BY COUNT(*) DESC
    LIMIT 15
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

print("\n=== GRANTS BY TERRITORY ===")
cur.execute("SELECT territory, COUNT(*) FROM grant_opportunities GROUP BY territory ORDER BY territory")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

print("\n=== FESTIVALS COUNT ===")
cur.execute("SELECT COUNT(*) FROM film_festivals")
print(f"  Total: {cur.fetchone()[0]}")

conn.close()
