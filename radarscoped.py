"""
Radar Scope Daemon

This program displays relative positions of aircraft received with ADSB receiver on the Unicorn Hat HD.
The receiver location is in the middle of the screen.

"""

import json
import math
import time
import urllib.request

import unicornhathd as uh

def lon_length(latitude):
    return 60 * math.cos(math.radians(latitude))

def coord_span(radius, origin=(0, 0)):
    lat = origin[0]
    lon = origin[1]

    lat_delta = float(radius/60.0)
    lon_delta = float(radius/lon_length(lat))

    return {
        "lat": {
            "min": lat - lat_delta,
            "max": lat + lat_delta,
            "delta": lat_delta
        },
        "lon": {
            "min": lon - lon_delta,
            "max": lon + lon_delta,
            "delta": lon_delta
        }
    }

def get_json(url):
    request = urllib.request.urlopen(url)
    data = request.read()
    encoding = request.info().get_content_charset('utf-8')
    json_data = json.loads(data.decode(encoding))
    return json_data

def get_origin():
    data = get_json("http://piradar/dump1090-fa/data/receiver.json")

    latitude = data["lat"]
    longitude = data["lon"]
    return latitude, longitude

def pixel_origin():
    return 8, 8

def get_aircraft():
    data = get_json("http://piradar/dump1090-fa/data/aircraft.json")
    return data["aircraft"]

def pixel_pos(radius, origin, position):
    span = coord_span(radius, origin)
    pixel_radius = 7

    deg_per_px_lat = span["lat"]["delta"] / pixel_radius
    deg_per_px_lon = span["lon"]["delta"] / pixel_radius

    delta_lat = position[0] - origin[0]
    delta_lon = position[1] - origin[1]

    delta_x = delta_lon / deg_per_px_lon
    delta_y = delta_lat / deg_per_px_lat

    x_sign = 1
    y_sign = 1

    if origin[1] < 0:
        x_sign = -1
    if origin[0] < 0:
        y_sign = -1

    x = pixel_origin()[1] + (delta_x * x_sign)
    y = pixel_origin()[0] + (delta_y * y_sign)

    if x < 0:
        x = 0
    if x > 15:
        x = 15

    if y < 0:
        y = 0
    if y > 15:
        y = 15

    return int(x), int(y)


def plot_positions(positions, radius):
    origin = get_origin()

    uh.off()
    rcvr = pixel_origin()
    uh.set_pixel(rcvr[0], rcvr[1], 255, 255, 255)

    for position in positions:
        pixel = pixel_pos(radius, origin, (position[0], position[1]))
        print("aircraft: {}, {}".format(pixel[0], pixel[1]))
        uh.set_pixel(pixel[0], pixel[1], 255, 128, 0)

    uh.show()

def main():
    while True:
        all_aircraft = get_aircraft()
        ac_positions = list()
        for plane in all_aircraft:
            if "lat" in plane:
                lat = plane["lat"]
                lon = plane["lon"]
                alt = plane["altitude"]
                ac_positions.append([lat, lon, alt])
                print("lat: {}, lon: {}, alt: {}".format(lat, lon, alt))

        plot_positions(ac_positions, 72)
        time.sleep(5)


if __name__ == '__main__':
    main()
