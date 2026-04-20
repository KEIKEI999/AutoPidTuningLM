from __future__ import annotations

import json
import unittest

from orchestrator.can_if import CanIfStatus, VirtualCanBus, can_if_init
from orchestrator.config import load_config_bundle
from plant.roundtrip import run_plant_roundtrip
from tests.test_support import REPO_ROOT, workspace_temp_dir


class PlantRoundtripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_config_bundle(REPO_ROOT / "configs" / "target_response.yaml", case_name="first_order_nominal")

    def test_virtual_roundtrip_returns_measurement_and_heartbeat(self) -> None:
        with workspace_temp_dir() as tmp:
            bus = VirtualCanBus()
            host_handle = can_if_init(bus)
            plant_handle = can_if_init(bus)
            self.assertEqual(host_handle.open(), CanIfStatus.OK)
            self.assertEqual(plant_handle.open(), CanIfStatus.OK)
            try:
                result = run_plant_roundtrip(
                    tmp,
                    host_handle,
                    plant_handle,
                    self.bundle.plant_cases[0],
                    self.bundle.target,
                    seed=123,
                    steps=8,
                    control_output=1.0,
                    timeout_ms=10,
                )
            finally:
                plant_handle.deinit()
                host_handle.deinit()

            self.assertTrue(result.waveform_path.exists())
            self.assertTrue(result.summary_path.exists())
            self.assertEqual(result.measurement_count, 8)
            self.assertEqual(result.heartbeat_count, 8)
            self.assertGreater(result.last_measurement, 0.0)
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["measurement_count"], 8)


if __name__ == "__main__":
    unittest.main()
