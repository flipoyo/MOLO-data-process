import math
import unittest

from raw_to_processed.readers import decode_sensor_data, encode_sensor_data


class Codec(unittest.TestCase):
    def test_roundtrip(self):
        meas = [1234.0, 2048.0, 4095.0, 0.0, 17.0, 3000.0]
        ui, t, m = decode_sensor_data(encode_sensor_data("VIRT-0001", 1007241200, meas))
        self.assertEqual((ui, t, m), ("VIRT-0001", 1007241200, meas))

    def test_missing_measurements_are_just_shorter(self):
        _, _, m = decode_sensor_data(encode_sensor_data("A", 1, [5.0]))
        self.assertEqual(m, [5.0])

    def test_against_the_official_protobuf_library(self):
        """Contrôle indépendant : le codec écrit à la main doit produire et lire les mêmes octets que protobuf."""
        try:
            from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
            F = descriptor_pb2.FieldDescriptorProto
            fdp = descriptor_pb2.FileDescriptorProto(name="sensor_test.proto")
            msg = fdp.message_type.add(name="SensorData")
            msg.field.add(name="UI", number=1, type=F.TYPE_STRING, label=F.LABEL_REQUIRED)
            msg.field.add(name="time", number=2, type=F.TYPE_INT32, label=F.LABEL_REQUIRED)
            msg.field.add(name="measurements", number=3, type=F.TYPE_FLOAT, label=F.LABEL_REPEATED)
            pool = descriptor_pool.DescriptorPool()
            pool.Add(fdp)
            cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("SensorData"))
        except Exception as exc:                                   # protobuf absent ou API différente
            self.skipTest(f"protobuf indisponible : {exc}")
        meas = [1234.0, 2048.0, 4095.0, 1.5]
        official = cls(UI="VIRT-0001", time=1007241200)
        official.measurements.extend(meas)
        data = official.SerializeToString()
        self.assertEqual(encode_sensor_data("VIRT-0001", 1007241200, meas), data)
        self.assertEqual(decode_sensor_data(data), ("VIRT-0001", 1007241200, meas))


if __name__ == "__main__":
    unittest.main()
