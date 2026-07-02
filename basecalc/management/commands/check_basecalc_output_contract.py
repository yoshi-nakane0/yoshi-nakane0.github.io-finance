from django.core.management.base import BaseCommand, CommandError

from basecalc.output_contract import apply_output_contract
from basecalc.snapshot import load_basecalc_snapshot
from basecalc.validation_report import load_validation_report


ALLOWED_DISPLAY_STATUSES = {
    "blocked",
    "watch_only",
    "candidate_limited",
    "candidate_confirmed",
}
ALLOWED_EXPLANATION_ALLOWED_STATUSES = {"blocked", "limited", "allowed", "confirmed"}
CONTRACT_STATUS_EXPLANATION_ALLOWED = {
    "error": {"blocked"},
    "limited": {"limited"},
    "ok": {"allowed"},
    "confirmed": {"confirmed"},
}
DISPLAY_STATUS_EXPLANATION_ALLOWED = {
    "blocked": {"blocked"},
    "watch_only": {"allowed", "limited"},
    "candidate_limited": {"allowed", "limited"},
    "candidate_confirmed": {"confirmed"},
}


class Command(BaseCommand):
    help = "Fail when the saved basecalc snapshot contains contradictory display output."

    def add_arguments(self, parser):
        parser.add_argument(
            "--snapshot",
            default=None,
            help="Path to the basecalc snapshot JSON. Defaults to basecalc/data/latest_snapshot.json.",
        )
        parser.add_argument(
            "--validation-report",
            default=None,
            help="Optional validation report path.",
        )

    def handle(self, *args, **options):
        payload = load_basecalc_snapshot(options.get("snapshot"))
        if not payload:
            raise CommandError("basecalc output contract failed: snapshot is missing")
        world_model = payload.get("world_model") or (payload.get("data") or {}).get("world_model") or {}
        if not isinstance(world_model, dict):
            raise CommandError("basecalc output contract failed: world_model is missing")

        validation_report = load_validation_report(options.get("validation_report")) if options.get("validation_report") else None
        display_price = (
            (world_model.get("output_contract") or {}).get("display_price")
            or world_model.get("display_price")
            or world_model.get("price")
        )
        output_contract = world_model.get("output_contract") or {}
        saved_display_status = output_contract.get("display_status") or world_model.get("display_status") or ""
        if saved_display_status and saved_display_status not in ALLOWED_DISPLAY_STATUSES:
            raise CommandError(
                f"basecalc output contract failed: display_status is not allowed: {saved_display_status}"
            )
        saved_explanation_allowed = _contract_value(output_contract, world_model, "explanation_allowed")
        if saved_explanation_allowed not in (None, "") and saved_explanation_allowed not in ALLOWED_EXPLANATION_ALLOWED_STATUSES:
            raise CommandError(
                f"basecalc output contract failed: explanation_allowed is not allowed: {saved_explanation_allowed}"
            )
        _assert_contract_explanation_allowed_match(output_contract.get("contract_status") or "", saved_explanation_allowed)
        _assert_display_explanation_allowed_match(saved_display_status, saved_explanation_allowed)
        _assert_saved_contract_consistency(output_contract)
        contract = apply_output_contract(
            world_model,
            display_price=display_price,
            validation_report=validation_report,
            performance_by_horizon=payload.get("backtest_performance_by_horizon") or {},
        )
        if contract.get("contract_status") == "error":
            unsafe_display = (
                contract.get("target_display_allowed")
                or contract.get("probability_display_allowed")
                or contract.get("allowed_direction") != "stopped"
            )
            reasons = " / ".join(contract.get("stop_reasons") or ["unknown"])
            if unsafe_display:
                raise CommandError(f"basecalc output contract failed: {reasons}")
            self.stdout.write(
                self.style.WARNING(
                    "basecalc output contract stopped directional display: " + reasons
                )
            )
        blocked_horizons = [
            horizon
            for horizon, row in (contract.get("allowed_horizons") or {}).items()
            if not row.get("direction_allowed")
        ]
        if blocked_horizons:
            self.stdout.write(
                self.style.WARNING(
                    "basecalc direction gate limited: " + ", ".join(blocked_horizons)
                )
            )
        self.stdout.write(self.style.SUCCESS("basecalc output contract ok"))


def _assert_saved_contract_consistency(output_contract):
    if not isinstance(output_contract, dict):
        return
    if output_contract.get("contract_status") != "error":
        return
    display_status = output_contract.get("display_status")
    if display_status and display_status != "blocked":
        raise CommandError("basecalc output contract failed: error contract display_status must be blocked")
    explanation_allowed = output_contract.get("explanation_allowed")
    if explanation_allowed and explanation_allowed != "blocked":
        raise CommandError("basecalc output contract failed: error contract explanation_allowed must be blocked")
    confidence_score = output_contract.get("confidence_score")
    if confidence_score not in (None, "", 0, 0.0):
        raise CommandError("basecalc output contract failed: error contract confidence_score must be 0")
    confidence_label = output_contract.get("confidence_label")
    if confidence_label and confidence_label != "D":
        raise CommandError("basecalc output contract failed: error contract confidence_label must be D")


def _contract_value(output_contract, world_model, key):
    if isinstance(output_contract, dict) and key in output_contract:
        return output_contract.get(key)
    if isinstance(world_model, dict) and key in world_model:
        return world_model.get(key)
    return None


def _assert_display_explanation_allowed_match(display_status, explanation_allowed):
    if not display_status or explanation_allowed in (None, ""):
        return
    allowed = DISPLAY_STATUS_EXPLANATION_ALLOWED.get(display_status)
    if allowed and explanation_allowed not in allowed:
        expected = " or ".join(sorted(allowed))
        raise CommandError(
            f"basecalc output contract failed: {display_status} explanation_allowed must be {expected}"
        )


def _assert_contract_explanation_allowed_match(contract_status, explanation_allowed):
    if not contract_status or explanation_allowed in (None, ""):
        return
    allowed = CONTRACT_STATUS_EXPLANATION_ALLOWED.get(contract_status)
    if allowed and explanation_allowed not in allowed:
        expected = " or ".join(sorted(allowed))
        raise CommandError(
            f"basecalc output contract failed: {contract_status} contract explanation_allowed must be {expected}"
        )
