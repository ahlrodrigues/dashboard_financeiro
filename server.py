#!/usr/bin/env python3
import http.server
import socketserver
import json
import requests
from urllib.parse import parse_qs, urlparse
import os

SGP_BASE = 'https://sgp.net4you.com.br/api'
AUTH = ('robo', 'Ox(?YMae?0V3V#}HIGcF')

PORT = 9001

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/comodato/list'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            ini = params.get('data_cadastro_ini', [''])[0]
            fim = params.get('data_cadastro_fim', [''])[0]
            resp = requests.get(f'{SGP_BASE}/estoque/comodato/list/', auth=AUTH, params={'data_cadastro_ini': ini, 'data_cadastro_fim': fim}, timeout=60)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(resp.content)
            return
        elif self.path.startswith('/comodatoitens/list'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            cid = params.get('comodato_id', [''])[0]
            resp = requests.get(f'{SGP_BASE}/estoque/comodatoitens/list/', auth=AUTH, params={'comodato_id': cid}, timeout=30)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp.content)
        elif self.path == '/' or self.path == '/index.html' or self.path == '/dashboard_financeiro.html' or self.path == '/test.html':
            self.path = '/test.html'
            super().do_GET()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')

    def do_POST(self):
        if self.path == '/contrato/' or self.path == '/contrato':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            params = parse_qs(post_data)
            data = {
                'token': params.get('token', [''])[0],
                'app': params.get('app', [''])[0],
                'contrato': params.get('contrato', [''])[0]
            }
            resp = requests.post(f'{SGP_BASE}/ura/listacontrato/', data=data, timeout=30)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp.content)
        else:
            self.send_response(404)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'Not Found')

os.chdir(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd())
with socketserver.TCPServer(("", PORT), ProxyHandler) as httpd:
    print(f"Server running at http://localhost:{PORT}/")
    httpd.serve_forever()
