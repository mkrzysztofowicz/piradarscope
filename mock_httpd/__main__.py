"""
Mock HTTP server serving aircraft.json and receiver.json files used for testing.
By default the server runs on port 10080 (to change, modify tcp_port variable
at the bottom of the script.

To run this daemon, in the main project directory issue the following command:

$ python3 -m mock_httpd
"""

from http.server import SimpleHTTPRequestHandler, HTTPServer
import os
import sys

class MockHttpdRequestHandler(SimpleHTTPRequestHandler):
    """
    HTTP Request Handler class.

    The only files this server currently returns to the http client are
    receiver.json and aircraft.json. These static files will be served
    from the current directory, as long as the URL passed to the server in the
    GET request ends with 'receiver.json' or 'aircraft.json'.

    Note, this is only meant to be used for code testing during development.
    """

    base = os.path.dirname(__file__)

    # handle GET requests
    def do_GET(self):
        try:
            if self.path.endswith('receiver.json'):
                self.handle_json('receiver.json')
            elif self.path.endswith('aircraft.json'):
                self.handle_json('aircraft.json')
            else:
                SimpleHTTPRequestHandler.do_GET(self)
        except Exception as e:
            print('Failed in do_GET(): {} {}'.format(type(e).__name__, e), file=sys.stderr)

    def handle_json(self, jsonfile):
        """
        Return the json file to the client
        :param jsonfile: name of the json file to be served from the local directory
        """

        jsonfile = os.path.join(self.base, jsonfile)

        if os.path.exists(jsonfile):
            with open(jsonfile, 'rt') as f:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(bytes(f.read(), 'utf-8'))

        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(bytes('File {} not found\n'.format(jsonfile), 'utf-8'))


def run(port):
    print('starting the server')
    server_address = ('localhost', port)
    httpd = HTTPServer(server_address, MockHttpdRequestHandler)
    print('running the server')
    httpd.serve_forever()


if __name__ == '__main__':
    tcp_port = 10080
    run(tcp_port)
