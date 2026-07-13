from pathlib import Path
import sqlite3

from flask import g
from werkzeug.security import generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "trekking.db"


# ---------------- database setup ----------------


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # sqlite needs reminding about foreign keys every connection
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_all(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def query_one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def execute(sql, params=()):
    db = get_db()
    cursor = db.execute(sql, params)
    # tiny writes commit here so no half-finished changes
    db.commit()
    return cursor.lastrowid


def init_db():
    db = get_db()
    # DO NOT CHANGE THIS ORDER treks and bookings depend on users
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'staff', 'user')),
            phone TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_DATE
        );

        CREATE TABLE IF NOT EXISTS treks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            difficulty TEXT NOT NULL CHECK (difficulty IN ('Easy', 'Moderate', 'Hard')),
            duration_days INTEGER NOT NULL,
            total_slots INTEGER NOT NULL,
            staff_id INTEGER,
            status TEXT NOT NULL DEFAULT 'Open',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (staff_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trek_id INTEGER NOT NULL,
            booking_date TEXT NOT NULL DEFAULT CURRENT_DATE,
            status TEXT NOT NULL DEFAULT 'Booked',
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (trek_id) REFERENCES treks(id) ON DELETE CASCADE
        );
        """
    )

    seed_users()
    seed_treks()


def seed_users():
    # demo passwords get hashed too obviously
    if not query_one("SELECT id FROM users WHERE role = 'admin'"):
        execute(
            """
            INSERT INTO users (name, email, password_hash, role, phone, status)
            VALUES (?, ?, ?, 'admin', ?, 'active')
            """,
            (
                "Admin",
                "admin@example.com",
                generate_password_hash("admin123"),
                "9999999999",
            ),
        )

    if not query_one("SELECT id FROM users WHERE email = ?", ("staff@example.com",)):
        execute(
            """
            INSERT INTO users (name, email, password_hash, role, phone, status)
            VALUES (?, ?, ?, 'staff', ?, 'approved')
            """,
            (
                "Vikas Singh",
                "staff@example.com",
                generate_password_hash("staff123"),
                "9876543210",
            ),
        )
        execute(
            """
            INSERT INTO users (name, email, password_hash, role, phone, status)
            VALUES (?, ?, ?, 'staff', ?, 'pending')
            """,
            (
                "Neha Joshi",
                "neha@example.com",
                generate_password_hash("staff123"),
                "9123456780",
            ),
        )

    if not query_one("SELECT id FROM users WHERE email = ?", ("user@example.com",)):
        execute(
            """
            INSERT INTO users (name, email, password_hash, role, phone, status)
            VALUES (?, ?, ?, 'user', ?, 'active')
            """,
            (
                "Amit Sharma",
                "user@example.com",
                generate_password_hash("user123"),
                "9000000000",
            ),
        )


def seed_treks():
    if query_one("SELECT id FROM treks"):
        return

    staff = query_one("SELECT id FROM users WHERE email = ?", ("staff@example.com",))
    treks = [
        (
            "Everest Base Camp",
            "Nepal",
            "Hard",
            12,
            10,
            staff["id"],
            "Open",
            "2026-08-10",
            "2026-08-21",
            "A high-altitude trek with mountain views and experienced guidance.",
        ),
        (
            "Roopkund Trek",
            "Uttarakhand",
            "Moderate",
            7,
            15,
            staff["id"],
            "Open",
            "2026-09-01",
            "2026-09-07",
            "A scenic Himalayan route through forests, meadows, and glacial lakes.",
        ),
        (
            "Kedarkantha Trek",
            "Uttarakhand",
            "Easy",
            5,
            20,
            staff["id"],
            "Closed",
            "2026-10-12",
            "2026-10-16",
            "A beginner-friendly summit trek with campsites and snow views.",
        ),
        (
            "Hampta Pass",
            "Himachal",
            "Moderate",
            5,
            12,
            staff["id"],
            "Open",
            "2026-11-05",
            "2026-11-09",
            "A crossover trek with contrasting valleys and river crossings.",
        ),
    ]
    # seed all treks together warna half the demo disappears
    db = get_db()
    db.executemany(
        """
        INSERT INTO treks
        (name, location, difficulty, duration_days, total_slots, staff_id,
         status, start_date, end_date, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        treks,
    )
    db.commit()


def booked_count(trek_id):
    row = query_one(
        "SELECT COUNT(*) AS total FROM bookings WHERE trek_id = ? AND status = 'Booked'",
        (trek_id,),
    )
    return row["total"]


def add_trek_counts(treks):
    items = []
    for trek in treks:
        item = dict(trek)
        item["booked_count"] = booked_count(trek["id"])
        # dont let slots go negative ayyo
        item["remaining_slots"] = max(0, trek["total_slots"] - item["booked_count"])
        items.append(item)
    return items


def trek_by_id(trek_id):
    trek = query_one(
        """
        SELECT t.*, u.name AS staff_name
        FROM treks t
        LEFT JOIN users u ON u.id = t.staff_id
        WHERE t.id = ?
        """,
        (trek_id,),
    )
    if not trek:
        return None
    return add_trek_counts([trek])[0]
