import re
from pathlib import Path

def test_mql5_source_code_architecture_invariants():
    mq5_path = Path(__file__).resolve().parent.parent.parent / "Alped_Bridge.mq5"
    assert mq5_path.exists(), f"Alped_Bridge.mq5 not found at {mq5_path}"

    content = mq5_path.read_text(encoding="utf-8")

    # 1. Verify AllowExecution is strictly false
    assert "input bool     AllowExecution       = false;" in content

    # 2. Extract handler blocks
    def extract_function_body(func_name: str) -> str:
        pattern = rf"{func_name}\s*\([^)]*\)\s*\{{"
        match = re.search(pattern, content)
        assert match, f"Function {func_name} not found in MQL5 file"
        start = match.end()
        brace_count = 1
        i = start
        while i < len(content) and brace_count > 0:
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
            i += 1
        return content[start:i-1]

    on_init_body = extract_function_body("int OnInit")
    on_tick_body = extract_function_body("void OnTick")
    on_trade_trans_body = extract_function_body("void OnTradeTransaction")

    # 3. Verify ZERO WebRequest in OnInit, OnTick, and OnTradeTransaction
    assert "WebRequest" not in on_init_body, "CRITICAL VIOLATION: WebRequest found inside OnInit()!"
    assert "WebRequest" not in on_tick_body, "CRITICAL VIOLATION: WebRequest found inside OnTick()!"
    assert "WebRequest" not in on_trade_trans_body, "CRITICAL VIOLATION: WebRequest found inside OnTradeTransaction()!"

    # 4. Verify WebRequest is strictly located in HttpSend, which is invoked by OnTimer state machine
    assert "WebRequest" in content
    assert "HttpSend" in extract_function_body("void OnTimer") or "PerformHandshake" in extract_function_body("void OnTimer")

    # 5. Verify ZERO strategy indicator calls or trading executions
    forbidden_symbols = [
        "iRSI", "iMA", "iMACD", "iBands", "iStochastic",
        "OrderSend", "ctrade", "PositionClose", "Trade.Buy", "Trade.Sell"
    ]
    for sym in forbidden_symbols:
        assert not re.search(rf"\b{sym}\b", content, re.IGNORECASE), f"CRITICAL VIOLATION: Indicator or execution logic '{sym}' detected in MQL5 EA!"
