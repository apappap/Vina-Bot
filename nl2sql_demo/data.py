"""Build two fictional tenants' SQLite databases.

All rows are generated from a fixed random seed, so every run (and every test)
sees the same numbers. Nothing here comes from a real company or customer.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# Data is generated relative to this date, so results do not drift with the calendar.
ANCHOR = date(2026, 10, 31)

SCHEMA = """
CREATE TABLE warehouses (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);
CREATE TABLE parcels (
    tracking_no TEXT PRIMARY KEY,
    customer_code TEXT NOT NULL,
    warehouse_code TEXT NOT NULL REFERENCES warehouses(code),
    status TEXT NOT NULL,
    cartons INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE TABLE loads (
    load_no TEXT PRIMARY KEY,
    truck_no TEXT NOT NULL,
    warehouse_code TEXT NOT NULL REFERENCES warehouses(code),
    departed_at TEXT
);
CREATE TABLE load_items (
    load_no TEXT NOT NULL REFERENCES loads(load_no),
    tracking_no TEXT NOT NULL REFERENCES parcels(tracking_no)
);
CREATE TABLE app_users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
"""

TENANTS = {
    "acme": {
        "seed": 7,
        "parcels": 24000,
        "loads": 40,
        "prefix": "AC",
        "customers": ["C001", "C002", "C003", "C004", "C005"],
        "warehouses": [("WH-N", "北仓", "长沙"), ("WH-S", "南仓", "株洲"), ("WH-E", "东仓", "湘潭")],
    },
    "globex": {
        "seed": 11,
        "parcels": 600,
        "loads": 8,
        "prefix": "GX",
        "customers": ["G01", "G02"],
        "warehouses": [("GX-1", "一号仓", "武汉"), ("GX-2", "二号仓", "孝感")],
    },
}


def build(tenant: str, path: Path, parcels: int | None = None) -> Path:
    """Create (or recreate) one tenant's database file and fill it with generated rows."""
    cfg = TENANTS[tenant]
    rnd = random.Random(cfg["seed"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        warehouses = cfg["warehouses"]
        con.executemany("INSERT INTO warehouses VALUES (?, ?, ?)", warehouses)

        rows = []
        for i in range(parcels if parcels is not None else cfg["parcels"]):
            created = ANCHOR - timedelta(days=rnd.randint(0, 120))
            status = rnd.choices("NRLDX", weights=[5, 10, 10, 70, 5])[0]
            delivered = None
            if status == "D":
                delivered = min(created + timedelta(days=rnd.randint(1, 5)), ANCHOR).isoformat()
            rows.append((
                f"{cfg['prefix']}{i:06d}",
                rnd.choice(cfg["customers"]),
                rnd.choice(warehouses)[0],
                status,
                rnd.randint(1, 12),
                created.isoformat(),
                delivered,
            ))
        con.executemany("INSERT INTO parcels VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

        on_trucks = [r for r in rows if r[3] in ("L", "D")]
        loads, items = [], []
        for n in range(cfg["loads"]):
            load_no = f"LD{n + 1:04d}"
            departed = (ANCHOR - timedelta(days=rnd.randint(0, 60))).isoformat()
            loads.append((load_no, f"TRK-{rnd.randint(100, 999)}", rnd.choice(warehouses)[0], departed))
            for parcel in rnd.sample(on_trucks, k=min(len(on_trucks), rnd.randint(5, 15))):
                items.append((load_no, parcel[0]))
        con.executemany("INSERT INTO loads VALUES (?, ?, ?, ?)", loads)
        con.executemany("INSERT INTO load_items VALUES (?, ?)", items)

        # A table the assistant must never read from: it is not in the knowledge base,
        # and its password column would be refused even if it were.
        con.executemany(
            "INSERT INTO app_users VALUES (?, ?, ?)",
            [(1, "admin", "pbkdf2$not-a-real-hash"), (2, "operator", "pbkdf2$not-a-real-hash")],
        )
        con.commit()
    finally:
        con.close()
    return path


def build_all(directory: Path | str = "demo_data") -> dict[str, Path]:
    directory = Path(directory)
    return {tenant: build(tenant, directory / f"{tenant}.db") for tenant in TENANTS}


if __name__ == "__main__":
    for tenant, db in build_all().items():
        print(f"{tenant}: {db}")
