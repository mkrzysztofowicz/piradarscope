"""
Unit testing of the radarscoped.py module

To run:
nosetests -s test_radarscoped.py
"""

import json
import socket
import unittest
from multiprocessing import Process

import radarscoped
import mock_httpd

class RadarScopeTestCase(unittest.TestCase):

    def setUp(self):
        self.radard = radarscoped.RadarDaemon('/tmp/test_radard.pid')

    def test_configuration(self):
        self.radard.config_file = 'radarscope.conf'
        self.radard.configure()

        self.assertEqual(self.radard.username, 'pi')
        self.assertEqual(self.radard.scope_radius, 72)
        self.assertEqual(self.radard.scope_brightness, 0.5)
        self.assertEqual(self.radard.airport_brightness, 0.2)
        self.assertEqual(self.radard.scope_rotation, 0)
        self.assertEqual(self.radard.adsb_host, 'localhost:10080')
        self.assertEqual(self.radard.aircrafturl, 'http://localhost:10080/dump1090-fa/data/aircraft.json')
        self.assertEqual(self.radard.receiverurl, 'http://localhost:10080/dump1090-fa/data/receiver.json')

        self.assertEqual(len(self.radard.airports), 4)

        eidw = self.radard.airports[0]
        egns = self.radard.airports[3]
        self.assertEqual(eidw['icao_code'], 'eidw')
        self.assertEqual(eidw['lat'], 53.45)
        self.assertEqual(eidw['lon'], -6.27)

        self.assertEqual(egns['icao_code'], 'egns')
        self.assertEqual(egns['lat'], 54.08)
        self.assertEqual(egns['lon'], -4.63)

    def test_configuration_no_config_file(self):
        self.radard.config_file = None
        self.radard.configure()

        self.assertEqual(self.radard.username, None)
        self.assertEqual(self.radard.scope_radius, 60)
        self.assertEqual(self.radard.scope_brightness, 0.5)
        self.assertEqual(self.radard.airport_brightness, 0.2)
        self.assertEqual(self.radard.scope_rotation, 0)
        self.assertEqual(self.radard.adsb_host, 'localhost')
        self.assertEqual(self.radard.aircrafturl, 'http://localhost/dump1090-fa/data/aircraft.json')
        self.assertEqual(self.radard.receiverurl, 'http://localhost/dump1090-fa/data/receiver.json')

        self.assertEqual(len(self.radard.airports), 0)

    def test_configuration_file_not_exists(self):
        self.radard.config_file = '/this/file/does/not/exist.conf'

        with self.assertRaises(SystemExit) as cm:
            self.radard.configure()

        self.assertEqual(cm.exception.code, 1)

    def test_add_airport(self):
        eick = {
            'icao_code': 'eick',
            'lat': 51.00,
            'lon': -8.00,
        }

        self.radard.add_airport(eick['icao_code'], eick['lat'], eick['lon'])
        self.assertEqual(self.radard.airports[0], eick)

    def test_get_json(self):
        data = self.radard.get_json('http://localhost:10080/dump1090-fa/data/receiver.json')
        self.assertEqual(data['version'], '3.5.3')

    def test_get_aircraft(self):
        self.radard.config_file = 'radarscope.conf'
        self.radard.configure()

        ac = self.radard.get_aircraft()
        self.assertEqual(len(ac), 6)

    def test_get_receiver_origin(self):
        self.radard.config_file = 'radarscope.conf'
        self.radard.configure()

        rcvr = self.radard.get_receiver_origin()
        self.assertEqual(rcvr[0], 53.34)
        self.assertEqual(rcvr[1], -6.22)

    def test_setup_server_socket(self):
        self.assertIsNone(self.radard.socket)

        self.radard.setup_server_socket()
        self.assertEqual(self.radard.socket.family, socket.AF_INET)
        self.assertEqual(self.radard.socket.type, socket.SOCK_STREAM)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', 12345))
        sock.close()
        self.assertEqual(result, 0)

    def test_destroy_server_socket(self):
        self.radard.setup_server_socket()
        self.assertEqual(self.radard.socket.family, socket.AF_INET)
        self.assertEqual(self.radard.socket.type, socket.SOCK_STREAM)

        self.radard.destroy_server_socket()
        self.assertIsNone(self.radard.socket)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', 12345))
        sock.close()
        self.assertNotEqual(result, 0)

    def test_lon_length(self):
        latitude = 30
        self.assertAlmostEqual(self.radard.lon_length(latitude), 51.96, 2)

    def test_coord_span(self):
        radius = 72
        origin = (53, -6)

        span = self.radard.coord_span(radius, origin)
        self.assertAlmostEqual(span["lat"]["min"], 51.8, 2)
        self.assertAlmostEqual(span["lat"]["max"], 54.2, 2)
        self.assertAlmostEqual(span["lat"]["delta"], 1.2, 2)

        self.assertAlmostEqual(span["lon"]["min"], -7.99, 2)
        self.assertAlmostEqual(span["lon"]["max"], -4.006, 3)
        self.assertAlmostEqual(span["lon"]["delta"], 1.99, 2)

    def test_pixel_origin(self):
        px_origin = self.radard.pixel_origin()
        self.assertEqual(8, px_origin[0])
        self.assertEqual(8, px_origin[1])

    def test_pixel_radius(self):
        radius = self.radard.pixel_radius()
        self.assertEqual(8, radius)

    def test_pixel_pos(self):
        origin = (53, -6)

        positions = [
            ((55, -6), (8, 15)),
            ((50, -6), (8, 0)),
            ((53, -10), (0, 8)),
            ((53, -2), (15, 8))
        ]

        for position in positions:
            pixel = self.radard.pixel_pos(72, origin, position[0])
            self.assertEqual(pixel, position[1])

    def test_normalise(self):
        normalise = self.radard.normalise
        n = normalise(22500, min_value=0, max_value=45000, bottom=0.0, top=1.0)
        self.assertEqual(n, 0.5)

        n = normalise(0, min_value=0, max_value=45000, bottom=0.0, top=1.0)
        self.assertEqual(n, 0.0)

        n = normalise(45000, 0, 45000, 0.0, 1.0)
        self.assertEqual(n, 1)

        n = normalise(-1, 0, 100, 0.0, 1.0)
        self.assertEqual(n, 0)

        n = normalise(150, 0, 100, 0.0, 1.0)
        self.assertEqual(n, 1)

        n = normalise(11250)
        self.assertEqual(n, 0.25)

        n = normalise(100, 0, 1000, 0.0, 10.0)
        self.assertEqual(n, 1)

    def test_get_altitude_colour(self):
        a = self.radard.get_altitude_colour(0)
        self.assertEqual(a, (168, 0, 0))

        a = self.radard.get_altitude_colour(45000)
        self.assertEqual(a, (168, 0, 151))

        a = self.radard.get_altitude_colour(-1)
        self.assertEqual(a, (64, 64, 64))

        a = self.radard.get_altitude_colour(None)
        self.assertEqual(a, (64, 64, 64))

        a = self.radard.get_altitude_colour('invalid')
        self.assertEqual(a, (64, 64, 64))


if __name__ == '__main__':
    unittest.main()
