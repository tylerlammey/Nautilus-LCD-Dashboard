import io
import pathlib
import struct
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
from PIL import Image
from dashboard_core import DEFAULT_CONFIG, countdown, encode_image, image_packets, merge_config, render_frame, select_quotas, select_sensors, temperature_percent


class CoreTests(unittest.TestCase):
    def test_temperature_ring_scale_and_clamping(self):
        scale={"min_c":20,"max_c":100}
        for temperature,expected in [(0,0),(20,0),(40,25),(60,50),(80,75),(100,100),(130,100)]:
            self.assertEqual(temperature_percent(temperature,scale),expected)
        self.assertIsNone(temperature_percent(None,scale))

    def test_partial_settings_preserve_nested_defaults(self):
        config=merge_config({"theme":{"cpu":"#123456"},"rotation":180})
        self.assertEqual(config["theme"]["cpu"],"#123456")
        self.assertEqual(config["theme"]["gpu"],DEFAULT_CONFIG["theme"]["gpu"])
        self.assertEqual(config["temperature_range"],{"min_c":20,"max_c":100})
        self.assertEqual(config["rotation"],180)

    def test_bad_settings_rejected(self):
        for patch in ({"theme":{"cpu":"red"}},{"temperature_range":{"min_c":100,"max_c":20}},
                      {"layout":{"temperature_font_size":1000}},{"remaining":"false"}):
            with self.assertRaises(ValueError):
                merge_config(patch)

    def test_discrete_gpu_core_selection(self):
        def sensor(hardware,kind,name,value):
            return dict(hardware=hardware,hardwareType=kind,type="Temperature",name=name,value=value)
        sensors=[sensor("AMD Radeon Graphics","GpuAmd","GPU Core",45),
                 sensor("AMD Radeon RX 9070 XT","GpuAmd","GPU Hot Spot",93),
                 sensor("AMD Radeon RX 9070 XT","GpuAmd","GPU Core",61),
                 sensor("AMD Ryzen 7 7800X3D","Cpu","Core (Tctl/Tdie)",52)]
        readings=select_sensors(sensors)
        self.assertEqual(readings["cpu_temp"],52)
        self.assertEqual(readings["gpu_temp"],61)
        self.assertIn("9070",readings["gpu_name"])

    def test_unavailable_temperature_is_not_zero(self):
        readings=select_sensors([dict(hardware="AMD CPU",hardwareType="Cpu",type="Temperature",name="Core (Tctl/Tdie)",value=0)])
        self.assertIsNone(readings["cpu_temp"])

    def test_quota_windows_are_identified_by_duration(self):
        output=select_quotas({"rateLimitsByLimitId":{"codex":{"primary":{"usedPercent":12,"windowDurationMins":10080,"resetsAt":100},"secondary":{"usedPercent":23,"windowDurationMins":300,"resetsAt":50}}}})
        self.assertEqual(output["five_hour"]["used"],23)
        self.assertEqual(output["weekly"]["used"],12)

    def test_unknown_quota_is_unavailable(self):
        output=select_quotas({"rateLimits":{"primary":{"usedPercent":12,"windowDurationMins":60}}})
        self.assertIsNone(output["five_hour"])
        self.assertIsNone(output["weekly"])

    def test_final_packet_for_exact_multiple(self):
        payload=bytes(range(254))*8
        packets=list(image_packets(payload))
        self.assertEqual(len(packets),2)
        self.assertEqual(packets[0][3],0)
        self.assertEqual(packets[1][3],1)
        restored=b"".join(p[8:8+struct.unpack_from("<H",p,6)[0]] for p in packets)
        self.assertEqual(restored,payload)

    def test_final_partial_packet_and_report_size(self):
        packets=list(image_packets(b"jpeg"*300))
        self.assertEqual([len(p) for p in packets],[1024,1024])
        self.assertEqual(struct.unpack_from("<H",packets[-1],6)[0],184)
        self.assertEqual(packets[-1][3],1)

    def test_packet_limits(self):
        for data in (b"",b"x"*(1016*256+1)):
            with self.assertRaises(ValueError):
                list(image_packets(data))

    def test_render_missing_and_extreme_values(self):
        for state in ({},{"hardware":{"cpu_temp":130,"gpu_temp":130,"cpu_load":100,"gpu_load":0},"quotas":{"five_hour":{"used":100,"resets_at":None},"weekly":{"used":0,"resets_at":None}}}):
            frame=render_frame(state,DEFAULT_CONFIG)
            self.assertEqual(frame.size,(480,480))
            encoded=encode_image(frame,"JPEG")
            Image.open(io.BytesIO(encoded)).verify()
            self.assertLess(len(encoded),1016*256)

    def test_reset_is_not_fabricated(self):
        self.assertEqual(countdown(None),"")
        self.assertEqual(countdown(99,100),"0m")
        for seconds, expected in [(0,"0m"),(59,"0m"),(60,"1m"),(3599,"59m"),
                                  (3600,"1h"),(4.9*3600,"4h 54m"),
                                  (5*3600,"5h"),(5*3600-1,"4h 59m"),
                                  (86399,"23h 59m"),
                                  (86400,"1d"),(86400+3599,"1d"),
                                  (86400+3600,"1d 1h"),
                                  (3*86400+6*3600+59*60,"3d 6h")]:
            with self.subTest(seconds=seconds):
                self.assertEqual(countdown(100+seconds,100),expected)


if __name__=="__main__":
    unittest.main()
