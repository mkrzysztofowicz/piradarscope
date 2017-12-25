#!/usr/bin/env python3

"""
Radar Scope Daemon

This program displays relative positions of aircraft received with ADSB receiver on the UnicornHat HD.
The receiver location is in the middle of the screen.

"""

import argparse
import atexit
import colorsys
import grp
import json
import logging
import logging.handlers
import math
import os
import pwd
import signal
import sys
import time
import urllib.request

import unicornhathd as uh


class Daemon(object):
    """
    A generic daemon class

    Subclass the Daemon class and override the run() method
    """

    def __init__(self, pidfile, stdin='/dev/null', stdout='/dev/null', stderr='/dev/null', daemon_name="Daemon"):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.pidfile = pidfile
        self.username = None
        self.name = daemon_name
        self.logger = logging.getLogger(self.name)
        self.setup_logging()

    def setup_logging(self):
        self.logger.setLevel(logging.DEBUG)

        logformatter = logging.Formatter('%(name)s: [%(levelname)s] %(message)s')

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(logformatter)
        self.logger.addHandler(console_handler)

        syslog_handler = logging.handlers.SysLogHandler('/dev/log')
        syslog_handler.setFormatter(logformatter)
        self.logger.addHandler(syslog_handler)

        # catch all unhandled exceptions
        sys.excepthook = self.exception_log_handler

    def exception_log_handler(self, type, value, tb):
        self.logger.exception('Uncaught exception: {}'.format(str(value)))

    def attach_stream(self, name, mode):
        """
        Replaces the stream with a new one
        """
        stream = open(getattr(self, name), mode)
        os.dup2(stream.fileno(), getattr(sys, name).fileno())

    def dettach_process(self):
        self.fork()  # first fork, detach from parent

        # Become a process group and session group leader
        os.setsid()

        # change to root directory
        os.chdir('/')

        self.fork()     # second fork, relinquish session leadership

    def fork(self):
        """
        Spawn the child process
        """
        try:
            pid = os.fork()
            if pid > 0:
                raise SystemExit(0)  # parent exits
        except OSError as e:
            self.logger.error("Fork failed: {} ({})".format(e.errno, e.strerror))
            raise SystemExit(1)

    def create_pidfile(self):
        atexit.register(self.delete_pidfile)
        pid = str(os.getpid())
        open(self.pidfile, 'w+').write("{}\n".format(pid))

    def delete_pidfile(self):
        os.remove(self.pidfile)

    # noinspection PyMethodMayBeStatic
    def pid_exists(self, pid):
        if pid < 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        else:
            return True

    def get_pid(self):
        try:
            pf = open(self.pidfile, 'r')
            pid = int(pf.read().strip())
            pf.close()
        except (IOError, TypeError):
            pid = None
        return pid

    def status(self):
        pid = self.get_pid()
        if pid and self.pid_exists(pid):
            message = "{} is running, pid: {}".format(self.name, pid)
        else:
            message = "{} not running".format(self.name)

        return {
            "message": message,
            "pid": pid
        }

    def daemonize(self):
        self.dettach_process()

        # Flush I/O buffers
        sys.stdout.flush()
        sys.stderr.flush()

        self.attach_stream('stdin', mode='r')
        self.attach_stream('stdout', mode='a+')
        self.attach_stream('stderr', mode='a+')

        self.create_pidfile()

        # Setup signal handlers
        signal.signal(signal.SIGINT, self.sigterm_handler)
        signal.signal(signal.SIGQUIT, self.sigterm_handler)
        signal.signal(signal.SIGTERM, self.sigterm_handler)

        if self.username:
            self.drop_privileges()

    def start(self, username=None):
        """
        Start the daemon
        """

        self.logger.info("Starting.")

        self.username = username

        # check for a pid to see if the daemon is already running
        pid = self.get_pid()

        if pid:
            if self.pid_exists(pid):
                message = "pidfile {} already exists. {} already running?".format(self.pidfile, self.name)
                self.logger.error(message)
                raise SystemExit(1)
            else:
                message = "removing stale pid file"
                self.logger.info(message)
                self.delete_pidfile()

        # Start the daemon
        self.daemonize()
        self.run()

    def stop(self, silent=False):
        """
        Stop the daemon
        """

        # get the pid from the pidfile
        pid = self.get_pid()

        if not pid:
            if not silent:
                message = "pidfile {} does not exist. {} not running?".format(self.pidfile, self.name)
                self.logger.info(message)
            return  # not an error in a restart

        # Try killing the daemon first
        try:
            while True:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.1)
        except OSError as e:
            e = str(e)
            if e.find("No such process") > 0:
                if os.path.exists(self.pidfile):
                    os.remove(self.pidfile)
            else:
                self.logger.error(e)
                raise SystemExit(1)

    def restart(self):
        """
        Restart the daemon
        """
        self.stop(silent=True)
        self.start()

    def drop_privileges(self):
        """
        Drop privileges if running as root
        """

        if os.getuid() != 0:
            self.logger.info('drop_privileges: not running as root, nothing to do')
            # we're not running as root, so nothing to do
            return

        try:
            pwnam = pwd.getpwnam(self.username)
            uid = pwnam.pw_uid

        except Exception as e:
            self.logger.error(str(e))
            raise SystemExit(1)

        # reset group privileges
        try:
            groups = [g.gr_gid for g in grp.getgrall() if self.username in g.gr_mem]
            os.setgroups(groups)
        except Exception as e:
            self.logger.error(str(e))
            raise SystemExit(1)

        # try setting new uid
        try:
            os.setuid(uid)

        except Exception as e:
            self.logger.error(str(e))
            raise SystemExit(1)

        # ensure reasonable mask
        os.umask(0o22)

    def sigterm_handler(self, signo, frame):
        self.logger.warning("Exiting.".format(signo))
        raise SystemExit(1)

    def run(self):
        """
        Override this method when subclassing Daemon
        """
        raise NotImplementedError


class RadarDaemon(Daemon):
    """
    Subclass of the Daemon class.

    This subclass builds on top of the generic Daemon class to implement a daemon process, reading a list of aircraft
    within the range of the ADSB receiver and plotting their relative positions to that of the receiver on the
    UnicornHAT HD mounted on the host Raspberry PI.
    """

    def __init__(self, pidfile, stdin='/dev/null', stdout='/dev/null', stderr='/dev/null'):
        """
        Override the init() method of the Daemon class to add extra properties.
        """
        super().__init__(pidfile, stdin, stdout, stderr, daemon_name="radarscoped")
        self.adsb_host = 'localhost'
        self.receiverurl = "dump1090-fa/data/receiver.json"
        self.aircrafturl = "dump1090-fa/data/aircraft.json"
        self.scope_radius = None

    @staticmethod
    def lon_length(latitude):
        """
        Calculate the length in Nautical Miles of 1 degree of longitude at a given latitude.
        At the equator, 1 degree of longitude will be 1NM, while at the pole, it will be 0.

        :param float latitude: the latitude at which to calculate the length of 1 degree of longitude.
        :return: a fraction of the Nautical Mile representing the distance of 1 degree of longitude.
        """

        return 60 * math.cos(math.radians(latitude))

    def coord_span(self, radius, origin=(0, 0)):
        """
        Calculate the span of latitudes and longitudes "visible" on the screen for a given radius (in Nautical Miles)
        from the centre position as set by the origin.

        For example, given GPS coordinates of the origin as (53, -6) and a radius of 72 NM, the span of coordinates
        visible on the screen will be:

            minimum latitude:   51.8°
            maximum latitude:   54.2°
            span of latitudes:   1.2°

            minimum longitude:  -7.99°
            maximum longitude:  -4.00°
            span of longitudes:  1.99°

        :param int radius: the radius for which to calculate the lat/lon span
        :param (float, float) origin: GPS coordinates of centre point
        :return: a dictionary with minimum, maximum and spans for latitudes and longitudes
        :rtype: dict
        """

        lat = origin[0]
        lon = origin[1]

        lat_delta = float(radius / 60.0)
        lon_delta = float(radius / self.lon_length(lat))

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

    @staticmethod
    def get_json(url):
        """
        Fetch JSON data from a web server and return a dictionary with same.

        :param str url: URL where to fetch the JSON data from
        :return: a dictionary with JSON data
        :rtype: dict
        """

        request = urllib.request.urlopen(url)
        data = request.read()
        encoding = request.info().get_content_charset('utf-8')
        json_data = json.loads(data.decode(encoding))
        return json_data

    def get_aircraft(self):
        """
        Get a list of aircraft within the range of the ADSB receiver

        :return: a dictionary with all aircraft in the range of the receiver.
        :rtype: dict
        """

        url = 'http://{}/{}'.format(self.adsb_host, self.aircrafturl)
        data = self.get_json(url)
        return data["aircraft"]

    def get_receiver_origin(self):
        """
        Get the GPS coordinates of the ADSB receiver

        :return: lat/lon of the ADSB receiver
        :rtype: (float, float)
        """
        url = 'http://{}/{}'.format(self.adsb_host, self.receiverurl)
        data = self.get_json(url)
        latitude = data["lat"]
        longitude = data["lon"]
        return latitude, longitude

    @staticmethod
    def pixel_origin():
        """
        Get the pixel coordinates of the ADSB receiver on the UnicornHat HD

        This should always be the centre of the LED matrix.
        :return: pixel coordinates of the ADSB receiver
        :rtype: (int, int)
        """

        shape = uh.get_shape()
        x = math.floor(shape[0] / 2)
        y = math.floor(shape[1] / 2)
        return int(x), int(y)

    @staticmethod
    def pixel_radius():
        """
        Find and return the radius in pixels for the LED Matrix.

        This function will return a minimum of half the length or half the height of the screen in pixels.
        :return: a length of radius in pixels
        :rtype: int
        """

        shape = uh.get_shape()
        radius = math.floor(min(shape[0] / 2, shape[1] / 2))
        return radius

    def pixel_pos(self, radius, origin, position):
        """
        Calculate the pixel coordinates for a GPS position given the GPS coordinates of the origin and a radius in
        Nautical Miles.

        :param int radius: radius in Nautical Miles
        :param (float, float) origin: GPS coordinates of the centre point (origin)
        :param (float, float) position: GPS coordinates to plot (i.e. of the aircraft)
        :return: a tuple of pixel coordinates (x, y)
        :rtype: (int, int)
        """

        span = self.coord_span(radius, origin)
        shape = uh.get_shape()

        deg_per_px_lat = span["lat"]["delta"] / self.pixel_radius()
        deg_per_px_lon = span["lon"]["delta"] / self.pixel_radius()

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

        x = self.pixel_origin()[1] + (delta_x * x_sign)
        y = self.pixel_origin()[0] + (delta_y * y_sign)

        if origin[1] < 0:
            x = shape[0] - x
        if origin[0] < 0:
            y = shape[1] - y

        if x < 0:
            x = 0
        if x > shape[0] - 1:
            x = shape[0] - 1

        if y < 0:
            y = 0
        if y > shape[1] - 1:
            y = shape[1] - 1

        return int(x), int(y)

    @staticmethod
    def normalise(value, min_value=0, max_value=45000, bottom=0.0, top=1.0):
        """
        Scale a measured value (by default from a range 0..45000) to a fraction between 0.0 and 1.0.

        All values below or above the min/max range are clipped to min/max respectively.

        The calculation is taken from Ask Dr Math page:
        http://mathforum.org/library/drmath/view/60433.html

        :param float value: actual measured value
        :param float min_value: minimum of the range of values
        :param float max_value: maximum of the range of values
        :param float bottom: minimum of the normalised range
        :param float top: maximum of the normalised range
        :return: a normalised value
        :rtype: float
        """
        if value < min_value:
            value = min_value
        if value > max_value:
            value = max_value
        normalised = bottom + (value - min_value) * (top - bottom)/(max_value - min_value)
        return normalised

    @staticmethod
    def hsv2rgb(h, s, v):
        """
        A wrapper method for colorsys.hsv_to_rgb().

        The colorsys.hsv_to_rgb() uses and returns normalised values for each colour coordinate (i.e. values from
        0.0 to 1.0). unicornhathd.set_pixel() requires RGB colors as values from 0 to 255.

        This method converts normalised values to 8-bit integers.

        :param float h: hue
        :param float s: saturation
        :param float v: value
        :return: RGB colour components as (r, g, b)
        :rtype: (int, int, int)
        """
        return tuple(int(i * 255) for i in colorsys.hsv_to_rgb(h, s, v))

    def get_altitude_colour(self, altitude=None, highlight=False):
        """
        Get a colour corresponding to a given altitude.

        This calculates an RGB colour for any altitude between 0 and 45000ft in a similar fashion as per dump1090-fa
        web interface.

        If altitude is None, this method will simply return a dark gray colour.

        The highlight parameter, if set, will make the pixel appear brighter and can be used for visualising
        either two aircraft overlapping, or an aircraft directly overhead the receiver.

        :param float altitude: altitude in feet
        :param bool highlight: make the pixel brighter
        :return: colour values in RGB
        :rtype: (int, int, int)
        """

        if type(altitude) is not int:
            altitude = None

        if altitude is None or altitude < 0:            # handle special case of unknown altitude
            return 64, 64, 64

        hue = self.normalise(altitude, min_value=0, max_value=40000, bottom=0.0, top=0.85)
        if highlight:
            intensity = 1
        else:
            intensity = 0.66
        return self.hsv2rgb(hue, 1, intensity)

    def plot_positions(self, positions, radius):
        """
        Plot aircraft positions on the UnicornHAT HD.

        :param list[(float, float, float)] positions: list of aircraft positions, \
                where each element of the list is a tuple of lat, lon, altitude for a given aircraft
        :param int radius: radius in Nautical Miles
        """
        origin = self.get_receiver_origin()

        # clear the display buffer
        uh.clear()
        rcvr = self.pixel_origin()
        uh.set_pixel(rcvr[0], rcvr[1], 128, 128, 128)   # display the position of the receiver on the UnicornHAT

        for position in positions:
            pixel = self.pixel_pos(radius, origin, (position[0], position[1]))

            # make the pixel extra bright if it's directly overhead the receiver
            if pixel == rcvr:
                highlight = True
            else:
                highlight = False

            colour = self.get_altitude_colour(position[2], highlight=highlight)
            uh.set_pixel(pixel[0], pixel[1], colour[0], colour[1], colour[2])

        # redraw the screen
        uh.show()

    def run(self):
        """
        The RadarDaemon's run loop (the worker).
        """

        while True:
            all_aircraft = self.get_aircraft()
            ac_positions = list()
            for plane in all_aircraft:
                if "lat" in plane and "lon" in plane:
                    lat = plane["lat"]
                    lon = plane["lon"]
                    if "altitude" in plane:
                        alt = plane["altitude"]
                    else:
                        alt = None      # set to unknown value
                    ac_positions.append([lat, lon, alt])

            self.plot_positions(ac_positions, self.scope_radius)
            time.sleep(1)

    def start(self, scope_radius=None, username=None, adsb_hostname=None):
        """
        Override the Daemon.start() method to implement some extra customisation.

        This method starts the RadarDaemon's worker process.

        If scope_radius is provided, set the corresponding property to its value.
        If username is provided, the daemon will drop its privileges to work as unprivileged user.
        :param int scope_radius: radius in Nautical Miles
        :param str username: if provided, the daemon will drop privileges to work as this user
        :param str adsb_hostname: if provided, fetch the aircraft data from this hostname instead of localhost
        """
        if scope_radius:
            self.scope_radius = scope_radius

        if adsb_hostname:
            self.adsb_host = adsb_hostname
        super().start(username=username)

    def stop(self, silent=False):
        """
        Override the Daemon.stop() method to implement turning off the UnicornHAT HD when the daemon exits.
        :param bool silent: when set to true, this will log a message to indicate the daemon has been stopped.
        """
        uh.off()
        super().stop(silent)

    def sigterm_handler(self, signo, frame):
        """
        Override the Daemon.sigterm_handle() method to turn off the UnicornHAT HD when the daemon process is terminated.
        """
        uh.off()
        super().sigterm_handler(signo, frame)


def main():
    """
    The application main entry point
    """

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title='actions', dest='action', metavar='action')
    parser_start = subparsers.add_parser('start', help='start radarscoped')
    parser_start.add_argument('-u, --user', dest='username', help='user to run as', type=str, default=None)
    parser_start.add_argument('-r, --radius', dest='radius',
                              help='scope radius in Nautical Miles', type=int, default=72)
    parser_start.add_argument('-a, --adsb-receiver-hostname', dest='hostname',
                              help='hostname of the ADSB receiver running dump1090-fa',
                              type=str, default='localhost')

    parser_stop = subparsers.add_parser('stop', help='stop radarscoped')
    parser_restart = subparsers.add_parser('restart', help='restart radarscoped')
    parser_status = subparsers.add_parser('status', help='get status for radarscoped')

    subparsers.required = True

    args = parser.parse_args()
    action = args.action

    # instantiate the daemon
    radarscoped = RadarDaemon('/var/run/radarscoped.pid')

    if action == 'start':
        radarscoped.start(scope_radius=args.radius, username=args.username, adsb_hostname=args.hostname)
        pid = radarscoped.get_pid()

        if not pid:
            print("Error starting radarscoped")

    elif action == 'stop':
        radarscoped.stop()

    elif action == 'restart':
        radarscoped.restart()

    elif action == 'status':
        radarscoped.status()

    raise SystemExit(0)


if __name__ == '__main__':
    main()
