#!/usr/bin/env python3

"""
Radar Scope Daemon

This program displays relative positions of aircraft received with ADSB receiver on the UnicornHat HD.
The receiver location is in the middle of the screen.

"""

import argparse
import atexit
import colorsys
import configparser
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

try:
    import unicornhathd as uh
except ImportError:
    import mock_unicornhathd as uh
    print('Warning. UnicornHAT HD module not found. Using a mock module instead', file=sys.stderr)


class Daemon(object):
    """
    A generic daemon class

    Subclass the Daemon class and override the run() method
    """

    def __init__(self, pidfile, config_file=None,
                 stdin='/dev/null', stdout='/dev/null', stderr='/dev/null', daemon_name="Daemon"):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.pidfile = pidfile
        self.username = None
        self.name = daemon_name
        self.logger = logging.getLogger(self.name)
        self.setup_logging()
        self.config_file = config_file
        self.configuration = None
        self.dont_daemonize = False

    def configure(self):
        """
        Parse the configuration file and configure the daemon.
        This method has to be overridden when subclassing the Daemon.
        """
        raise NotImplementedError

    def setup_logging(self):
        """
        Set up the logging sytem.

        This will set the format for all log messages, configure the logging system to send messages to syslog via
        a special file /dev/log. In addition, the logging system will be configured to log all uncaught exceptions
        to assist in troubleshooting.
        """
        logformatter = logging.Formatter('%(name)s[%(process)s]: [%(levelname)s] %(funcName)s: %(message)s')

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(logformatter)
        self.logger.addHandler(console_handler)

        if os.path.exists('/dev/log'):
            syslog_handler = logging.handlers.SysLogHandler('/dev/log')
            syslog_handler.setFormatter(logformatter)
            self.logger.addHandler(syslog_handler)

        # catch all unhandled exceptions
        sys.excepthook = self.exception_log_handler

    def exception_log_handler(self, atype, value, tb):
        """
        The uncaught exceptions log handler method. This will log any uncaught exception.
        """
        self.logger.exception('Uncaught exception: {}'.format(str(value)))

    def attach_stream(self, name, mode):
        """
        Replaces the stream with a new one
        """
        stream = open(getattr(self, name), mode)
        os.dup2(stream.fileno(), getattr(sys, name).fileno())

    def dettach_process(self):
        """
        Detach the process from the environment.
        """

        self.fork()     # first fork, detach from parent

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
        """
        Create a pid file and save the current pid.
        """
        atexit.register(self.delete_pidfile)
        pid = str(os.getpid())
        open(self.pidfile, 'w+').write("{}\n".format(pid))

    def delete_pidfile(self):
        """
        Remove the pid file
        """
        os.remove(self.pidfile)

    @staticmethod
    def pid_exists(pid):
        """
        Check if a process with a given process ID is already running.

        This method uses a fact that kill signal 0 doesn't actually do anything to a running process. If a process
        with a given PID does exist and the user doesn't have the permissions to send it a signal, the permissions
        denied exception will be raised (meaning the process with a given ID *does* exist), or nothing will happen
        at all. If the process doesn't exist, ProcessLookupError exception will be raised instead.

        :param int pid: process ID to check
        :return: False if no process with a given PID is running, True otherwise
        """
        if pid < 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        else:
            return True

    def get_pid(self):
        """
        Return the Process ID of the running process.
        :return: pid of the currently running process
        :rtype: int
        """
        try:
            pf = open(self.pidfile, 'r')
            pid = int(pf.read().strip())
            pf.close()
        except (IOError, TypeError):
            pid = None
        return pid

    def status(self):
        """
        This method runs when the 'status' action was specified as run time argument to the daemon.
        It will return a dict with two fields: a message to say if the daemon is running and a pid (or None).
        :return: a dict with a message and a pid. If not running, pid will be None.
        """
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
        """
        Make a daemon out of the process by dettaching from the environment and forking. If username is specified,
        this method will also cause the daemon to drop privileges to those of the specified user.
        Also register sigterm handler.
        """
        if self.dont_daemonize:
            return

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

    def start(self):
        """
        Start the daemon
        """

        self.logger.info("Starting.")

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


        if self.config_file is not None:
            self.configuration = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation)
            self.configure()

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
        """
        Sigterm handler method. By default this will simply log a message to say the daemon is terminating and
        then exit.

        If any extra functionality needed, this method should be overridden in the child class.
        """
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

    def __init__(self, pidfile, config_file=None, stdin='/dev/null', stdout='/dev/null', stderr='/dev/null'):
        """
        Override the init() method of the Daemon class to add extra properties.
        """
        self.adsb_host = 'localhost'
        self.receiverurl = "http://{}/dump1090-fa/data/receiver.json".format(self.adsb_host)
        self.aircrafturl = "http://{}/dump1090-fa/data/aircraft.json".format(self.adsb_host)
        self.scope_radius = 60
        self.scope_brightness = 0.5
        self.airport_brightness = 0.2
        self.scope_rotation = 0
        self.airports = list()
        self.aircraft_in_range = 0

        super().__init__(pidfile, config_file, stdin, stdout, stderr, daemon_name="radarscoped")

    def configure(self):
        """
        Override the Daemon.configure() method to configure RadarDaemon.
        """

        self.configuration = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())

        if not self.config_file:
            self.logger.info('No configuration file specified. Running with defaults')
            return

        if not os.path.exists(self.config_file):
            print("Configuration file {} does not exist. Exiting".format(self.config_file))
            raise SystemExit(1)

        self.configuration.read(self.config_file)

        if self.configuration.has_section('main'):
            if not self.username:
                if 'username' in self.configuration['main']:
                    self.username = self.configuration.get('main', 'username')

            loglevel = self.configuration.get('main', 'loglevel', fallback='INFO')
            loglevel = getattr(logging, loglevel.upper())
            self.logger.setLevel(loglevel)

        if self.configuration.has_section('scope'):
            self.scope_radius = self.configuration.getint('scope', 'radius', fallback=60)
            self.scope_brightness = self.configuration.getfloat('scope', 'scope_brightness', fallback=0.5)
            self.airport_brightness = self.configuration.getfloat('scope', 'airport_brightness', fallback=0.5)
            self.scope_rotation = self.configuration.getint('scope', 'rotation', fallback=0)

        if self.configuration.has_section('ADSB'):
            self.adsb_host = self.configuration.get('ADSB', 'adsb_host', fallback='localhost')
            self.receiverurl = self.configuration.get('ADSB', 'receiver_url',
                                                      fallback='http://{}/dump1090-fa/data/receiver.json'.format(
                                                          self.adsb_host
                                                      ))
            self.aircrafturl = self.configuration.get('ADSB', 'aircraft_url',
                                                      fallback='http://{}/dump1090-fa/data/aircraft.json'.format(
                                                          self.adsb_host
                                                      ))

        if self.configuration.has_section('airports'):
            for airport in self.configuration.items(section='airports'):
                icao_code = airport[0]
                coordinates = airport[1].strip().split(',')
                self.add_airport(icao_code, float(coordinates[0]), float(coordinates[1]))

    def add_airport(self, icao_code, latitude, longitude):
        """
        Add an airport to the static list of airports for plotting
        :param str icao_code: ICAO code of the airport (e.g. EIDW for Dublin)
        :param float latitude: latitude of the ARP
        :param float longitude: longitude of the ARP
        """

        self.airports.append({
            "icao_code": icao_code,
            "lat": latitude,
            "lon": longitude
        })

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

        data = self.get_json(self.aircrafturl)
        return data["aircraft"]

    def get_receiver_origin(self):
        """
        Get the GPS coordinates of the ADSB receiver

        :return: lat/lon of the ADSB receiver
        :rtype: (float, float)
        """

        data = self.get_json(self.receiverurl)
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
        radius = math.floor(max(shape[0] / 2, shape[1] / 2))
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
            saturation = 0.50
        else:
            intensity = 0.66
            saturation = 1
        return self.hsv2rgb(hue, saturation, intensity)

    def plot_airports(self, airports, origin, radius):
        """
        Plot all airports on the UnicornHAT HD

        :param airports: a list of airports; each element in the list is a dict() containing the following fields: \
            icao_code, lat, lon
        :type airports: list[dict[str, float, float]]
        :param (float, float) origin: GPS coordinates of the receiver as lat, lon
        :param int radius: scope radius in Nautical Miles
        """

        brightness_scaling_factor = 64
        for airport in airports:
            pixel = self.pixel_pos(radius, origin, (airport["lat"], airport["lon"]))

            # don't plot an airport if it's at or beyond the scope margin
            if pixel[0] == 0 or pixel[0] == 15 or pixel[1] == 0 or pixel[1] == 15:
                continue

            # this calculates the shade of gray (brightness) of the airport depending on the
            # configuration setting
            colour = colorsys.hsv_to_rgb(0, 0, self.airport_brightness * brightness_scaling_factor)
            uh.set_pixel(pixel[0], pixel[1], colour[0], colour[1], colour[2])

    def plot_receiver(self):
        """
        Plot the position of the ADSB receiver on the Radar Scope
        """
        rcvr = self.pixel_origin()
        uh.set_pixel(rcvr[0], rcvr[1], 255, 255, 255)  # display the position of the receiver on the UnicornHAT

    def plot_aircraft(self, positions, origin, radius):
        """
        Plot the positions of all aircraft in range of the ADSB receiver on the Radar Scope

        :param list[(float, float, float)] positions:  list of aircraft positions, \
                where each element of the list is a tuple of lat, lon, altitude for a given aircraft
        :param [float, float] origin: the latitude and longitude of the ADSB receiver
        :param int radius: the radius of the Radar Scope in Nautical Miles
        :return:
        """

        rcvr = self.get_receiver_origin()
        for position in positions:
            pixel = self.pixel_pos(radius, origin, (position[0], position[1]))

            # make the pixel extra bright if it's directly overhead the receiver
            if pixel == rcvr:
                highlight = True
            else:
                highlight = False

            colour = self.get_altitude_colour(position[2], highlight=highlight)
            uh.set_pixel(pixel[0], pixel[1], colour[0], colour[1], colour[2])

    def plot(self, positions, radius=60):
        """
        Plot aircraft positions on the UnicornHAT HD.

        :param list[(float, float, float)] positions: list of aircraft positions, \
                where each element of the list is a tuple of lat, lon, altitude for a given aircraft
        :param int radius: radius in Nautical Miles
        """
        origin = self.get_receiver_origin()

        # clear the display buffer
        uh.clear()

        self.plot_receiver()
        self.plot_airports(self.airports, origin, radius)
        self.plot_aircraft(positions, origin, radius)

        # redraw the screen
        uh.show()

    def run(self):
        """
        The RadarDaemon's run loop (the worker).
        """

        # preconfigure the display
        uh.brightness(self.scope_brightness)
        uh.rotation(self.scope_rotation)

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

            if len(ac_positions) != self.aircraft_in_range:
                self.aircraft_in_range = len(ac_positions)
                self.logger.info('{} aircraft in range'.format(self.aircraft_in_range))

            self.plot(ac_positions, self.scope_radius)
            time.sleep(1)

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
    parser_start.add_argument('-c, --config-file', dest='config_file', help='path to the config file',
                              type=str, default=None)
    parser_start.add_argument('-f, --foreground', dest='foreground', help='run in foreground',
                              action='store_true', default=False)

    parser_stop = subparsers.add_parser('stop', help='stop radarscoped')
    parser_restart = subparsers.add_parser('restart', help='restart radarscoped')
    parser_status = subparsers.add_parser('status', help='get status for radarscoped')

    subparsers.required = True

    args = parser.parse_args()
    action = args.action

    if not hasattr(args, 'config_file'):
        args.config_file = None

    # instantiate the daemon
    radarscoped = RadarDaemon('/var/run/radarscoped.pid', config_file=args.config_file)

    if hasattr(args, 'foreground'):
        radarscoped.dont_daemonize = args.foreground

    radarscoped.configure()

    if action == 'start':
        radarscoped.start()
        pid = radarscoped.get_pid()

        if not pid:
            print("Error starting radarscoped")

    elif action == 'stop':
        radarscoped.stop()

    # elif action == 'restart':
    #     radarscoped.restart()

    elif action == 'status':
        status = radarscoped.status()
        print(status["message"])

    raise SystemExit(0)


if __name__ == '__main__':
    main()
