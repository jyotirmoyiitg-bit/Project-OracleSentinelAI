import uuid

from sentinel.incidents.fingerprinting import compute_fingerprint


def test_same_inputs_produce_same_fingerprint():
    target_id = uuid.uuid4()
    first = compute_fingerprint("rule_a", target_id, "USERS")
    second = compute_fingerprint("rule_a", target_id, "USERS")
    assert first == second
    assert len(first) == 64
    int(first, 16)  # valid hex


def test_different_scope_key_produces_different_fingerprint():
    target_id = uuid.uuid4()
    assert compute_fingerprint("rule_a", target_id, "USERS") != compute_fingerprint("rule_a", target_id, "SYSTEM")


def test_different_rule_id_produces_different_fingerprint():
    target_id = uuid.uuid4()
    assert compute_fingerprint("rule_a", target_id, "") != compute_fingerprint("rule_b", target_id, "")


def test_different_target_id_produces_different_fingerprint():
    assert compute_fingerprint("rule_a", uuid.uuid4(), "") != compute_fingerprint("rule_a", uuid.uuid4(), "")


def test_accepts_string_target_id_consistently():
    target_id = uuid.uuid4()
    assert compute_fingerprint("rule_a", target_id, "") == compute_fingerprint("rule_a", str(target_id), "")
