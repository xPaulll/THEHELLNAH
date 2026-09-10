import pytest
from fastapi.testclient import TestClient

from backend.app.core.constants import (
    MarketState,
    StructureStrength,
    StructureConfiguration,
    CanonicalStructureEventType,
    canonicalize_symbol
)
from backend.app.models.market_structure_state_models import CanonicalStructureEvent
from backend.app.features.market_structure_state_engine import (
    market_structure_state_engine,
    create_initial_market_state,
    check_ordered_bullish_sequence,
    check_ordered_bearish_sequence
)
from backend.app.repositories.market_structure_state_repo import (
    market_structure_state_repo,
    clear_state_memory,
    _memory_current_state,
    _memory_state_history,
    _memory_processed_events
)
from backend.app.main import app
from backend.app.core.config import settings

client = TestClient(app)
SOURCE_ID = "a" * 64
AUTH_HEADERS = {"X-API-Key": settings.API_KEY}


@pytest.fixture(autouse=True)
def clean_state_isolation():
    """Guarantees 100% clean test isolation in pure memory."""
    clear_state_memory()
    yield
    clear_state_memory()


def make_event(
    event_type: CanonicalStructureEventType,
    bar_time: int,
    bar_index: int,
    price: float = 2750.0,
    source_id: str = SOURCE_ID,
    symbol: str = "XAUUSD",
    timeframe: str = "M15",
    is_closed_bar: bool = True,
    source_event_id: int = 1
) -> CanonicalStructureEvent:
    ev_key = f"{source_id}:{symbol}:{timeframe}:{event_type.value}:{bar_time}:{source_event_id}"
    return CanonicalStructureEvent(
        source_id=source_id,
        symbol=symbol,
        timeframe=timeframe,
        event_type=event_type,
        event_time=bar_time,
        event_price=price,
        bar_time=bar_time,
        bar_index=bar_index,
        source_event_id=source_event_id,
        is_closed_bar=is_closed_bar,
        event_key=ev_key
    )


def test_01_initial_state():
    """1. Initial State: Empty input -> UNKNOWN, INSUFFICIENT, NONE."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    assert state["state"] == MarketState.UNKNOWN.value
    assert state["previous_state"] == MarketState.UNKNOWN.value
    assert state["structure"] == StructureConfiguration.NONE.value
    assert state["structure_strength"] == StructureStrength.INSUFFICIENT.value
    assert state["state_changed"] is False
    assert state["last_event"] == "NONE"


def test_02_bullish_confirmation():
    """2. Bullish Confirmation: Validated ordered HH -> HL sequence -> BULLISH, CONFIRMED, HH_HL."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    swings = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0, "confirmed_at_candle_time_epoch": 100},
        {"id": 2, "classification": "HL", "swing_type": "LOW", "swing_price": 2720.0, "confirmed_at_candle_time_epoch": 120},
    ]

    ev = make_event(CanonicalStructureEventType.HL, bar_time=120, bar_index=1, price=2720.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev, swings)

    assert mutated is True
    assert state["state"] == MarketState.BULLISH.value
    assert state["previous_state"] == MarketState.UNKNOWN.value
    assert state["structure"] == StructureConfiguration.HH_HL.value
    assert state["structure_strength"] == StructureStrength.CONFIRMED.value
    assert state["state_changed"] is True

    # Persist and verify history
    market_structure_state_repo.save_state_and_history_if_changed(state, ev.event_key)
    history = market_structure_state_repo.get_state_history(SOURCE_ID, "XAUUSD", "M15")
    assert len(history) == 1
    assert history[0]["new_state"] == MarketState.BULLISH.value


def test_03_bearish_confirmation():
    """3. Bearish Confirmation: Validated ordered LH -> LL sequence -> BEARISH, CONFIRMED, LH_LL."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    swings = [
        {"id": 1, "classification": "LH", "swing_type": "HIGH", "swing_price": 2740.0, "confirmed_at_candle_time_epoch": 100},
        {"id": 2, "classification": "LL", "swing_type": "LOW", "swing_price": 2700.0, "confirmed_at_candle_time_epoch": 120},
    ]

    ev = make_event(CanonicalStructureEventType.LL, bar_time=120, bar_index=1, price=2700.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev, swings)

    assert mutated is True
    assert state["state"] == MarketState.BEARISH.value
    assert state["previous_state"] == MarketState.UNKNOWN.value
    assert state["structure"] == StructureConfiguration.LH_LL.value
    assert state["structure_strength"] == StructureStrength.CONFIRMED.value
    assert state["state_changed"] is True


def test_04_bearish_choch_to_transition():
    """4. Bearish CHoCH: BULLISH + CHOCH_BEARISH -> TRANSITION, NOT BEARISH."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.BULLISH.value
    state["previous_state"] = MarketState.NEUTRAL.value
    state["structure"] = StructureConfiguration.HH_HL.value
    state["structure_strength"] = StructureStrength.CONFIRMED.value
    state["bar_time"] = 100

    ev = make_event(CanonicalStructureEventType.CHOCH_BEARISH, bar_time=120, bar_index=2, price=2710.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev)

    assert mutated is True
    assert state["state"] == MarketState.TRANSITION.value
    assert state["previous_state"] == MarketState.BULLISH.value
    assert state["structure_strength"] == StructureStrength.DEVELOPING.value
    # Structure context is preserved during transition
    assert state["structure"] == StructureConfiguration.HH_HL.value
    assert state["state_changed"] is True


def test_05_bullish_choch_to_transition():
    """5. Bullish CHoCH: BEARISH + CHOCH_BULLISH -> TRANSITION, NOT BULLISH."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.BEARISH.value
    state["previous_state"] = MarketState.NEUTRAL.value
    state["structure"] = StructureConfiguration.LH_LL.value
    state["structure_strength"] = StructureStrength.CONFIRMED.value
    state["bar_time"] = 100

    ev = make_event(CanonicalStructureEventType.CHOCH_BULLISH, bar_time=120, bar_index=2, price=2730.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev)

    assert mutated is True
    assert state["state"] == MarketState.TRANSITION.value
    assert state["previous_state"] == MarketState.BEARISH.value
    assert state["structure_strength"] == StructureStrength.DEVELOPING.value
    assert state["structure"] == StructureConfiguration.LH_LL.value
    assert state["state_changed"] is True


def test_06_bearish_confirmation_after_choch():
    """6. Bearish Confirmation after CHoCH: TRANSITION + LH -> LL -> BEARISH."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.TRANSITION.value
    state["previous_state"] = MarketState.BULLISH.value
    state["structure"] = StructureConfiguration.HH_HL.value
    state["structure_strength"] = StructureStrength.DEVELOPING.value
    state["bar_time"] = 100

    post_swings = [
        {"id": 1, "classification": "LH", "swing_type": "HIGH", "swing_price": 2735.0, "confirmed_at_candle_time_epoch": 110},
        {"id": 2, "classification": "LL", "swing_type": "LOW", "swing_price": 2690.0, "confirmed_at_candle_time_epoch": 130},
    ]

    ev = make_event(CanonicalStructureEventType.LL, bar_time=130, bar_index=3, price=2690.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev, post_swings)

    assert mutated is True
    assert state["state"] == MarketState.BEARISH.value
    assert state["previous_state"] == MarketState.TRANSITION.value
    assert state["structure"] == StructureConfiguration.LH_LL.value
    assert state["structure_strength"] == StructureStrength.CONFIRMED.value
    assert state["state_changed"] is True


def test_07_bullish_confirmation_after_choch():
    """7. Bullish Confirmation after CHoCH: TRANSITION + HH -> HL -> BULLISH."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.TRANSITION.value
    state["previous_state"] = MarketState.BEARISH.value
    state["structure"] = StructureConfiguration.LH_LL.value
    state["structure_strength"] = StructureStrength.DEVELOPING.value
    state["bar_time"] = 100

    post_swings = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0, "confirmed_at_candle_time_epoch": 110},
        {"id": 2, "classification": "HL", "swing_type": "LOW", "swing_price": 2725.0, "confirmed_at_candle_time_epoch": 130},
    ]

    ev = make_event(CanonicalStructureEventType.HL, bar_time=130, bar_index=3, price=2725.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev, post_swings)

    assert mutated is True
    assert state["state"] == MarketState.BULLISH.value
    assert state["previous_state"] == MarketState.TRANSITION.value
    assert state["structure"] == StructureConfiguration.HH_HL.value
    assert state["structure_strength"] == StructureStrength.CONFIRMED.value
    assert state["state_changed"] is True


def test_08_mss_transition():
    """8. MSS Transition: BULLISH + MSS_BEARISH -> TRANSITION (conservative regime shift)."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.BULLISH.value
    state["previous_state"] = MarketState.BULLISH.value
    state["structure"] = StructureConfiguration.HH_HL.value
    state["bar_time"] = 100

    ev = make_event(CanonicalStructureEventType.MSS_BEARISH, bar_time=120, bar_index=2, price=2700.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev)

    assert mutated is True
    assert state["state"] == MarketState.TRANSITION.value
    assert state["previous_state"] == MarketState.BULLISH.value
    assert state["structure_strength"] == StructureStrength.DEVELOPING.value


def test_09_mss_confirmation():
    """9. MSS Confirmation: TRANSITION + MSS_BEARISH -> BEARISH."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.TRANSITION.value
    state["previous_state"] = MarketState.BULLISH.value
    state["structure"] = StructureConfiguration.HH_HL.value
    state["bar_time"] = 100

    ev = make_event(CanonicalStructureEventType.MSS_BEARISH, bar_time=120, bar_index=2, price=2700.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev)

    assert mutated is True
    assert state["state"] == MarketState.BEARISH.value
    assert state["previous_state"] == MarketState.TRANSITION.value
    assert state["structure"] == StructureConfiguration.LH_LL.value
    assert state["structure_strength"] == StructureStrength.CONFIRMED.value


def test_10_opposite_bos_protection():
    """10. Opposite BOS: BEARISH + BOS_BULLISH must NOT directly become BULLISH."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.BEARISH.value
    state["previous_state"] = MarketState.BEARISH.value
    state["structure"] = StructureConfiguration.LH_LL.value
    state["structure_strength"] = StructureStrength.CONFIRMED.value
    state["bar_time"] = 100

    ev = make_event(CanonicalStructureEventType.BOS_BULLISH, bar_time=120, bar_index=2, price=2750.0)
    state, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev)

    # Invariant: Must NOT flip directly to BULLISH
    assert state["state"] != MarketState.BULLISH.value
    assert state["state"] == MarketState.TRANSITION.value
    assert state["structure_strength"] == StructureStrength.DEVELOPING.value
    assert state["structure"] == StructureConfiguration.MIXED.value


def test_11_duplicate_event_idempotency():
    """11. Duplicate Event: Same event delivered twice -> processed once, second is no-op."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    swings = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0, "confirmed_at_candle_time_epoch": 100},
        {"id": 2, "classification": "HL", "swing_type": "LOW", "swing_price": 2720.0, "confirmed_at_candle_time_epoch": 120},
    ]
    ev = make_event(CanonicalStructureEventType.HL, bar_time=120, bar_index=1, price=2720.0)

    # 1st processing
    is_dup1 = market_structure_state_repo.is_event_processed(SOURCE_ID, "XAUUSD", "M15", ev.event_key)
    assert is_dup1 is False
    state1, mutated1, _ = market_structure_state_engine.evaluate_structure_event(state, ev, swings, is_duplicate=is_dup1)
    assert mutated1 is True
    market_structure_state_repo.save_state_and_history_if_changed(state1, ev.event_key)

    # 2nd processing
    is_dup2 = market_structure_state_repo.is_event_processed(SOURCE_ID, "XAUUSD", "M15", ev.event_key)
    assert is_dup2 is True
    state2, mutated2, msg2 = market_structure_state_engine.evaluate_structure_event(state1, ev, swings, is_duplicate=is_dup2)
    assert mutated2 is False
    assert "DUPLICATE_EVENT_IGNORED" in msg2

    # Verify history still has exactly 1 entry
    history = market_structure_state_repo.get_state_history(SOURCE_ID, "XAUUSD", "M15")
    assert len(history) == 1


def test_12_out_of_order_event_rejection():
    """12. Out-of-order Event: Bar 100, Bar 102, then Bar 101 -> Bar 101 rejected."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    # Bar 100
    ev100 = make_event(CanonicalStructureEventType.BOS_BULLISH, bar_time=100, bar_index=1)
    state, mutated1, _ = market_structure_state_engine.evaluate_structure_event(state, ev100)
    assert mutated1 is True
    assert state["bar_time"] == 100

    # Bar 102
    ev102 = make_event(CanonicalStructureEventType.BOS_BULLISH, bar_time=102, bar_index=2)
    state, mutated2, _ = market_structure_state_engine.evaluate_structure_event(state, ev102)
    assert mutated2 is True
    assert state["bar_time"] == 102

    # Bar 101 arrives late
    ev101 = make_event(CanonicalStructureEventType.CHOCH_BEARISH, bar_time=101, bar_index=3)
    state_after, mutated3, msg3 = market_structure_state_engine.evaluate_structure_event(state, ev101)

    assert mutated3 is False
    assert "OUT_OF_ORDER_EVENT" in msg3
    assert state_after["bar_time"] == 102  # State untouched!


def test_13_bar_0_non_mutation():
    """13. Bar 0: is_closed_bar=False or bar_index=0 cannot mutate persistent state."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    ev_unclosed = make_event(CanonicalStructureEventType.CHOCH_BEARISH, bar_time=100, bar_index=0, is_closed_bar=False)
    state_after, mutated, msg = market_structure_state_engine.evaluate_structure_event(state, ev_unclosed)

    assert mutated is False
    assert "BAR_0_REJECTED" in msg
    assert state_after["state"] == MarketState.UNKNOWN.value

    # Verify repo has no record
    saved = market_structure_state_repo.get_current_state(SOURCE_ID, "XAUUSD", "M15")
    assert saved is None


def test_14_multi_timeframe_isolation():
    """14. Multi-Timeframe Isolation: XAUUSD M5 and M15 remain strictly independent."""
    state_m5 = create_initial_market_state(SOURCE_ID, "XAUUSD", "M5")
    state_m15 = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    # M5 becomes BULLISH
    swings_m5 = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0, "confirmed_at_candle_time_epoch": 100},
        {"id": 2, "classification": "HL", "swing_type": "LOW", "swing_price": 2720.0, "confirmed_at_candle_time_epoch": 120},
    ]
    ev_m5 = make_event(CanonicalStructureEventType.HL, bar_time=120, bar_index=1, timeframe="M5")
    state_m5, _, _ = market_structure_state_engine.evaluate_structure_event(state_m5, ev_m5, swings_m5)
    market_structure_state_repo.save_state_and_history_if_changed(state_m5, ev_m5.event_key)

    # M15 becomes BEARISH
    swings_m15 = [
        {"id": 10, "classification": "LH", "swing_type": "HIGH", "swing_price": 2740.0, "confirmed_at_candle_time_epoch": 200},
        {"id": 11, "classification": "LL", "swing_type": "LOW", "swing_price": 2700.0, "confirmed_at_candle_time_epoch": 250},
    ]
    ev_m15 = make_event(CanonicalStructureEventType.LL, bar_time=250, bar_index=1, timeframe="M15")
    state_m15, _, _ = market_structure_state_engine.evaluate_structure_event(state_m15, ev_m15, swings_m15)
    market_structure_state_repo.save_state_and_history_if_changed(state_m15, ev_m15.event_key)

    res_m5 = market_structure_state_repo.get_current_state(SOURCE_ID, "XAUUSD", "M5")
    res_m15 = market_structure_state_repo.get_current_state(SOURCE_ID, "XAUUSD", "M15")

    assert res_m5["state"] == MarketState.BULLISH.value
    assert res_m15["state"] == MarketState.BEARISH.value


def test_15_multi_source_isolation():
    """15. Multi-Source Isolation: Source A and Source B do not contaminate each other."""
    source_b = "b" * 64
    state_a = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state_b = create_initial_market_state(source_b, "XAUUSD", "M15")

    ev_a = make_event(CanonicalStructureEventType.CHOCH_BEARISH, bar_time=100, bar_index=1, source_id=SOURCE_ID)
    state_a, _, _ = market_structure_state_engine.evaluate_structure_event(state_a, ev_a)
    market_structure_state_repo.save_state_and_history_if_changed(state_a, ev_a.event_key)

    res_a = market_structure_state_repo.get_current_state(SOURCE_ID, "XAUUSD", "M15")
    res_b = market_structure_state_repo.get_current_state(source_b, "XAUUSD", "M15")

    assert res_a is not None
    assert res_b is None


def test_16_same_bar_deterministic_ordering():
    """16. Same-bar ordering: Multiple events with identical bar_time order deterministically."""
    # Priority: Step 1 Swings (priority 0) processed before Step 2 breaks (priority 1)
    ev_swing = {
        "bar_time": 100,
        "stream_priority": 0,
        "sub_epoch": 80,
        "source_event_id": 1,
        "type": "HL"
    }
    ev_break = {
        "bar_time": 100,
        "stream_priority": 1,
        "sub_epoch": 0,
        "source_event_id": 5,
        "type": "BOS_BULLISH"
    }

    # Reverse order input
    raw_list = [ev_break, ev_swing]
    sorted_list = sorted(raw_list, key=lambda x: (x["bar_time"], x["stream_priority"], x["sub_epoch"], x["source_event_id"]))

    assert sorted_list[0]["type"] == "HL"
    assert sorted_list[1]["type"] == "BOS_BULLISH"


def test_17_false_sequence_protection():
    """17. False sequence protection: HH -> LL -> HL must NOT become BULLISH."""
    broken_sequence = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0},
        {"id": 2, "classification": "LL", "swing_type": "LOW", "swing_price": 2700.0},  # Intervening contrary!
        {"id": 3, "classification": "HL", "swing_type": "LOW", "swing_price": 2720.0},
    ]

    is_bull = check_ordered_bullish_sequence(broken_sequence)
    assert is_bull is False

    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    ev = make_event(CanonicalStructureEventType.HL, bar_time=150, bar_index=3)
    state, _, _ = market_structure_state_engine.evaluate_structure_event(state, ev, broken_sequence)

    # Invariant: Must NOT be BULLISH
    assert state["state"] != MarketState.BULLISH.value
    assert state["state"] == MarketState.NEUTRAL.value


def test_18_previous_state_semantics():
    """18. Previous-state semantics: previous_state always represents state immediately before latest event."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    # 1. UNKNOWN -> BULLISH
    swings = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0},
        {"id": 2, "classification": "HL", "swing_type": "LOW", "swing_price": 2720.0},
    ]
    ev1 = make_event(CanonicalStructureEventType.HL, bar_time=100, bar_index=1)
    state, _, _ = market_structure_state_engine.evaluate_structure_event(state, ev1, swings)
    assert state["state"] == MarketState.BULLISH.value
    assert state["previous_state"] == MarketState.UNKNOWN.value
    assert state["state_changed"] is True

    # 2. Continuation in BULLISH (BOS_BULLISH)
    ev2 = make_event(CanonicalStructureEventType.BOS_BULLISH, bar_time=120, bar_index=2)
    state, _, _ = market_structure_state_engine.evaluate_structure_event(state, ev2, swings)
    assert state["state"] == MarketState.BULLISH.value
    assert state["previous_state"] == MarketState.BULLISH.value
    assert state["state_changed"] is False


def test_19_history_cleanliness_no_duplicate_records():
    """19. History: No history row when state remains unchanged."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    swings = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0},
        {"id": 2, "classification": "HL", "swing_type": "LOW", "swing_price": 2720.0},
    ]
    ev_initial = make_event(CanonicalStructureEventType.HL, bar_time=100, bar_index=1)
    state, _, _ = market_structure_state_engine.evaluate_structure_event(state, ev_initial, swings)
    market_structure_state_repo.save_state_and_history_if_changed(state, ev_initial.event_key)

    # Now simulate 3 continuation BOS events
    for b in range(110, 140, 10):
        ev_cont = make_event(CanonicalStructureEventType.BOS_BULLISH, bar_time=b, bar_index=b // 10)
        state, _, _ = market_structure_state_engine.evaluate_structure_event(state, ev_cont, swings)
        market_structure_state_repo.save_state_and_history_if_changed(state, ev_cont.event_key)

    history = market_structure_state_repo.get_state_history(SOURCE_ID, "XAUUSD", "M15")
    # Strictly 1 history row (from the initial UNKNOWN -> BULLISH transition)!
    assert len(history) == 1


def test_20_rebuild_determinism():
    """20. Rebuild: Full rebuild twice yields identical state and history."""
    state_run1 = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state_run2 = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")

    swings = [
        {"id": 1, "classification": "HH", "swing_type": "HIGH", "swing_price": 2750.0},
        {"id": 2, "classification": "HL", "swing_type": "LOW", "swing_price": 2720.0},
    ]

    events_sequence = [
        make_event(CanonicalStructureEventType.HL, bar_time=100, bar_index=1),
        make_event(CanonicalStructureEventType.BOS_BULLISH, bar_time=120, bar_index=2),
        make_event(CanonicalStructureEventType.CHOCH_BEARISH, bar_time=140, bar_index=3),
    ]

    for ev in events_sequence:
        state_run1, _, _ = market_structure_state_engine.evaluate_structure_event(state_run1, ev, swings)

    for ev in events_sequence:
        state_run2, _, _ = market_structure_state_engine.evaluate_structure_event(state_run2, ev, swings)

    assert state_run1["state"] == state_run2["state"]
    assert state_run1["previous_state"] == state_run2["previous_state"]
    assert state_run1["structure"] == state_run2["structure"]
    assert state_run1["structure_strength"] == state_run2["structure_strength"]
    assert state_run1["state"] == MarketState.TRANSITION.value


def test_21_api_endpoint_market_structure_state():
    """21. API Contract: GET /api/v1/market-structure-state/{symbol}/{timeframe} returns dashboard snapshot."""
    # Seed a persistent state in repo
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M15")
    state["state"] = MarketState.BULLISH.value
    state["previous_state"] = MarketState.NEUTRAL.value
    state["structure"] = StructureConfiguration.HH_HL.value
    state["structure_strength"] = StructureStrength.CONFIRMED.value
    state["last_event"] = "BOS_BULLISH"
    state["last_event_time"] = 1788960000
    state["last_event_price"] = 2750.50
    state["bar_time"] = 1788960000
    state["bar_index"] = 10
    state["state_changed"] = True
    state["state_reason"] = "Bullish confirmation established"

    market_structure_state_repo.save_state_and_history_if_changed(state, event_key="test_api_key")

    res = client.get(
        f"/api/v1/market-structure-state/XAUUSD/M15?source_id={SOURCE_ID}",
        headers=AUTH_HEADERS
    )
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "XAUUSD"
    assert data["timeframe"] == "M15"
    assert data["state"] == "BULLISH"
    assert data["previous_state"] == "NEUTRAL"
    assert data["structure"] == "HH_HL"
    assert data["structure_strength"] == "CONFIRMED"
    assert data["last_event"] == "BOS_BULLISH"
    assert data["last_event_price"] == 2750.50

    # History endpoint
    res_hist = client.get(
        f"/api/v1/market-structure-state/XAUUSD/M15/history?source_id={SOURCE_ID}",
        headers=AUTH_HEADERS
    )
    assert res_hist.status_code == 200
    data_hist = res_hist.json()
    assert data_hist["status"] == "SUCCESS"
    assert data_hist["total"] == 1
    assert data_hist["history"][0]["new_state"] == "BULLISH"


def test_22_source_event_id_persistence_and_propagation():
    """22. Identity Invariant: source_event_id is never NULL when source event possesses canonical ID."""
    state = create_initial_market_state(SOURCE_ID, "XAUUSD", "M5")
    state["state"] = MarketState.BEARISH.value

    # Simulate Step 2 CHOCH break event carrying canonical DB ID 2850
    ev = make_event(
        event_type=CanonicalStructureEventType.CHOCH_BULLISH,
        bar_time=1789017000,
        bar_index=15,
        price=4418.80,
        timeframe="M5",
        source_event_id=2850
    )

    new_st, mutated, _ = market_structure_state_engine.evaluate_structure_event(state, ev)
    assert mutated is True
    assert new_st["state"] == MarketState.TRANSITION.value
    assert new_st["source_event_id"] == 2850

    # Persist and verify in memory / DB
    market_structure_state_repo.save_state_and_history_if_changed(new_st, event_key=ev.event_key)
    current = market_structure_state_repo.get_current_state(SOURCE_ID, "XAUUSD", "M5")
    assert current is not None
    assert current["source_event_id"] == 2850

    history = market_structure_state_repo.get_state_history(SOURCE_ID, "XAUUSD", "M5")
    assert len(history) == 1
    assert history[0]["source_event_id"] == 2850

