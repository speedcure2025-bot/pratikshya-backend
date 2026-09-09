import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("DATABASE_URL")

if not url:
    raise RuntimeError("DATABASE_URL not found in .env")

url = url.replace("postgresql+asyncpg://", "postgresql://", 1)

conn = psycopg2.connect(url)
cur = conn.cursor()

queries = {
    "NULL coupon codes": """
        SELECT count(*)
        FROM pratikshya.commerce_coupon
        WHERE code IS NULL;
    """,
    "NULL return order IDs": """
        SELECT count(*)
        FROM pratikshya.orders_return_order
        WHERE order_id IS NULL;
    """,
}

for name, query in queries.items():
    cur.execute(query)
    print(f"{name}: {cur.fetchone()[0]}")

cur.close()
conn.close()