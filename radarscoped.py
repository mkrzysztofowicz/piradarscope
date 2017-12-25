"""
Radar Scope Daemon

This program displays relative positions of aircraft received with ADSB receiver on the Unicorn Hat HD.
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

    # noinspection PyMethodMayBeStatic
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

    # noinspection PyMethodMayBeStatic
    def sigterm_handler(self, signo, frame):
        self.logger.warning("Exiting.".format(signo))
        raise SystemExit(1)

    def run(self):
        """
        Override this method when subclassing Daemon
        """
        raise NotImplementedError


class RadarDaemon(Daemon):

    def __init__(self, pidfile, stdin='/dev/null', stdout='/dev/null', stderr='/dev/null'):
        super().__init__(pidfile, stdin, stdout, stderr, daemon_name="radarscoped")
        self.receiverurl = "http://piradar/dump1090-fa/data/receiver.json"
        self.aircrafturl = "http://piradar/dump1090-fa/data/aircraft.json"
        self.scope_radius = None

    @staticmethod
    def lon_length(latitude):
        return 60 * math.cos(math.radians(latitude))

    def coord_span(self, radius, origin=(0, 0)):
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
        request = urllib.request.urlopen(url)
        data = request.read()
        encoding = request.info().get_content_charset('utf-8')
        json_data = json.loads(data.decode(encoding))
        return json_data

    def get_origin(self):
        data = self.get_json(self.receiverurl)

        latitude = data["lat"]
        longitude = data["lon"]
        return latitude, longitude

    @staticmethod
    def pixel_origin():
        shape = uh.get_shape()
        x = shape[0] / 2
        y = shape[1] / 2
        return x, y

    @staticmethod
    def pixel_radius():
        shape = uh.get_shape()
        radius = math.floor(min(shape[0] / 2, shape[1] / 2))
        return radius

    def get_aircraft(self):
        data = self.get_json(self.aircrafturl)
        return data["aircraft"]

    def pixel_pos(self, radius, origin, position):
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
        if value < min_value:
            value = min_value
        if value > max_value:
            value = max_value
        normalised = bottom + (value - min_value) * (top - bottom)/(max_value - min_value)
        return normalised

    @staticmethod
    def hsv2rgb(h, s, v):
        return tuple(int(i * 255) for i in colorsys.hsv_to_rgb(h, s, v))

    def get_altitude_colour(self, altitude):
        hue = self.normalise(altitude, min_value=0, max_value=45000, bottom=0.1, top=0.8)
        return self.hsv2rgb(hue, 1, 1)

    def plot_positions(self, positions, radius):
        origin = self.get_origin()

        uh.off()

        for position in positions:
            pixel = self.pixel_pos(radius, origin, (position[0], position[1]))
            colour = self.get_altitude_colour(position[2])
            uh.set_pixel(pixel[0], pixel[1], colour[0], colour[1], colour[2])

        rcvr = self.pixel_origin()
        uh.set_pixel(rcvr[0], rcvr[1], 255, 255, 255)
        uh.show()

    def run(self):
        while True:
            all_aircraft = self.get_aircraft()
            ac_positions = list()
            for plane in all_aircraft:
                if "lat" in plane:
                    lat = plane["lat"]
                    lon = plane["lon"]
                    if "altitude" in plane:
                        alt = plane["altitude"]
                    else:
                        alt = 0
                    ac_positions.append([lat, lon, alt])

            self.plot_positions(ac_positions, 72)
            time.sleep(5)

    def start(self, scope_radius=None, username=None):
        if scope_radius:
            self.scope_radius = scope_radius
        super().start(username=username)


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

    parser_stop = subparsers.add_parser('stop', help='stop radarscoped')
    parser_restart = subparsers.add_parser('restart', help='restart radarscoped')
    parser_status = subparsers.add_parser('status', help='get status for radarscoped')
    subparsers.required = True

    args = parser.parse_args()
    action = args.action

    # instantiate the daemon
    radarscoped = RadarDaemon('/var/run/radarscoped.pid')

    if action == 'start':
        radarscoped.start(username=args.username)
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
