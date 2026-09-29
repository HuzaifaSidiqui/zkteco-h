from http.server import BaseHTTPHandler

class handler(BaseHTTPHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write('pong'.encode('utf-8'))
        return
