"""
Unit testing of the radarscoped.py module

To run:
nosetests -s test_radarscoped.py
"""

import unittest
import radarscoped


class RadarScopeTestCase(unittest.TestCase):

    def setUp(self):
        self.radard = radarscoped.RadarDaemon('/tmp/test_radard.pid')
        self.radard.config_file = 'radarscope.conf'

    def test_configuration(self):
        self.radard.configure()

        self.assertEqual(self.radard.username, 'pi')
        self.assertEqual(self.radard.scope_radius, 72)
        self.assertEqual(self.radard.adsb_host, 'localhost')
        self.assertEqual(self.radard.aircrafturl, 'http://localhost/dump1090-fa/data/aircraft.json')
        self.assertEqual(self.radard.receiverurl, 'http://localhost/dump1090-fa/data/receiver.json')

        self.assertEqual(len(self.radard.airports), 4)

        eidw = self.radard.airports[0]
        egns = self.radard.airports[3]
        self.assertEqual(eidw['icao_code'], 'eidw')
        self.assertEqual(eidw['lat'], 53.45)
        self.assertEqual(eidw['lon'], -6.27)

        self.assertEqual(egns['icao_code'], 'egns')
        self.assertEqual(egns['lat'], 54.08)
        self.assertEqual(egns['lon'], -4.63)

    def test_add_airport(self):
        eick = {
            'icao_code': 'eick',
            'lat': 51.00,
            'lon': -8.00,
        }

        self.radard.add_airport(eick['icao_code'], eick['lat'], eick['lon'])
        self.assertEqual(self.radard.airports[0], eick)

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


if __name__ == '__main__':
    unittest.main()
