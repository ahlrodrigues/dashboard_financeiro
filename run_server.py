#!/usr/bin/env python3
import http.server
import socketserver
import os

PORT = 8000
os.chdir('/home/ahlr/Projetos/dashboard_financeiro')

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.getcwd(), **kwargs)
    
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

Handler = DashboardHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Serving at http://127.0.0.1:{PORT}/")
    httpd.serve_forever()
