import copy
import json
import unittest
from pathlib import Path

from athena.freeze import FreezeValidationError, load_freeze, validate_freeze, validate_traceability


ROOT = Path(__file__).resolve().parents[1]
FREEZE_PATH = ROOT / "config" / "engineering_freeze.json"
TRACEABILITY_PATH = ROOT / "config" / "freeze_traceability.json"


class EngineeringFreezeTests(unittest.TestCase):
    def test_repository_freeze_and_traceability_are_valid(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        status = validate_freeze(freeze)
        traceability = json.loads(TRACEABILITY_PATH.read_text(encoding="utf-8"))
        mapping = validate_traceability(freeze, traceability, ROOT)
        self.assertTrue(status["valid"])
        self.assertEqual(status["layers"], 7)
        self.assertEqual(status["requirements"], 17)
        self.assertEqual(mapping["requirements"], 17)
        self.assertEqual(mapping["status_counts"], {
            "blocked_external": 2,
            "implemented": 1,
            "partial": 9,
            "pending": 2,
            "scaffolded": 3,
        })

    def test_layer_removal_is_rejected(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        changed = copy.deepcopy(freeze)
        changed["layers"].pop()
        with self.assertRaises(FreezeValidationError):
            validate_freeze(changed)

    def test_target_reduction_is_rejected(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        changed = copy.deepcopy(freeze)
        changed["scale_targets"]["minimum_experiments"] = 249
        with self.assertRaises(FreezeValidationError):
            validate_freeze(changed)

    def test_specialist_and_validation_bypass_is_rejected(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        changed = copy.deepcopy(freeze)
        changed["specialist_services"] = [
            service for service in changed["specialist_services"]
            if service["name"] != "Decision Court"
        ]
        changed["validation"]["required_stages"].remove("Monte Carlo")
        with self.assertRaises(FreezeValidationError):
            validate_freeze(changed)

    def test_every_requirement_must_be_mapped(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        traceability = json.loads(TRACEABILITY_PATH.read_text(encoding="utf-8"))
        changed = copy.deepcopy(traceability)
        changed["requirements"].pop()
        with self.assertRaises(FreezeValidationError):
            validate_traceability(freeze, changed, ROOT)

    def test_market_data_resolution_cannot_be_broadened_or_weakened(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        changed = copy.deepcopy(freeze)
        changed["approved_market_data"]["universe"].append("XAU/USD")
        changed["approved_market_data"]["live_execution"] = "permitted"
        with self.assertRaises(FreezeValidationError):
            validate_freeze(changed)

    def test_runtime_persistence_resolution_cannot_be_weakened(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        changed = copy.deepcopy(freeze)
        changed["approved_runtime_persistence"]["redis_canonical_records_permitted"] = True
        changed["approved_runtime_persistence"]["recovery_point_objective_minutes"] = 1440
        changed["approved_runtime_persistence"]["production_ready"] = True
        with self.assertRaises(FreezeValidationError):
            validate_freeze(changed)

    def test_idempotent_cycle_control_cannot_be_weakened(self) -> None:
        freeze = load_freeze(FREEZE_PATH)
        changed = copy.deepcopy(freeze)
        changed["approved_idempotent_cycle"]["validation_on_every_invocation"] = False
        changed["approved_idempotent_cycle"]["append_ledger_records"] = 1
        changed["approved_idempotent_cycle"]["decision_court_bypass"] = "permitted"
        with self.assertRaises(FreezeValidationError):
            validate_freeze(changed)


if __name__ == "__main__":
    unittest.main()
