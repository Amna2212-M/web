from __future__ import annotations

import html
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("CREATIVE_SPIRIT_DB", ROOT / "orders.sqlite3"))
ADMIN_TOKEN = os.environ.get("CREATIVE_SPIRIT_ADMIN_TOKEN") or secrets.token_urlsafe(32)
HOST = os.environ.get("CREATIVE_SPIRIT_HOST") or ("0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
STATUSES = {"new", "confirmed", "preparing", "shipped", "completed", "cancelled"}


def get_catalog() -> dict[str, str]:
    gallery = (ROOT / "Gallery.html").read_text(encoding="utf-8")
    return {
        html.unescape(title): kind
        for title, kind in re.findall(r'data-title="([^"]+)" data-kind="([^"]+)"', gallery)
    }


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE,
        created_at TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        customer_phone TEXT NOT NULL,
        customer_address TEXT NOT NULL,
        customer_notes TEXT NOT NULL DEFAULT '',
        items_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new'
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS inquiries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference TEXT UNIQUE,
        created_at TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        customer_contact TEXT NOT NULL,
        subject TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new'
    )""")
    db.commit()
    return db


@contextmanager
def database():
    db = connect_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def send_json(handler: SimpleHTTPRequestHandler, status: int, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


class StoreHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        # Avoid access logs containing submitted customer details.
        super().log_message(fmt, *args)

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        route = parsed.path
        aliases = {
            "/": "/Home.html",
            "/index.html": "/Home.html",
            "/home.html": "/Home.html",
            "/gallery.html": "/Gallery.html",
            "/about.html": "/About.html",
            "/orders.html": "/Orders.html",
            "/contact.html": "/Contact.html",
        }
        if route.lower() in aliases:
            path = aliases[route.lower()]
        return super().translate_path(path)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            return send_json(self, 200, {"ok": True, "service": "Creative Spirit orders"})
        if path == "/api/admin/inquiries":
            if self.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}":
                return send_json(self, 401, {"error": "Admin token required."})
            with database() as db:
                rows = db.execute("SELECT * FROM inquiries ORDER BY id DESC").fetchall()
            return send_json(self, 200, {"inquiries": [dict(row) for row in rows]})
        if path == "/api/admin/orders":
            if self.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}":
                return send_json(self, 401, {"error": "Admin token required."})
            with database() as db:
                rows = db.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
            orders = []
            for row in rows:
                order = dict(row)
                order["items"] = json.loads(order.pop("items_json"))
                orders.append(order)
            return send_json(self, 200, {"orders": orders})
        return super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/contact":
            return self.save_inquiry()
        if route != "/api/orders":
            return send_json(self, 404, {"error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 32768:
                return send_json(self, 413, {"error": "Order request is too large or empty."})
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return send_json(self, 400, {"error": "Invalid JSON request."})

        customer = payload.get("customer") if isinstance(payload, dict) else None
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(customer, dict) or not isinstance(items, list) or not items or len(items) > 20:
            return send_json(self, 400, {"error": "Add at least one artwork and enter your contact details."})

        fields = {
            "name": ("customer_name", 120),
            "phone": ("customer_phone", 50),
            "address": ("customer_address", 800),
            "notes": ("customer_notes", 1200),
        }
        values = {}
        for source, (target, limit) in fields.items():
            value = customer.get(source, "")
            if not isinstance(value, str) or len(value.strip()) > limit:
                return send_json(self, 400, {"error": f"Please check the {source} field."})
            values[target] = value.strip()
        if not values["customer_name"] or not values["customer_phone"] or not values["customer_address"]:
            return send_json(self, 400, {"error": "Name, phone, and delivery address are required."})

        catalog = get_catalog()
        clean_items = []
        for item in items:
            if not isinstance(item, dict):
                return send_json(self, 400, {"error": "Invalid artwork in the cart."})
            title = item.get("title")
            quantity = item.get("quantity")
            if title not in catalog or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10:
                return send_json(self, 400, {"error": "A cart item is invalid. Refresh the gallery and try again."})
            clean_items.append({"title": title, "kind": catalog[title], "quantity": quantity})

        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with database() as db:
            cursor = db.execute(
                "INSERT INTO orders (created_at, customer_name, customer_phone, customer_address, customer_notes, items_json) VALUES (?, ?, ?, ?, ?, ?)",
                (created_at, values["customer_name"], values["customer_phone"], values["customer_address"], values["customer_notes"], json.dumps(clean_items, ensure_ascii=False)),
            )
            order_number = f"CS-{cursor.lastrowid:06d}"
            db.execute("UPDATE orders SET order_number = ? WHERE id = ?", (order_number, cursor.lastrowid))
            db.commit()
        return send_json(self, 201, {"ok": True, "order_number": order_number, "status": "new"})


    def save_inquiry(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16384:
                return send_json(self, 413, {"error": "Inquiry is too large or empty."})
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return send_json(self, 400, {"error": "Invalid inquiry request."})
        if not isinstance(payload, dict):
            return send_json(self, 400, {"error": "Invalid inquiry request."})
        limits = {"name": 120, "contact": 120, "subject": 160, "message": 2000}
        values = {}
        for field, limit in limits.items():
            value = payload.get(field, "")
            if not isinstance(value, str) or len(value.strip()) > limit:
                return send_json(self, 400, {"error": f"Please check the {field} field."})
            values[field] = value.strip()
        if not values["name"] or not values["contact"] or not values["message"]:
            return send_json(self, 400, {"error": "Name, contact, and message are required."})
        with database() as db:
            cursor = db.execute(
                "INSERT INTO inquiries (created_at, customer_name, customer_contact, subject, message) VALUES (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), values["name"], values["contact"], values["subject"], values["message"]),
            )
            reference = f"CI-{cursor.lastrowid:06d}"
            db.execute("UPDATE inquiries SET reference = ? WHERE id = ?", (reference, cursor.lastrowid))
        return send_json(self, 201, {"ok": True, "reference": reference})

    def do_PATCH(self) -> None:
        match = re.fullmatch(r"/api/admin/orders/(\d+)/status", urlparse(self.path).path)
        if not match:
            return send_json(self, 404, {"error": "Not found."})
        if self.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}":
            return send_json(self, 401, {"error": "Admin token required."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(min(length, 4096)))
        except (ValueError, json.JSONDecodeError):
            return send_json(self, 400, {"error": "Invalid JSON request."})
        status = payload.get("status") if isinstance(payload, dict) else None
        if status not in STATUSES:
            return send_json(self, 400, {"error": "Unknown order status."})
        with database() as db:
            cursor = db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, int(match.group(1))))
            db.commit()
        if cursor.rowcount == 0:
            return send_json(self, 404, {"error": "Order not found."})
        return send_json(self, 200, {"ok": True, "status": status})

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()


if __name__ == "__main__":
    connect_db().close()
    print(f"Creative Spirit is running at http://{HOST}:{PORT}")
    print("Orders dashboard: http://%s:%s/Orders.html" % (HOST, PORT))
    print("Admin token (keep private): %s" % ADMIN_TOKEN)
    print("Order database: %s" % DB_PATH)
    ThreadingHTTPServer((HOST, PORT), StoreHandler).serve_forever()
