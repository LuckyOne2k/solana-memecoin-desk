"""is_on_curve gegen unabhängige Referenzen.

1. Solana-Referenz: tests/fixtures/ed25519_on_curve_reference.json.
   Erwartete Werte stammen aus solders.pubkey.Pubkey.is_on_curve (Solana
   SDK; curve25519-dalek CompressedEdwardsY::decompress), erzeugt am
   2026-11-09, NICHT aus desk.solana_rpc. Enthält alle nicht-kanonischen
   y-Werte (p ... 2^255-1) mit und ohne Vorzeichenbit, kleine y, y nahe p,
   Punkte kleiner Ordnung, Zufallswerte und PDA-Hashes, die Solanas
   create_program_address angenommen (off-curve) bzw. abgelehnt
   (on-curve) hat.
2. RFC 8032, Abschnitt 5.1.3 (Punkt-Dekodierung), hier unabhängig
   nachgebaut. RFC 8032 lehnt nicht-kanonische y und (x = 0, Vorzeichen 1)
   ab, Solana (dalek) nimmt sie an. Für alle anderen Werte müssen beide
   übereinstimmen; die Abweichungen sind ausdrücklich geprüft.
"""

import json
import random
from pathlib import Path

import pytest

from desk.solana_rpc import _b58encode, is_on_curve


FIXTURE = Path(__file__).parent / "fixtures" / "ed25519_on_curve_reference.json"
REFERENCE = json.loads(FIXTURE.read_text(encoding="utf-8"))
VECTORS = REFERENCE["vectors"]

P = 2 ** 255 - 19
SIGN = 1 << 255


def address(raw):
    return _b58encode(raw)


def le(value):
    return value.to_bytes(32, "little")


# ---------------------------------------------------------------
# RFC 8032, 5.1.3 (unabhängige Implementierung, nur für Tests)
# ---------------------------------------------------------------

_D = -121665 * pow(121666, P - 2, P) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


def rfc8032_decode_ok(raw):
    """True, wenn RFC 8032 die 32 Byte als Punkt dekodiert."""
    value = int.from_bytes(raw, "little")
    sign = value >> 255
    y = value & (SIGN - 1)

    if y >= P:  # Schritt 1: nicht-kanonisch -> Fehler
        return False

    u = (y * y - 1) % P
    v = (_D * y * y + 1) % P
    # Schritt 2: x = u v^3 (u v^7)^((p-5)/8)
    x = u * pow(v, 3, P) * pow(u * pow(v, 7, P), (P - 5) // 8, P) % P

    # Schritt 3
    if (v * x * x - u) % P == 0:
        pass
    elif (v * x * x + u) % P == 0:
        x = x * _SQRT_M1 % P
    else:
        return False

    # Schritt 4: x = 0 mit Vorzeichen 1 -> Fehler
    if x == 0 and sign == 1:
        return False

    return True


def rfc_special_case(raw):
    """Werte, bei denen RFC 8032 strenger ist als Solana."""
    value = int.from_bytes(raw, "little")
    y = value & (SIGN - 1)
    return y >= P or (value >> 255 == 1 and y in (1, P - 1))


# ---------------------------------------------------------------
# Solana-Referenzvektoren
# ---------------------------------------------------------------

def test_reference_fixture_is_from_solana_sdk():
    assert "solders" in REFERENCE["source"]
    assert "curve25519-dalek" in REFERENCE["source"]
    assert len(VECTORS) >= 250
    labels = {v["label"] for v in VECTORS}
    assert "create_program_address accepted (PDA)" in labels
    assert "create_program_address rejected (on curve)" in labels
    # beide Ergebnisse ausreichend vertreten
    on = sum(v["on_curve"] for v in VECTORS)
    assert 60 <= on <= len(VECTORS) - 60


@pytest.mark.parametrize(
    "vector", VECTORS, ids=lambda v: f"{v['label']}:{v['hex'][:8]}"
)
def test_matches_solana_reference(vector):
    raw = bytes.fromhex(vector["hex"])
    assert is_on_curve(address(raw)) is vector["on_curve"]


def test_all_non_canonical_y_values_are_covered():
    non_canonical = {
        bytes.fromhex(v["hex"])
        for v in VECTORS
        if "non-canonical" in v["label"]
    }
    # 19 Werte p..2^255-1, je mit und ohne Vorzeichenbit
    assert len(non_canonical) == 38
    for k in range(19):
        assert le(P + k) in non_canonical
        assert le(P + k | SIGN) in non_canonical


def test_pdas_accepted_by_solana_are_off_curve():
    pdas = [v for v in VECTORS if v["label"] in (
        "create_program_address accepted (PDA)", "find_program_address")]
    assert len(pdas) >= 30
    for vector in pdas:
        assert vector["on_curve"] is False
        assert is_on_curve(address(bytes.fromhex(vector["hex"]))) is False


def test_hashes_rejected_by_solana_are_on_curve():
    rejected = [v for v in VECTORS if v["label"] == "create_program_address rejected (on curve)"]
    assert len(rejected) >= 20
    for vector in rejected:
        assert is_on_curve(address(bytes.fromhex(vector["hex"]))) is True


# ---------------------------------------------------------------
# RFC 8032 als zweite unabhängige Referenz
# ---------------------------------------------------------------

def test_rfc8032_agrees_on_all_regular_reference_vectors():
    checked = 0
    for vector in VECTORS:
        raw = bytes.fromhex(vector["hex"])
        if rfc_special_case(raw):
            continue
        assert rfc8032_decode_ok(raw) is vector["on_curve"], vector["label"]
        checked += 1
    assert checked >= 200


def test_rfc8032_agrees_on_random_values():
    rng = random.Random(4711)
    for _ in range(3000):
        raw = bytes(rng.getrandbits(8) for _ in range(32))
        if rfc_special_case(raw):
            continue
        assert is_on_curve(address(raw)) is rfc8032_decode_ok(raw)


@pytest.mark.parametrize(
    "raw",
    [le(P), le(P + 1), le(2 ** 255 - 1), b"\xff" * 32, le(1 | SIGN), le(P - 1 | SIGN)],
    ids=["y=p", "y=p+1", "y=2^255-1", "0xff*32", "y=1+sign", "y=p-1+sign"],
)
def test_solana_is_more_lenient_than_rfc8032_here(raw):
    # Diese Werte lehnt RFC 8032 ab; Solana (dalek) nimmt sie als
    # on-curve. Wir folgen Solana, denn nur dessen Einordnung entscheidet,
    # ob eine Adresse eine PDA sein kann. Zu solchen Adressen gibt es
    # trotzdem keinen privaten Schlüssel: ein Beispiel für das
    # dokumentierte Restrisiko (desk/circulating_bounds.py).
    assert rfc8032_decode_ok(raw) is False
    expected = {v["hex"]: v["on_curve"] for v in VECTORS}[raw.hex()]
    assert expected is True
    assert is_on_curve(address(raw)) is True


# ---------------------------------------------------------------
# Eingaben, die keine 32-Byte-Adresse sind
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        _b58encode(b"\x01" * 31),   # 31 Byte
        _b58encode(b"\x01" * 33),   # 33 Byte
        "1" * 31,                   # 31 Null-Bytes
        "1" * 33,                   # 33 Null-Bytes
        "11111111111111111111111111111111O",  # ungültiges Zeichen
        " " + "1" * 32,
        "",
        None,
        b"\x00" * 32,               # Bytes statt Text
    ],
)
def test_non_32_byte_input_raises(bad):
    with pytest.raises(ValueError):
        is_on_curve(bad)


def test_exactly_32_zero_bytes_is_system_program_and_on_curve():
    assert address(bytes(32)) == "11111111111111111111111111111111"
    assert is_on_curve("11111111111111111111111111111111") is True
