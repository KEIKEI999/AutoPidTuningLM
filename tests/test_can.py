from __future__ import annotations

import json
import unittest

from orchestrator.can_codec import (
    CanCodecError,
    CanCodecStatus,
    pack_control_output,
    pack_setpoint,
    unpack_control_output,
    unpack_setpoint,
)
from orchestrator.can_if import CanIfStatus, VirtualCanBus, can_if_init
from tests.test_support import FIXTURES_DIR


class CanCodecAndIfTest(unittest.TestCase):
    def test_pack_unpack_known_vector(self) -> None:
        vectors = json.loads((FIXTURES_DIR / "codec_vectors.json").read_text(encoding="utf-8"))
        frame = pack_setpoint(vectors["setpoint_1_234"]["value"])
        self.assertEqual(list(frame.data), vectors["setpoint_1_234"]["bytes"])
        self.assertAlmostEqual(unpack_setpoint(frame), 1.234, places=3)

        control = pack_control_output(vectors["control_negative_0_250"]["value"])
        self.assertEqual(list(control.data), vectors["control_negative_0_250"]["bytes"])
        self.assertAlmostEqual(unpack_control_output(control), -0.25, places=3)

    def test_invalid_dlc_is_detected(self) -> None:
        frame = pack_setpoint(1.0)
        frame.dlc = 7
        with self.assertRaises(CanCodecError) as ctx:
            unpack_setpoint(frame)
        self.assertEqual(ctx.exception.status, CanCodecStatus.INVALID_DLC)

    def test_virtual_can_send_receive(self) -> None:
        bus = VirtualCanBus()
        sender = can_if_init(bus)
        receiver = can_if_init(bus)
        self.assertEqual(sender.open(), CanIfStatus.OK)
        self.assertEqual(receiver.open(), CanIfStatus.OK)
        frame = pack_setpoint(1.0)
        self.assertEqual(sender.send(frame), CanIfStatus.OK)
        status, received = receiver.receive()
        self.assertEqual(status, CanIfStatus.OK)
        self.assertIsNotNone(received)
        self.assertEqual(received.id, frame.id)
        self.assertEqual(list(received.data), list(frame.data))

