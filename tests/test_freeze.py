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


if __name__ == "__main__":
    unittest.main()

