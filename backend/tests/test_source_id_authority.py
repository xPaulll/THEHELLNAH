import pytest
from backend.app.core.security import generate_source_id

def test_source_id_deterministic_normalization():
    # Varying whitespace and casing
    id1 = generate_source_id(
        broker="ICMarkets",
        environment="DEMO",
        account_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ea_identifier="ALPED_BRIDGE_01"
    )

    id2 = generate_source_id(
        broker="  icmarkets  ",
        environment="demo ",
        account_hash="E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855",
        ea_identifier=" alped_bridge_01 "
    )

    assert id1 == id2
    assert len(id1) == 64
    assert isinstance(id1, str)

def test_source_id_isolation_between_accounts():
    id_acc1 = generate_source_id(
        broker="ICMarkets",
        environment="DEMO",
        account_hash="1111111111111111111111111111111111111111111111111111111111111111",
        ea_identifier="ALPED_BRIDGE_01"
    )

    id_acc2 = generate_source_id(
        broker="ICMarkets",
        environment="DEMO",
        account_hash="2222222222222222222222222222222222222222222222222222222222222222",
        ea_identifier="ALPED_BRIDGE_01"
    )

    assert id_acc1 != id_acc2

def test_source_id_isolation_between_brokers():
    id_broker1 = generate_source_id(
        broker="ICMarkets",
        environment="DEMO",
        account_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ea_identifier="ALPED_BRIDGE_01"
    )

    id_broker2 = generate_source_id(
        broker="Exness",
        environment="DEMO",
        account_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ea_identifier="ALPED_BRIDGE_01"
    )

    assert id_broker1 != id_broker2
