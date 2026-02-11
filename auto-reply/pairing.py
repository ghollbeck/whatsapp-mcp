# ABOUTME: Pairing-based access control for the WhatsApp auto-reply daemon.
# ABOUTME: Manages contact approval flow: unknown -> pending (code sent) -> approved.

import sqlite3
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import structlog

logger = structlog.get_logger("pairing")


class ContactStatus(Enum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"


@dataclass
class Contact:
    jid: str
    status: ContactStatus
    pairing_code: Optional[str] = None
    code_expires_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    name: Optional[str] = None


class PairingStore:
    def __init__(self, db_path: str = "store/pairing.db",
                 code_expiry_minutes: int = 10, code_length: int = 6):
        self.db_path = db_path
        self.code_expiry_minutes = code_expiry_minutes
        self.code_length = code_length
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                jid TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unknown',
                pairing_code TEXT,
                code_expires_at TIMESTAMP,
                approved_at TIMESTAMP,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def get_contact(self, jid: str) -> Contact:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT jid, status, pairing_code, code_expires_at, approved_at, name FROM contacts WHERE jid = ?",
            (jid,)
        ).fetchone()
        conn.close()

        if not row:
            return Contact(jid=jid, status=ContactStatus.UNKNOWN)

        return Contact(
            jid=row[0],
            status=ContactStatus(row[1]),
            pairing_code=row[2],
            code_expires_at=datetime.fromisoformat(row[3]) if row[3] else None,
            approved_at=datetime.fromisoformat(row[4]) if row[4] else None,
            name=row[5]
        )

    def check_access(self, jid: str) -> ContactStatus:
        contact = self.get_contact(jid)
        if contact.status == ContactStatus.PENDING and contact.code_expires_at:
            if datetime.now() > contact.code_expires_at:
                logger.info("[SECURITY] Pairing code expired", jid=jid)
                self._update_status(jid, ContactStatus.UNKNOWN)
                return ContactStatus.UNKNOWN
        return contact.status

    def generate_pairing_code(self, jid: str, name: Optional[str] = None) -> str:
        code = ''.join(secrets.choice('0123456789') for _ in range(self.code_length))
        expires_at = datetime.now() + timedelta(minutes=self.code_expiry_minutes)

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO contacts (jid, status, pairing_code, code_expires_at, name, updated_at)
            VALUES (?, 'pending', ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(jid) DO UPDATE SET
                status = 'pending',
                pairing_code = excluded.pairing_code,
                code_expires_at = excluded.code_expires_at,
                name = COALESCE(excluded.name, contacts.name),
                updated_at = CURRENT_TIMESTAMP
        """, (jid, code, expires_at.isoformat(), name))
        conn.commit()
        conn.close()

        logger.info("[SECURITY] Pairing code generated", jid=jid, expires_at=expires_at.isoformat())
        return code

    def approve_contact(self, jid: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        now = datetime.now().isoformat()
        result = conn.execute(
            "UPDATE contacts SET status = 'approved', approved_at = ?, updated_at = ? WHERE jid = ?",
            (now, now, jid)
        )
        if result.rowcount == 0:
            conn.execute(
                "INSERT INTO contacts (jid, status, approved_at, updated_at) VALUES (?, 'approved', ?, ?)",
                (jid, now, now)
            )
        conn.commit()
        conn.close()
        logger.info("[SECURITY] Contact approved", jid=jid)
        return True

    def approve_by_code(self, code: str) -> Optional[str]:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT jid, code_expires_at FROM contacts WHERE pairing_code = ? AND status = 'pending'",
            (code,)
        ).fetchone()

        if not row:
            conn.close()
            return None

        jid = row[0]
        expires_at = datetime.fromisoformat(row[1]) if row[1] else None

        if expires_at and datetime.now() > expires_at:
            conn.close()
            logger.warning("[SECURITY] Attempted to use expired pairing code", code=code)
            return None

        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE contacts SET status = 'approved', approved_at = ?, updated_at = ? WHERE jid = ?",
            (now, now, jid)
        )
        conn.commit()
        conn.close()
        logger.info("[SECURITY] Contact approved via pairing code", jid=jid)
        return jid

    def block_contact(self, jid: str) -> bool:
        self._update_status(jid, ContactStatus.BLOCKED)
        logger.info("[SECURITY] Contact blocked", jid=jid)
        return True

    def list_contacts(self, status: Optional[ContactStatus] = None) -> list[Contact]:
        conn = sqlite3.connect(self.db_path)
        if status:
            rows = conn.execute(
                "SELECT jid, status, pairing_code, code_expires_at, approved_at, name FROM contacts WHERE status = ? ORDER BY updated_at DESC",
                (status.value,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT jid, status, pairing_code, code_expires_at, approved_at, name FROM contacts ORDER BY updated_at DESC"
            ).fetchall()
        conn.close()

        return [Contact(
            jid=r[0], status=ContactStatus(r[1]), pairing_code=r[2],
            code_expires_at=datetime.fromisoformat(r[3]) if r[3] else None,
            approved_at=datetime.fromisoformat(r[4]) if r[4] else None,
            name=r[5]
        ) for r in rows]

    def _update_status(self, jid: str, status: ContactStatus):
        conn = sqlite3.connect(self.db_path)
        result = conn.execute(
            "UPDATE contacts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE jid = ?",
            (status.value, jid)
        )
        if result.rowcount == 0:
            conn.execute(
                "INSERT INTO contacts (jid, status, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (jid, status.value)
            )
        conn.commit()
        conn.close()


if __name__ == "__main__":
    store = PairingStore()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python pairing.py approve <jid>        - Approve a contact by JID")
        print("  python pairing.py approve-code <code>  - Approve by pairing code")
        print("  python pairing.py block <jid>           - Block a contact")
        print("  python pairing.py list [status]         - List contacts")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "approve" and len(sys.argv) >= 3:
        store.approve_contact(sys.argv[2])
        print(f"Approved: {sys.argv[2]}")
    elif cmd == "approve-code" and len(sys.argv) >= 3:
        jid = store.approve_by_code(sys.argv[2])
        if jid:
            print(f"Approved: {jid}")
        else:
            print("Code not found or expired")
    elif cmd == "block" and len(sys.argv) >= 3:
        store.block_contact(sys.argv[2])
        print(f"Blocked: {sys.argv[2]}")
    elif cmd == "list":
        status_filter = ContactStatus(sys.argv[2]) if len(sys.argv) >= 3 else None
        contacts = store.list_contacts(status_filter)
        if not contacts:
            print("No contacts found")
        for c in contacts:
            print(f"  {c.jid:40s} {c.status.value:10s} {c.name or ''}")
    else:
        print(f"Unknown command: {cmd}")
