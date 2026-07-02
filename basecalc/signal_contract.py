INCLUDED_INPUTS = [
    "nikkei_futures_ohlcv",
    "nikkei_futures_indicators",
    "nasdaq100_futures_price_action",
    "sp500_futures_price_action",
    "dow_futures_price_action",
]

EXCLUDED_INPUTS = [
    "macro",
    "valuation",
    "fx",
    "rates",
    "vix",
    "sox",
    "oil",
    "news",
]


def build_basecalc_signal_contract(outlook: dict) -> dict:
    outlook = outlook or {}
    output_contract = outlook.get("output_contract") or {}
    return {
        "source": "basecalc",
        "scope": "technical_with_us_index_confirmation",
        "instrument": "nikkei_futures",
        "as_of": outlook.get("as_of") or outlook.get("last_updated_display") or "",
        "price": outlook.get("price"),
        "model_price": output_contract.get("model_price") or outlook.get("model_price") or outlook.get("price"),
        "display_price": output_contract.get("display_price") or outlook.get("display_price") or outlook.get("price"),
        "readiness_level": outlook.get("readiness_level"),
        "directional_allowed": output_contract.get("directional_allowed", outlook.get("directional_allowed")),
        "primary_direction": outlook.get("direction"),
        "allowed_direction": output_contract.get("allowed_direction") or outlook.get("direction"),
        "allowed_horizons": output_contract.get("allowed_horizons") or {},
        "validated_targets": output_contract.get("validated_targets") or {
            "upside": outlook.get("upside_targets") or [],
            "downside": outlook.get("downside_targets") or [],
        },
        "invalidated_targets": output_contract.get("invalidated_targets") or {},
        "stop_reasons": output_contract.get("stop_reasons") or outlook.get("stop_reasons") or [],
        "hard_stop_reasons": output_contract.get("hard_stop_reasons") or outlook.get("hard_stop_reasons") or [],
        "hard_block_reasons": output_contract.get("hard_block_reasons") or outlook.get("hard_block_reasons") or [],
        "soft_warning_reasons": output_contract.get("soft_warning_reasons") or outlook.get("soft_warning_reasons") or [],
        "validation_warnings": output_contract.get("validation_warnings") or outlook.get("validation_warnings") or [],
        "confidence_cap_reason": output_contract.get("confidence_cap_reason") or outlook.get("confidence_cap_reason") or "",
        "display_status": output_contract.get("display_status") or outlook.get("display_status") or "",
        "confidence_calibrated": output_contract.get("confidence_calibrated"),
        "validation_gate_status": output_contract.get("validation_gate_status") or {},
        "contract_status": output_contract.get("contract_status") or outlook.get("contract_status") or "unchecked",
        "counter_bias": outlook.get("counter_bias") or {},
        "scenario_probabilities": outlook.get("scenario_probabilities") or {},
        "action_note": outlook.get("action_note") or "",
        "primary_setup": outlook.get("primary_setup"),
        "technical_regime": outlook.get("technical_regime"),
        "nikkei_technical_score": outlook.get("nikkei_technical_score"),
        "us_index_confirmation_score": outlook.get("us_index_confirmation_score"),
        "confidence_score": outlook.get("confidence_score"),
        "confidence_label": outlook.get("confidence"),
        "horizons": outlook.get("horizons") or {},
        "scenarios": outlook.get("scenarios") or {},
        "levels": {
            "resistance_zones": outlook.get("upside_targets") or [],
            "support_zones": outlook.get("downside_targets") or [],
            "invalidation": outlook.get("invalidation") or {},
        },
        "included_inputs": INCLUDED_INPUTS,
        "excluded_inputs": EXCLUDED_INPUTS,
    }
