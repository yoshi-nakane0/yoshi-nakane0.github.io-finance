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
        saved_display_status = (
            (world_model.get("output_contract") or {}).get("display_status")
            or world_model.get("display_status")
            or ""
        )
        if saved_display_status and saved_display_status not in ALLOWED_DISPLAY_STATUSES:
            raise CommandError(
                f"basecalc output contract failed: display_status is not allowed: {saved_display_status}"
            )
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
