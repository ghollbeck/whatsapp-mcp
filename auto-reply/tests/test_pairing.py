# ABOUTME: Tests for the pairing-based access control store.
# ABOUTME: Covers the full contact lifecycle: unknown -> pending -> approved/blocked.

import time
import pytest
from datetime import datetime, timedelta

from pairing import PairingStore, ContactStatus


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_pairing.db")
    return PairingStore(db_path=db_path, code_expiry_minutes=1, code_length=6)


class TestCheckAccess:
    def test_unknown_contact_returns_unknown(self, store):
        status = store.check_access("unknown_jid@s.whatsapp.net")
        assert status == ContactStatus.UNKNOWN

    def test_approved_contact_returns_approved(self, store):
        jid = "user@s.whatsapp.net"
        store.approve_contact(jid)
        assert store.check_access(jid) == ContactStatus.APPROVED

    def test_blocked_contact_returns_blocked(self, store):
        jid = "spammer@s.whatsapp.net"
        store.block_contact(jid)
        assert store.check_access(jid) == ContactStatus.BLOCKED

    def test_pending_contact_returns_pending(self, store):
        jid = "new@s.whatsapp.net"
        store.generate_pairing_code(jid, "New User")
        assert store.check_access(jid) == ContactStatus.PENDING


class TestPairingCodeGeneration:
    def test_generates_numeric_code(self, store):
        code = store.generate_pairing_code("user@s.whatsapp.net")
        assert code.isdigit()
        assert len(code) == 6

    def test_code_stored_in_contact(self, store):
        jid = "user@s.whatsapp.net"
        code = store.generate_pairing_code(jid, "Test User")
        contact = store.get_contact(jid)
        assert contact.pairing_code == code
        assert contact.status == ContactStatus.PENDING
        assert contact.name == "Test User"

    def test_regenerating_code_updates_existing(self, store):
        jid = "user@s.whatsapp.net"
        code1 = store.generate_pairing_code(jid)
        code2 = store.generate_pairing_code(jid)
        contact = store.get_contact(jid)
        assert contact.pairing_code == code2
        assert contact.status == ContactStatus.PENDING


class TestPairingCodeApproval:
    def test_approve_by_valid_code(self, store):
        jid = "user@s.whatsapp.net"
        code = store.generate_pairing_code(jid, "User")
        result = store.approve_by_code(code)
        assert result == jid
        assert store.check_access(jid) == ContactStatus.APPROVED

    def test_approve_by_invalid_code_returns_none(self, store):
        result = store.approve_by_code("000000")
        assert result is None

    def test_approve_by_expired_code_returns_none(self, tmp_path):
        db_path = str(tmp_path / "expiry_test.db")
        store = PairingStore(db_path=db_path, code_expiry_minutes=0, code_length=6)
        jid = "user@s.whatsapp.net"
        code = store.generate_pairing_code(jid)
        # code_expiry_minutes=0 means it expires immediately
        time.sleep(0.1)
        result = store.approve_by_code(code)
        assert result is None

    def test_approve_contact_directly_by_jid(self, store):
        jid = "vip@s.whatsapp.net"
        store.approve_contact(jid)
        assert store.check_access(jid) == ContactStatus.APPROVED
        contact = store.get_contact(jid)
        assert contact.approved_at is not None


class TestContactBlocking:
    def test_block_unknown_contact(self, store):
        jid = "bad@s.whatsapp.net"
        # First generate a code so the contact exists in DB
        store.generate_pairing_code(jid)
        store.block_contact(jid)
        assert store.check_access(jid) == ContactStatus.BLOCKED

    def test_block_approved_contact(self, store):
        jid = "revoked@s.whatsapp.net"
        store.approve_contact(jid)
        assert store.check_access(jid) == ContactStatus.APPROVED
        store.block_contact(jid)
        assert store.check_access(jid) == ContactStatus.BLOCKED


class TestListContacts:
    def test_list_all_contacts(self, store):
        store.approve_contact("a@s.whatsapp.net")
        store.generate_pairing_code("b@s.whatsapp.net")
        contacts = store.list_contacts()
        assert len(contacts) == 2

    def test_list_by_status_filter(self, store):
        store.approve_contact("a@s.whatsapp.net")
        store.generate_pairing_code("b@s.whatsapp.net")
        approved = store.list_contacts(ContactStatus.APPROVED)
        assert len(approved) == 1
        assert approved[0].jid == "a@s.whatsapp.net"

    def test_list_empty_when_no_contacts(self, store):
        contacts = store.list_contacts()
        assert contacts == []


class TestExpiredCodeReset:
    def test_expired_code_resets_to_unknown(self, tmp_path):
        db_path = str(tmp_path / "expiry_reset.db")
        store = PairingStore(db_path=db_path, code_expiry_minutes=0, code_length=6)
        jid = "user@s.whatsapp.net"
        store.generate_pairing_code(jid)
        time.sleep(0.1)
        status = store.check_access(jid)
        assert status == ContactStatus.UNKNOWN
