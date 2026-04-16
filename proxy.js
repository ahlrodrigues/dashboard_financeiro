const http = require('http');
const https = require('https');

const SGP_BASE = 'https://sgp.net4you.com.br';
const AUTH = 'Basic ' + Buffer.from('robo:Ox(?YMae?0V3V#}HIGcF').toString('base64');

const server = http.createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    
    if (req.method === 'OPTIONS') {
        res.writeHead(200);
        res.end();
        return;
    }
    
    const url = new URL(req.url, 'http://localhost:9001');
    
    if (url.pathname === '/comodato/list') {
        const ini = url.searchParams.get('data_cadastro_ini');
        const fim = url.searchParams.get('data_cadastro_fim');
        
        https.get(`${SGP_BASE}/api/estoque/comodato/list/?data_cadastro_ini=${ini}&data_cadastro_fim=${fim}`, {
            headers: { 'Authorization': AUTH }
        }, (sgpRes) => {
            let data = '';
            sgpRes.on('data', chunk => data += chunk);
            sgpRes.on('end', () => {
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(data);
            });
        }).on('error', (e) => {
            res.writeHead(500);
            res.end(JSON.stringify({ error: e.message }));
        });
    }
    else if (url.pathname === '/comodatoitens/list') {
        const cid = url.searchParams.get('comodato_id');
        
        https.get(`${SGP_BASE}/api/estoque/comodatoitens/list/?comodato_id=${cid}`, {
            headers: { 'Authorization': AUTH }
        }, (sgpRes) => {
            let data = '';
            sgpRes.on('data', chunk => data += chunk);
            sgpRes.on('end', () => {
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(data);
            });
        }).on('error', (e) => {
            res.writeHead(500);
            res.end(JSON.stringify({ error: e.message }));
        });
    }
    else if (url.pathname === '/contrato') {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', () => {
            const params = new URLSearchParams(body);
            const postData = `token=${params.get('token')}&app=${params.get('app')}&contrato=${params.get('contrato')}`;
            
            const options = {
                hostname: 'sgp.net4you.com.br',
                path: '/api/ura/listacontrato/',
                method: 'POST',
                headers: {
                    'Authorization': AUTH,
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': Buffer.byteLength(postData)
                }
            };
            
            const sgpReq = https.request(options, (sgpRes) => {
                let data = '';
                sgpRes.on('data', chunk => data += chunk);
                sgpRes.on('end', () => {
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(data);
                });
            });
            
            sgpReq.write(postData);
            sgpReq.end();
        });
    }
    else {
        const fs = require('fs');
        let filePath = url.pathname === '/' ? '/test.html' : url.pathname;
        filePath = __dirname + filePath;
        
        if (fs.existsSync(filePath)) {
            const ext = filePath.split('.').pop();
            const types = { 'html': 'text/html', 'js': 'application/javascript', 'css': 'text/css' };
            res.writeHead(200, { 'Content-Type': types[ext] || 'text/plain' });
            res.end(fs.readFileSync(filePath));
        } else {
            res.writeHead(404);
            res.end('Not Found');
        }
    }
});

server.listen(8888, () => {
    console.log('Server running at http://localhost:8888/');
});
