#!/usr/bin/env python3
from flask import Flask, request, jsonify
import requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SGP_BASE = 'https://sgp.net4you.com.br/api'
AUTH = ('robo', 'Ox(?YMae?0V3V#}HIGcF')

@app.route('/proxy/comodato/list/', methods=['GET'])
def proxy_comodato():
    params = {
        'data_cadastro_ini': request.args.get('data_cadastro_ini'),
        'data_cadastro_fim': request.args.get('data_cadastro_fim')
    }
    resp = requests.get(f'{SGP_BASE}/estoque/comodato/list/', auth=AUTH, params=params)
    return jsonify(resp.json())

@app.route('/proxy/comodatoitens/list/', methods=['GET'])
def proxy_comodatoitens():
    params = {'comodato_id': request.args.get('comodato_id')}
    resp = requests.get(f'{SGP_BASE}/estoque/comodatoitens/list/', auth=AUTH, params=params)
    return jsonify(resp.json())

@app.route('/proxy/contrato/', methods=['POST'])
def proxy_contrato():
    data = {
        'token': request.form.get('token'),
        'app': request.form.get('app'),
        'contrato': request.form.get('contrato')
    }
    resp = requests.post(f'{SGP_BASE}/ura/listacontrato/', data=data)
    return jsonify(resp.json())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
