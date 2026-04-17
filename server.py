#!/usr/bin/env python3
import http.server
import socketserver
import json
import requests
from urllib.parse import parse_qs, urlparse
import os
from datetime import datetime, date, timedelta
import time
import socket
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import unicodedata
from socketserver import ThreadingMixIn
import re
import base64
import hashlib
import hmac
import secrets

SGP_BASE = 'https://sgp.net4you.com.br/api'
AUTH = ('robo', 'Ox(?YMae?0V3V#}HIGcF')
PORT = 8000

APP_TOKEN = '37ab7243-9e9c-45bc-a393-e2ccbf76eff2'
APP_NAME = 'financeiro'
AUTH_ENABLED = True
AUTH_JWT_SECRET = "dashboard-secret-change-in-production"
AUTH_ADMIN_GROUP = "financeiro"
AUTH_TOKEN_TTL_SECONDS = 12 * 60 * 60
REQUERER_CONTEUDO = "Financeiro - Negociação"
REQUERER_OCORRENCIA_TIPO = 140
REQUERER_MOTIVO_OS = 4018
REQUERER_SETOR = 1
REQUERER_PRIORIDADE = 2
# Campo/valor opcionais para "classificação" (algumas instâncias do SGP exigem/aceitam chaves diferentes).
REQUERER_CLASSIFICACAO_FIELD = "classificacao"
REQUERER_CLASSIFICACAO_VALUE = "suspenso"
SUSPENDED_STATUS_CODES = set()
SUSPENDED_STATUS_TOKENS = ["SUSP"]
try:
    base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
    cfg_path = os.path.join(base_dir, 'config.json')
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        # base do SGP (aceita url_base do config como raiz do site ou já contendo /api)
        url_base = (cfg.get('url_base') or '').strip()
        if url_base:
            normalized = url_base.rstrip('/')
            if normalized.endswith('/api'):
                SGP_BASE = normalized
            else:
                SGP_BASE = f'{normalized}/api'
        # credenciais Basic (para endpoints protegidos)
        basic = cfg.get('basic_auth') or {}
        user = (basic.get('username') or '').strip()
        pwd = (basic.get('password') or '')
        if user and pwd:
            AUTH = (user, pwd)
        APP_TOKEN = cfg.get('app_token_auth', {}).get('token', APP_TOKEN)
        APP_NAME = cfg.get('app_token_auth', {}).get('app', APP_NAME)
        auth_cfg = cfg.get('auth') or {}
        try:
            AUTH_ENABLED = bool(auth_cfg.get('enabled', AUTH_ENABLED))
        except Exception:
            pass
        AUTH_JWT_SECRET = str(auth_cfg.get('jwt_secret') or AUTH_JWT_SECRET)
        AUTH_ADMIN_GROUP = str(auth_cfg.get('admin_group') or AUTH_ADMIN_GROUP)
        try:
            ttl = int(auth_cfg.get('token_ttl_seconds') or AUTH_TOKEN_TTL_SECONDS)
            if ttl > 0:
                AUTH_TOKEN_TTL_SECONDS = ttl
        except Exception:
            pass
        req_cfg = cfg.get('requerer') or cfg.get('ocorrencia') or {}
        try:
            REQUERER_CONTEUDO = str(req_cfg.get('conteudo') or REQUERER_CONTEUDO)
        except Exception:
            pass
        try:
            v = req_cfg.get('ocorrenciatipo')
            if v is not None:
                REQUERER_OCORRENCIA_TIPO = int(v)
        except Exception:
            pass
        try:
            v = req_cfg.get('motivoos')
            if v is not None:
                REQUERER_MOTIVO_OS = int(v)
        except Exception:
            pass
        try:
            v = req_cfg.get('setor')
            if v is not None:
                REQUERER_SETOR = int(v)
        except Exception:
            pass
        try:
            v = req_cfg.get('os_prioridade')
            if v is not None:
                REQUERER_PRIORIDADE = int(v)
        except Exception:
            pass
        try:
            REQUERER_CLASSIFICACAO_FIELD = str(req_cfg.get('classificacao_field') or REQUERER_CLASSIFICACAO_FIELD).strip()
        except Exception:
            pass
        try:
            REQUERER_CLASSIFICACAO_VALUE = str(req_cfg.get('classificacao_value') or REQUERER_CLASSIFICACAO_VALUE).strip()
        except Exception:
            pass
        codes = cfg.get('dashboard', {}).get('suspended_status_codes') or []
        for c in codes:
            try:
                SUSPENDED_STATUS_CODES.add(int(c))
            except Exception:
                pass
        tokens = cfg.get('dashboard', {}).get('suspended_status_tokens') or []
        if isinstance(tokens, list) and tokens:
            SUSPENDED_STATUS_TOKENS = [str(t).strip() for t in tokens if str(t).strip()]
except Exception:
    pass

try:
    env_port = (os.environ.get("PORT") or "").strip()
    if env_port:
        PORT = int(env_port)
except Exception:
    PORT = 8000

def _read_local_version():
    base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
    fp = os.path.join(base_dir, 'version.py')
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            txt = f.read()
        m = re.search(r'^\s*VERSION\s*=\s*"([^"]+)"\s*$', txt, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

def _b64url_decode(text: str) -> bytes:
    s = str(text or "").strip()
    if not s:
        return b""
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("utf-8"))

def _create_simple_jwt(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    sig = hmac.new(str(secret).encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"

def _verify_simple_jwt(token: str, secret: str):
    try:
        parts = str(token or "").split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(str(secret).encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_sig, _b64url_decode(sig_b64)):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8") or "{}")
        exp = payload.get("exp")
        if exp is not None:
            try:
                if int(exp) < int(time.time()):
                    return None
            except Exception:
                return None
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    contrato_cache = {}
    contrato_status_cache = {}
    results_cache = {}
    RESULTS_TTL_SECONDS = 300
    titulos_cache = {}
    TITULOS_TTL_SECONDS = 300
    def _post_sgp(self, path, payload, timeout=60):
        url = f'{SGP_BASE}{path}'
        # Tenta JSON primeiro (padrão moderno), depois form-encoded (alguns endpoints do SGP aceitam melhor)
        try:
            return requests.post(url, auth=AUTH, json=payload, timeout=timeout), "json"
        except Exception:
            return requests.post(url, auth=AUTH, data=payload, timeout=timeout), "form"

    def _post_ura(self, path, data, timeout=60):
        url = f'{SGP_BASE}{path}'
        base = {"token": APP_TOKEN, "app": APP_NAME}
        base.update(data or {})

        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "dashboard-financeiro/1.0",
        }

        def as_files(payload):
            return {k: (None, "" if v is None else str(v)) for k, v in payload.items()}

        # URA (conforme Postman) costuma usar form-data; tentamos multipart primeiro.
        try:
            resp = requests.post(url, files=as_files(base), headers=headers, timeout=timeout, allow_redirects=False)
            return resp, "multipart"
        except Exception:
            # fallback: x-www-form-urlencoded
            resp = requests.post(url, data=base, headers=headers, timeout=timeout, allow_redirects=False)
            return resp, "urlencoded"

    def _post_suporte_contrato(self, contrato_id, timeout=40):
        url = f'{SGP_BASE}/suporte/contrato/list/'
        payload = {"contrato_id": int(contrato_id)}
        resp = requests.post(url, auth=AUTH, json=payload, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"Suporte contrato HTTP {resp.status_code}: {(resp.text or '')[:200]}")
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"Suporte contrato JSON inválido: {type(e).__name__}: {str(e)}")
        if not isinstance(data, list):
            raise RuntimeError(f"Suporte contrato resposta inesperada: {type(data).__name__}")
        return data

    def _ura_listacontrato_by_id(self, contrato_id, timeout=40):
        resp, mode = self._post_ura('/ura/listacontrato/', {"contrato": str(contrato_id)}, timeout=timeout)
        body_head = (resp.text or "")[:300]
        content_type = resp.headers.get("Content-Type", "")
        if resp.status_code in (301, 302, 303, 307, 308):
            raise RuntimeError(f"URA listacontrato redirect {resp.status_code} ({mode}) -> {resp.headers.get('Location','')}")
        if resp.status_code != 200:
            raise RuntimeError(f"URA listacontrato HTTP {resp.status_code} ({mode}): {body_head}")
        if "text/html" in content_type.lower():
            raise RuntimeError(f"URA listacontrato HTML ({mode}): {body_head}")
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"URA listacontrato JSON inválido ({mode}): {type(e).__name__}: {str(e)}")
        if not isinstance(data, list):
            raise RuntimeError(f"URA listacontrato resposta inesperada: {type(data).__name__}")
        return data

    def _to_int(self, value):
        try:
            if value is None or value == '':
                return None
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return int(value)
            s = str(value).strip()
            if not s:
                return None
            if s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                return int(s)
            return None
        except Exception:
            return None

    def _is_suspended_status(self, status_text, status_code):
        # 1) se vier texto, usa tokens (case-insensitive; normaliza acentos)
        if status_text:
            normalized = unicodedata.normalize("NFKD", str(status_text)).encode("ascii", "ignore").decode("ascii")
            normalized = normalized.upper()
            for token in SUSPENDED_STATUS_TOKENS:
                if token and token.upper() in normalized:
                    return True
        # 2) se vier código numérico, usa lista configurável via config.json
        if status_code is not None and status_code in SUSPENDED_STATUS_CODES:
            return True
        return False

    def _get_contrato_info_fast(self, contrato_id):
        # cache simples (sem expiração; dados não mudam a cada segundo)
        key = str(contrato_id)
        if key in ProxyHandler.contrato_cache:
            return ProxyHandler.contrato_cache[key]

        # Preferir URA listacontrato (retorna nome/cpf/status de forma consistente quando consulta por contrato)
        try:
            data = self._ura_listacontrato_by_id(contrato_id)
            item = data[0] if data else {}
            payload = {
                "cliente_id": str(item.get("id_cliente") or ""),
                "nome": str(item.get("nome") or "-"),
                "cpfcnpj": str(item.get("cpfcnpj") or ""),
                "status_text": str(item.get("status") or "-"),
                "status_code": None,
                "source": "ura",
            }
            ProxyHandler.contrato_cache[key] = payload
            return payload
        except Exception:
            # Fallback: suporte/contrato/list também traz status (e às vezes códigos)
            data = self._post_suporte_contrato(contrato_id)
            item = data[0] if data else {}
            status_text = self._pick_first(item, ['contrato_status', 'status_contrato', 'status', 'situacao', 'contrato_situacao'])
            status_code = self._pick_first(item, [
                'contrato_status_id',
                'status_contrato_id',
                'status_id',
                'situacao_id',
                'contrato_situacao_id',
            ])
            payload = {
                "cliente_id": str(self._pick_first(item, ["cliente_id", "id_cliente", "cliente_pk"]) or ""),
                "nome": str(self._pick_first(item, ["cliente_nome", "nome", "razao_social"]) or "-"),
                "cpfcnpj": str(self._pick_first(item, ["cliente_cpfcnpj", "cpfcnpj", "cpf_cnpj"]) or ""),
                "status_text": str(status_text or "-"),
                "status_code": self._to_int(status_code),
                "source": "suporte",
            }
            ProxyHandler.contrato_cache[key] = payload
            return payload

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _buildinfo(self):
        return {
            "ok": True,
            "version": _read_local_version(),
            "port": PORT,
            "sgp_base": SGP_BASE,
            "auth_user": AUTH[0] if isinstance(AUTH, tuple) and len(AUTH) else None,
        }

    def _extract_number(self, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        if not s:
            return None
        s = s.replace('.', '').replace(',', '.')
        out = []
        for ch in s:
            if ch.isdigit() or ch in '.-':
                out.append(ch)
        try:
            return float(''.join(out))
        except Exception:
            return None

    def _parse_date(self, value):
        if not value:
            return None
        s = str(value).strip()
        if not s:
            return None
        # Normaliza ISO (remove timezone/millis e força 'T')
        s_iso = s.replace(' ', 'T')
        m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}:\d{2}))?", s_iso)
        if m:
            try:
                if m.group(2):
                    return datetime.strptime(f"{m.group(1)}T{m.group(2)}", "%Y-%m-%dT%H:%M:%S").date()
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except Exception:
                pass

        # dd/mm/yyyy (com ou sem hora)
        m = re.match(r"^(\d{2}/\d{2}/\d{4})(?:[ T](\d{2}:\d{2}:\d{2}))?", s)
        if m:
            try:
                if m.group(2):
                    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M:%S").date()
                return datetime.strptime(m.group(1), "%d/%m/%Y").date()
            except Exception:
                pass

        # fallback: tenta formatos simples conhecidos
        s_iso = s_iso.replace('Z', '')
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s_iso[:len(fmt)], fmt).date()
            except Exception:
                continue
        return None

    def _extract_contrato_id(self, value):
        if value is None or value == '':
            return None
        if isinstance(value, (int, float)) and int(value) > 0:
            return str(int(value))
        if isinstance(value, dict):
            inner = self._pick_first(value, ["id", "pk", "contrato_id", "cliente_contrato_id", "clienteContratoId", "codigo", "numero"])
            return self._extract_contrato_id(inner)
        s = str(value).strip()
        if not s:
            return None
        # pega o primeiro número longo (contratos geralmente são numéricos)
        m = re.search(r"\d{3,}", s)
        if m:
            return m.group(0)
        return None

    def _extract_contrato_id_from_titulo(self, titulo):
        keys = [
            "clienteContrato",
            "cliente_contrato",
            "clienteContratoId",
            "cliente_contrato_id",
            "contrato",
            "contrato_id",
            "contratoId",
            "contrato_id_fk",
            "clienteContratoPk",
        ]
        for k in keys:
            if isinstance(titulo, dict) and k in titulo and titulo.get(k) not in (None, ''):
                cid = self._extract_contrato_id(titulo.get(k))
                if cid:
                    return cid
        return None

    def _days_overdue(self, item):
        direct_keys = [
            'dias_atraso',
            'dias_em_atraso',
            'dias_vencido',
            'dias_inadimplencia',
            'dias_debito',
            'dias_em_aberto',
            'dias_atraso_boleto',
        ]
        for k in direct_keys:
            if k in item:
                n = self._extract_number(item.get(k))
                if n is not None:
                    return int(n)

        date_keys = [
            'ultimo_vencimento',
            'vencimento',
            'data_vencimento',
            'titulo_vencimento',
            'boleto_vencimento',
            'vencimento_boleto',
        ]
        for k in date_keys:
            if k in item and item.get(k):
                d = self._parse_date(item.get(k))
                if d:
                    return (date.today() - d).days
        return None

    def _pick_first(self, item, keys):
        for k in keys:
            if k in item and item.get(k) not in (None, ''):
                return item.get(k)
        return None

    def _get_vencimento_str(self, item):
        v = self._pick_first(item, [
            'ultimo_vencimento',
            'vencimento',
            'data_vencimento',
            'titulo_vencimento',
            'boleto_vencimento',
            'vencimento_boleto',
        ])
        if not v:
            return '-'
        d = self._parse_date(v)
        return d.isoformat() if d else str(v)[:10]

    def _get_valor_aberto(self, item):
        v = self._pick_first(item, [
            'valor_em_aberto',
            'valor_aberto',
            'valor_vencido',
            'total_em_aberto',
            'total_aberto',
            'total_vencido',
        ])
        return self._extract_number(v)

    def _ura_titulos_page(self, data_ini, data_fim, offset, limit):
        resp, mode = self._post_ura('/ura/titulos/', {
            "data_vencimento_inicio": data_ini,
            "data_vencimento_fim": data_fim,
            "offset": str(offset),
            "limit": str(limit),
        }, timeout=60)
        body_head = (resp.text or "")[:300]
        content_type = resp.headers.get("Content-Type", "")
        if resp.status_code in (301, 302, 303, 307, 308):
            raise RuntimeError(f"URA titulos redirect {resp.status_code} ({mode}) -> {resp.headers.get('Location','')}")
        if resp.status_code != 200:
            raise RuntimeError(f"URA titulos HTTP {resp.status_code} ({mode}): {body_head}")
        if "text/html" in content_type.lower():
            raise RuntimeError(f"URA titulos HTML ({mode}): {body_head}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"URA titulos resposta inesperada: {type(data).__name__}")
        return data

    def _fetch_overdue_contracts(self, dias_min, lookback_days, max_pages=120, started_ts=None, max_seconds=None):
        # Estratégia: buscar títulos vencidos no intervalo [hoje-(dias_min+lookback_days), hoje-dias_min]
        end = date.today() - timedelta(days=dias_min)
        start = end - timedelta(days=lookback_days)
        data_ini = start.isoformat()
        data_fim = end.isoformat()

        limit = 250
        offset = 0
        pages = 0

        per_contrato = {}
        statuses = {}
        counters = {
            "titulos_total": 0,
            "titulos_status_aberto": 0,
            "titulos_skip_status": 0,
            "titulos_missing_contrato": 0,
            "titulos_missing_vencimento": 0,
            "titulos_filtered_days": 0,
        }

        truncated_time = False
        while pages < max_pages:
            if started_ts is not None and max_seconds is not None:
                if (time.time() - started_ts) > max_seconds:
                    truncated_time = True
                    break
            pages += 1
            payload = self._ura_titulos_page(data_ini, data_fim, offset, limit)
            pag = payload.get("paginacao") or {}
            titulos = payload.get("titulos") or []
            if not isinstance(titulos, list):
                break

            for t in titulos:
                counters["titulos_total"] += 1
                contrato_id = self._extract_contrato_id_from_titulo(t)
                if not contrato_id:
                    counters["titulos_missing_contrato"] += 1
                    continue
                status_t = self._pick_first(t, ["status"])
                if status_t:
                    st = str(status_t).lower()
                    statuses[st] = statuses.get(st, 0) + 1
                    if st not in ("aberto", "em aberto", "aberta"):
                        counters["titulos_skip_status"] += 1
                        continue
                    counters["titulos_status_aberto"] += 1

                venc_raw = self._pick_first(t, ["dataVencimento", "data_vencimento", "vencimento", "dtVencimento", "dataVenc", "data"])
                venc = self._parse_date(venc_raw)
                if not venc:
                    counters["titulos_missing_vencimento"] += 1
                    continue
                dias = (date.today() - venc).days
                if dias <= dias_min:
                    counters["titulos_filtered_days"] += 1
                    continue

                valor = self._extract_number(self._pick_first(t, ["valorCorrigido", "valor", "valor_em_aberto", "valor_aberto"]))

                acc = per_contrato.get(str(contrato_id))
                if not acc:
                    per_contrato[str(contrato_id)] = {
                        "max_dias": dias,
                        "oldest_venc": venc,
                        "valor": float(valor) if valor is not None else 0.0,
                    }
                else:
                    acc["max_dias"] = max(acc["max_dias"], dias)
                    acc["oldest_venc"] = min(acc["oldest_venc"], venc)
                    if valor is not None:
                        acc["valor"] += float(valor)

            total = int(pag.get("total") or 0)
            parcial = int(pag.get("parcial") or len(titulos))
            offset += parcial
            if offset >= total or parcial == 0:
                break

        return per_contrato, {
            "data_ini": data_ini,
            "data_fim": data_fim,
            "pages": pages,
            "offset": offset,
            "titulos_status_counts": statuses,
            "truncated_time_fetch": truncated_time,
            "counters": counters,
        }

    def _fetch_titulos_por_contrato(self, contrato_id):
        limit = 200
        offset = 0
        pages = 0
        out = []

        while pages < 20:
            pages += 1
            resp, mode = self._post_ura('/ura/titulos/', {"contrato": str(contrato_id), "limit": str(limit), "offset": str(offset)}, timeout=60)
            content_type = resp.headers.get("Content-Type", "")
            if resp.status_code in (301, 302, 303, 307, 308):
                raise RuntimeError(f"URA titulos redirect {resp.status_code} ({mode}) -> {resp.headers.get('Location','')}")
            if resp.status_code != 200:
                raise RuntimeError(f"URA titulos HTTP {resp.status_code} ({mode}): {(resp.text or '')[:200]}")
            if "text/html" in content_type.lower():
                raise RuntimeError(f"URA titulos HTML ({mode}): {(resp.text or '')[:200]}")
            try:
                data = resp.json()
            except Exception as e:
                raise RuntimeError(f"URA titulos JSON inválido ({mode}): {type(e).__name__}: {str(e)}")
            titulos = None
            pag = None
            if isinstance(data, list):
                titulos = data
            elif isinstance(data, dict):
                # Alguns ambientes retornam o mesmo formato paginado usado em _ura_titulos_page:
                # { paginacao: {...}, titulos: [...] }
                titulos = data.get("titulos")
                pag = data.get("paginacao") or {}
            if not isinstance(titulos, list):
                raise RuntimeError(f"URA titulos resposta inesperada: {type(data).__name__}")

            out.extend(titulos)
            if pag and isinstance(pag, dict):
                try:
                    total = int(pag.get("total") or 0)
                    parcial = int(pag.get("parcial") or len(titulos))
                except Exception:
                    total = 0
                    parcial = len(titulos)
                offset += parcial
                if offset >= total or parcial == 0:
                    break
            else:
                if len(titulos) < limit:
                    break
                offset += limit

        return out

    def _get_boletos_em_aberto(self, contrato_id, dias_min=None):
        dias_key = "all" if dias_min is None else str(int(dias_min))
        key = f"{contrato_id}:{dias_key}"
        cached = ProxyHandler.titulos_cache.get(key)
        if cached and (time.time() - cached.get("ts", 0)) < ProxyHandler.TITULOS_TTL_SECONDS:
            return cached.get("items") or []

        titulos = self._fetch_titulos_por_contrato(contrato_id)
        # NOTE: mantenha este processamento simples; debug detalhado fica no handler /api/boletos
        out = []
        for t in titulos:
            try:
                if self._is_titulo_pago(t):
                    continue
                venc = self._titulo_vencimento_date(t)
                if not venc:
                    continue
                dias = (date.today() - venc).days
                if dias_min is not None and dias < int(dias_min):
                    continue
                valor = self._titulo_valor(t)
                status_txt = self._pick_first(t, ["status", "situacao", "titulo_status"])
                out.append({
                    "vencimento": venc.isoformat(),
                    "valor": round(float(valor), 2) if valor is not None else None,
                    "dias_atraso": int(dias),
                    "status": str(status_txt) if status_txt is not None else None,
                })
            except Exception:
                continue

        out.sort(key=lambda x: (x.get("vencimento") or ""), reverse=False)
        ProxyHandler.titulos_cache[key] = {"ts": time.time(), "items": out}
        return out

    def _summarize_titulos(self, titulos, limit=5):
        if not isinstance(titulos, list):
            return {"count": 0, "sample": []}
        sample = []
        for t in titulos[: max(0, int(limit or 0))]:
            if not isinstance(t, dict):
                continue
            sample.append({
                "status": self._pick_first(t, ["status", "situacao", "titulo_status"]),
                "venc_raw": self._pick_first(t, ["data_vencimento", "dt_vencimento", "vencimento", "titulo_vencimento", "boleto_vencimento", "dataVencimento", "dtVencimento", "dataVenc"]),
                "valor_raw": self._pick_first(t, ["valor_em_aberto", "valor_aberto", "valor_vencido", "valor", "valor_titulo", "valor_original", "valor_total", "valorCorrigido"]),
                "contrato_raw": self._pick_first(t, ["clienteContrato", "cliente_contrato", "clienteContratoId", "cliente_contrato_id", "contrato", "contrato_id", "contratoId"]),
            })
        return {"count": len(titulos), "sample": sample}

    def _is_titulo_pago(self, titulo):
        status = self._pick_first(titulo, ["status", "situacao", "titulo_status"])
        if status:
            s = str(status).lower()
            if "pago" in s or "baix" in s or "liquid" in s or "quit" in s:
                return True
        paid_date = self._pick_first(titulo, ["data_pagamento", "dt_pagamento", "pagamento", "data_baixa"])
        return self._parse_date(paid_date) is not None

    def _titulo_vencimento_date(self, titulo):
        v = self._pick_first(titulo, [
            "data_vencimento",
            "dt_vencimento",
            "dataVencimento",
            "dtVencimento",
            "dataVenc",
            "vencimento",
            "titulo_vencimento",
            "boleto_vencimento",
        ])
        return self._parse_date(v)

    def _titulo_valor(self, titulo):
        v = self._pick_first(titulo, [
            "valor_em_aberto",
            "valor_aberto",
            "valor_vencido",
            "valorCorrigido",
            "valor",
            "valor_titulo",
            "valor_original",
            "valor_total",
        ])
        return self._extract_number(v)

    def _diagnose_sgp(self):
        host = "sgp.net4you.com.br"
        diag = {
            "host": host,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        try:
            addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443)})
            diag["dns"] = {"ok": True, "addresses": addrs}
        except Exception as e:
            diag["dns"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)}"}

        try:
            resp = requests.get(
                f"{SGP_BASE}/estoque/comodato/list/",
                auth=AUTH,
                params={"data_cadastro_ini": "2026-04-01", "data_cadastro_fim": "2026-04-01"},
                timeout=20,
            )
            diag["probe"] = {"ok": resp.status_code == 200, "status_code": resp.status_code, "body_head": resp.text[:120]}
        except Exception as e:
            diag["probe"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)}"}

        return diag

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def _extract_bearer_token(self):
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _is_protected_path(self, path_only: str) -> bool:
        if not AUTH_ENABLED:
            return False
        p = (path_only or "").strip() or "/"
        if p.startswith("/api/"):
            if p.startswith("/api/auth/"):
                return False
            if p in ("/api/health", "/api/buildinfo"):
                return False
            return True
        if p in ("/comodato/list", "/comodatoitens/list", "/contrato", "/contrato/"):
            return True
        return False

    def _require_auth(self):
        token = self._extract_bearer_token()
        if not token:
            self._send_json(401, {"ok": False, "message": "Autenticação necessária."})
            return None
        payload = _verify_simple_jwt(token, AUTH_JWT_SECRET)
        if not payload:
            self._send_json(401, {"ok": False, "message": "Token inválido ou expirado."})
            return None
        return payload

    def _read_json_body(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0) or 0)
        except Exception:
            content_length = 0
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length) or b""
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return None

    def _fetch_sgp_user_info(self, username: str, password: str, timeout=30):
        url = f"{SGP_BASE}/auth/info/"
        resp = requests.get(
            url,
            auth=(username, password),
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            if resp.status_code in (401, 403):
                raise RuntimeError("Credenciais inválidas")
            raise RuntimeError(f"Erro ao autenticar no SGP: HTTP {resp.status_code}")
        try:
            return resp.json()
        except Exception:
            raise RuntimeError("Resposta inválida do SGP (esperado JSON)")

    def _user_has_admin_group(self, user_info: dict) -> bool:
        target = str(AUTH_ADMIN_GROUP or "").strip().lower()
        if not target:
            return True
        grupos = user_info.get("grupos") if isinstance(user_info, dict) else None
        if not isinstance(grupos, list):
            return False
        for g in grupos:
            desc = ""
            try:
                desc = str((g or {}).get("descricao") or "").strip().lower()
            except Exception:
                desc = ""
            if desc == target:
                return True
        return False

    def do_GET(self):
        path_only = urlparse(self.path).path
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            return

        if path_only == "/api/auth/me":
            auth = self._require_auth()
            if not auth:
                return
            self._send_json(200, {"ok": True, "user": {"username": auth.get("sub"), "nome": auth.get("nome"), "email": auth.get("email"), "isAdmin": bool(auth.get("isAdmin"))}})
            return

        if path_only.startswith('/api/buildinfo'):
            self._send_json(200, self._buildinfo())
            return

        if path_only == '/version.py':
            base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
            fp = os.path.join(base_dir, 'version.py')
            if not os.path.exists(fp):
                self.send_response(404)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'Not Found')
                return
            with open(fp, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only.startswith('/api/health'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            check = params.get('check', ['0'])[0] == '1'
            payload = {"ok": True}
            if check:
                payload["sgp"] = self._diagnose_sgp()
            self._send_json(200, payload)
            return

        if self._is_protected_path(path_only):
            if not self._require_auth():
                return

        if path_only.startswith('/api/boletos'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            contrato_id = (params.get('contrato', [''])[0] or '').strip()
            debug = params.get('debug', ['0'])[0] in ('1', 'true', 'yes')
            nocache = params.get('nocache', ['0'])[0] in ('1', 'true', 'yes')
            try:
                dias_raw = params.get('dias', [None])[0]
                dias_min = None if (dias_raw is None or str(dias_raw).strip() == '') else int(dias_raw)
            except Exception:
                dias_min = None

            if not contrato_id:
                self._send_json(400, {"error": "Parâmetro obrigatório ausente: contrato"})
                return

            try:
                if nocache:
                    # remove apenas as entradas do contrato solicitado (todas as variantes de dias)
                    for k in list(ProxyHandler.titulos_cache.keys()):
                        if str(k).startswith(f"{contrato_id}:"):
                            ProxyHandler.titulos_cache.pop(k, None)

                items = self._get_boletos_em_aberto(contrato_id, dias_min)
                payload = {"contrato_id": str(contrato_id), "dias_min": dias_min, "items": items}
                if debug:
                    try:
                        titulos = self._fetch_titulos_por_contrato(contrato_id)
                        payload["debug"] = {
                            "ura_titulos": self._summarize_titulos(titulos, limit=7),
                            "parsed_items": len(items),
                        }
                    except Exception as e:
                        payload["debug"] = {"error": f"{type(e).__name__}: {str(e)}"}
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(502, {"error": "Falha ao consultar boletos", "details": {"message": f"{type(e).__name__}: {str(e)}"}})
            return

        if path_only.startswith('/api/contratos-suspensos-boletos'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            dias_min = 30
            # lookback menor por padrão para evitar chamadas muito longas (ajustável via querystring)
            lookback_days = 240
            max_pages = 30
            max_contratos_check = 500
            max_fetch_seconds = 45
            max_status_seconds = 45
            include_boletos = True
            max_boletos_seconds = 30

            try:
                dias_min = int(params.get('dias', ['30'])[0] or 30)
                if dias_min < 1:
                    dias_min = 1
            except Exception:
                dias_min = 30

            try:
                lookback_days = int(params.get('lookback', [str(lookback_days)])[0] or lookback_days)
                if lookback_days < 30:
                    lookback_days = 30
                if lookback_days > 3650:
                    lookback_days = 3650
            except Exception:
                lookback_days = 240

            # orçamento de tempo
            try:
                # compat: max_seconds define orçamento total aproximado (divide entre fetch e status)
                max_seconds_total = int(params.get('max_seconds', [''])[0] or 0)
                if max_seconds_total > 0:
                    # garante um mínimo para cada fase
                    max_fetch_seconds = max(10, int(max_seconds_total * 0.55))
                    max_status_seconds = max(10, max_seconds_total - max_fetch_seconds)
            except Exception:
                pass

            try:
                max_fetch_seconds = int(params.get('max_fetch_seconds', [str(max_fetch_seconds)])[0] or max_fetch_seconds)
                if max_fetch_seconds < 5:
                    max_fetch_seconds = 5
                if max_fetch_seconds > 600:
                    max_fetch_seconds = 600
            except Exception:
                max_fetch_seconds = 45

            try:
                max_status_seconds = int(params.get('max_status_seconds', [str(max_status_seconds)])[0] or max_status_seconds)
                if max_status_seconds < 5:
                    max_status_seconds = 5
                if max_status_seconds > 600:
                    max_status_seconds = 600
            except Exception:
                max_status_seconds = 45

            try:
                max_pages = int(params.get('max_pages', [str(max_pages)])[0] or max_pages)
                if max_pages < 1:
                    max_pages = 1
                if max_pages > 200:
                    max_pages = 200
            except Exception:
                max_pages = 30

            try:
                max_contratos_check = int(params.get('max_contratos', [str(max_contratos_check)])[0] or max_contratos_check)
                if max_contratos_check < 50:
                    max_contratos_check = 50
                if max_contratos_check > 5000:
                    max_contratos_check = 5000
            except Exception:
                max_contratos_check = 500

            try:
                include_boletos = params.get('boletos', ['1'])[0] not in ('0', 'false', 'no')
            except Exception:
                include_boletos = True

            try:
                max_boletos_seconds = int(params.get('max_boletos_seconds', [str(max_boletos_seconds)])[0] or max_boletos_seconds)
                if max_boletos_seconds < 0:
                    max_boletos_seconds = 0
                if max_boletos_seconds > 600:
                    max_boletos_seconds = 600
            except Exception:
                max_boletos_seconds = 30

            try:
                nocache = params.get('nocache', ['0'])[0] in ('1', 'true', 'yes')
                cache_key = f"{dias_min}:{lookback_days}:{max_pages}:{max_contratos_check}:{max_fetch_seconds}:{max_status_seconds}:{1 if include_boletos else 0}:{max_boletos_seconds}"
                cached = None if nocache else ProxyHandler.results_cache.get(cache_key)
                if cached and (time.time() - cached.get("ts", 0)) < ProxyHandler.RESULTS_TTL_SECONDS:
                    self._send_json(200, cached.get("payload"))
                    return

                started = time.time()
                fetch_started = time.time()
                per_contrato, meta = self._fetch_overdue_contracts(
                    dias_min,
                    lookback_days,
                    max_pages=max_pages,
                    started_ts=fetch_started,
                    max_seconds=max_fetch_seconds,
                )
                meta["max_pages"] = max_pages
                meta["max_contratos_check"] = max_contratos_check
                meta["max_fetch_seconds"] = max_fetch_seconds
                meta["max_status_seconds"] = max_status_seconds
                meta["fetch_seconds"] = round(time.time() - fetch_started, 2)

                contratos_ids = list(per_contrato.keys())
                truncated_candidates = False
                if len(contratos_ids) > max_contratos_check:
                    truncated_candidates = True
                    contratos_ids = sorted(
                        contratos_ids,
                        key=lambda cid: int((per_contrato.get(cid) or {}).get("max_dias") or 0),
                        reverse=True,
                    )[:max_contratos_check]

                out = []
                max_workers = 12
                checked = 0
                suspensos = 0
                status_started = time.time()
                status_deadline = status_started + max_status_seconds

                ex = ThreadPoolExecutor(max_workers=max_workers)
                futs = {}
                try:
                    it = iter(contratos_ids)
                    while True:
                        while len(futs) < max_workers * 2:
                            if time.time() > status_deadline:
                                break
                            try:
                                cid = next(it)
                            except StopIteration:
                                break
                            futs[ex.submit(self._get_contrato_info_fast, cid)] = cid

                        if not futs:
                            break

                        if time.time() > status_deadline:
                            break

                        done, _pending = wait(futs.keys(), timeout=1, return_when=FIRST_COMPLETED)
                        if not done:
                            continue

                        for fut in done:
                            cid = futs.pop(fut, None)
                            if cid is None:
                                continue
                            checked += 1
                            try:
                                info = fut.result() or {}
                            except Exception:
                                continue

                            status_text = info.get("status_text")
                            status_code = info.get("status_code")
                            if not self._is_suspended_status(status_text, status_code):
                                continue

                            suspensos += 1
                            acc = per_contrato.get(cid) or {}
                            out.append({
                                "contrato_id": str(cid),
                                "cliente_id": info.get("cliente_id") or "",
                                "nome": info.get("nome") or "-",
                                "cpfcnpj": info.get("cpfcnpj") or "",
                                "status": str(status_text or "-"),
                                "status_code": status_code,
                                "dias_atraso": int(acc.get("max_dias") or 0),
                                "vencimento": acc["oldest_venc"].isoformat() if acc.get("oldest_venc") else "-",
                                "valor_aberto": round(float(acc.get("valor") or 0.0), 2) if (acc.get("valor") or 0.0) > 0 else None,
                            })
                finally:
                    for fut in list(futs.keys()):
                        fut.cancel()
                    ex.shutdown(wait=False, cancel_futures=True)

                out.sort(key=lambda x: x.get("dias_atraso", 0), reverse=True)

                boletos_started = time.time()
                boletos_truncated = False
                boletos_enriched = 0
                if include_boletos and max_boletos_seconds > 0 and out:
                    deadline = boletos_started + max_boletos_seconds
                    for item in out:
                        if time.time() > deadline:
                            boletos_truncated = True
                            break
                        try:
                            # Detalhe: traz todos os boletos em aberto do contrato (não apenas os >= dias_min),
                            # porque o usuário precisa ver a lista completa.
                            item["boletos"] = self._get_boletos_em_aberto(item.get("contrato_id"), None)
                        except Exception:
                            item["boletos"] = []
                        boletos_enriched += 1

                truncated_time = time.time() > status_deadline
                total_seconds = round(time.time() - started, 2)
                payload = {
                    "meta": {
                        **meta,
                        "dias_min": dias_min,
                        "lookback_days": lookback_days,
                        "contratos_com_titulo_vencido": len(per_contrato),
                        "contratos_status_checked": checked,
                        "contratos_suspensos": suspensos,
                        "status_seconds": round(time.time() - status_started, 2),
                        "boletos_included": bool(include_boletos),
                        "boletos_enriched": int(boletos_enriched),
                        "boletos_seconds": round(time.time() - boletos_started, 2),
                        "boletos_truncated": bool(boletos_truncated),
                        "seconds": total_seconds,
                        "cache_ttl_seconds": ProxyHandler.RESULTS_TTL_SECONDS,
                        "suspended_status_codes": sorted(list(SUSPENDED_STATUS_CODES)),
                        "suspended_status_tokens": SUSPENDED_STATUS_TOKENS,
                        "truncated": bool(truncated_candidates or truncated_time),
                        "truncated_reason": "status_time_limit" if truncated_time else ("max_contratos" if truncated_candidates else None),
                    },
                    "items": out,
                }
                ProxyHandler.results_cache[cache_key] = {"ts": time.time(), "payload": payload}
                self._send_json(200, payload)
            except Exception as e:
                details = None
                try:
                    details = json.loads(str(e))
                except Exception:
                    details = {"message": str(e)}
                self._send_json(502, {"error": "Falha ao consultar SGP", "details": details, "diagnostic": self._diagnose_sgp()})
            return

        if path_only.startswith('/comodato/list'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            ini = params.get('data_cadastro_ini', [''])[0]
            fim = params.get('data_cadastro_fim', [''])[0]
            resp = requests.get(f'{SGP_BASE}/estoque/comodato/list/', auth=AUTH, params={'data_cadastro_ini': ini, 'data_cadastro_fim': fim}, timeout=60)
            self.send_response(resp.status_code)
            self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json; charset=utf-8'))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(resp.content)
            return
        elif path_only.startswith('/comodatoitens/list'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            cid = params.get('comodato_id', [''])[0]
            resp = requests.get(f'{SGP_BASE}/estoque/comodatoitens/list/', auth=AUTH, params={'comodato_id': cid}, timeout=30)
            self.send_response(resp.status_code)
            self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json; charset=utf-8'))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(resp.content)
        elif path_only == '/' or path_only == '/index.html' or path_only == '/dashboard_financeiro.html' or path_only == '/test.html':
            self.path = '/dashboard_financeiro.html'
            super().do_GET()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')

    def do_POST(self):
        path_only = urlparse(self.path).path

        if path_only == "/api/auth/login":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "message": "JSON inválido no corpo da requisição."})
                return
            username = str((body or {}).get("username") or "").strip()
            password = str((body or {}).get("password") or "").strip()
            if not username or not password:
                self._send_json(400, {"ok": False, "message": "Usuário e senha são obrigatórios."})
                return
            try:
                info = self._fetch_sgp_user_info(username, password, timeout=30)
                if not self._user_has_admin_group(info):
                    self._send_json(403, {"ok": False, "message": f"Acesso negado. Necessário grupo '{AUTH_ADMIN_GROUP}' no SGP."})
                    return
                now = int(time.time())
                payload = {
                    "sub": str(info.get("usuario") or username),
                    "nome": str(info.get("nome") or ""),
                    "email": str(info.get("email") or ""),
                    "grupos": info.get("grupos") or [],
                    "isAdmin": True,
                    "iat": now,
                    "exp": now + int(AUTH_TOKEN_TTL_SECONDS),
                    "jti": secrets.token_urlsafe(16),
                }
                token = _create_simple_jwt(payload, AUTH_JWT_SECRET)
                self._send_json(200, {"ok": True, "token": token, "user": {"username": payload["sub"], "nome": payload["nome"], "email": payload["email"], "isAdmin": True}})
            except Exception as e:
                self._send_json(401, {"ok": False, "message": str(e) or "Falha na autenticação."})
            return

        if path_only == "/api/auth/me":
            auth = self._require_auth()
            if not auth:
                return
            self._send_json(200, {"ok": True, "user": {"username": auth.get("sub"), "nome": auth.get("nome"), "email": auth.get("email"), "isAdmin": bool(auth.get("isAdmin"))}})
            return

        if path_only == "/api/auth/logout":
            # Stateless: apenas responde OK. O frontend limpa o token.
            self._send_json(200, {"ok": True, "message": "Logout realizado."})
            return

        if path_only == "/api/requerer":
            auth = self._require_auth()
            if not auth:
                return
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"ok": False, "message": "JSON inválido no corpo da requisição."})
                return
            contrato = str((body or {}).get("contrato") or (body or {}).get("contrato_id") or "").strip()
            if not contrato:
                self._send_json(400, {"ok": False, "message": "Parâmetro obrigatório ausente: contrato"})
                return

            cliente_nome = str((body or {}).get("cliente_nome") or (body or {}).get("nome") or "").strip()
            telefone = str((body or {}).get("telefone") or "").strip()
            observacao_extra = str((body or {}).get("observacao") or "").strip()

            requested_by = str(auth.get("sub") or "").strip()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            observacao = f"Requerido via Dashboard Financeiro por {requested_by} em {now}."
            if observacao_extra:
                observacao = f"{observacao}\n{observacao_extra}"

            payload = {
                "contrato": contrato,
                "conteudo": str(REQUERER_CONTEUDO or "Requerimento"),
                "observacao": observacao,
                "ocorrenciatipo": int(REQUERER_OCORRENCIA_TIPO),
                "motivoos": int(REQUERER_MOTIVO_OS),
                "setor": int(REQUERER_SETOR),
                "os_prioridade": int(REQUERER_PRIORIDADE),
            }
            if cliente_nome:
                payload["contato_nome"] = cliente_nome
            if telefone:
                payload["contato_telefone"] = telefone
            if requested_by:
                # No SGP, este campo costuma aceitar o usuário/nome do responsável.
                payload["responsavel"] = requested_by

            class_field = str(REQUERER_CLASSIFICACAO_FIELD or "").strip()
            class_value = str(REQUERER_CLASSIFICACAO_VALUE or "").strip()
            if class_field and class_value:
                payload[class_field] = class_value

            try:
                resp, _mode = self._post_ura("/ura/chamado/", payload, timeout=40)
                # repassar status e tentar decodificar JSON
                content_type = (resp.headers.get("Content-Type") or "").lower()
                out = None
                if "application/json" in content_type:
                    try:
                        out = resp.json()
                    except Exception:
                        out = {"raw": (resp.text or "")[:500]}
                else:
                    try:
                        out = resp.json()
                    except Exception:
                        out = {"raw": (resp.text or "")[:500]}
                if resp.status_code != 200:
                    self._send_json(resp.status_code, {"ok": False, "message": "Falha ao criar ocorrência no SGP.", "sgp": out})
                    return
                self._send_json(200, {"ok": True, "message": "Ocorrência criada.", "sgp": out})
            except Exception as e:
                self._send_json(502, {"ok": False, "message": "Falha ao criar ocorrência.", "details": {"message": f"{type(e).__name__}: {str(e)}"}})
            return

        if self._is_protected_path(path_only):
            if not self._require_auth():
                return

        if self.path == '/contrato/' or self.path == '/contrato':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            params = parse_qs(post_data)
            contrato = (params.get('contrato', [''])[0] or '').strip()
            if not contrato:
                self._send_json(400, {"error": "Parâmetro obrigatório ausente: contrato"})
                return

            # Permite override via POST, mas usa defaults do config.json quando não vierem.
            override_token = (params.get('token', [''])[0] or '').strip()
            override_app = (params.get('app', [''])[0] or '').strip()

            try:
                payload = {"contrato": contrato}
                if override_token:
                    payload["token"] = override_token
                if override_app:
                    payload["app"] = override_app
                resp, _mode = self._post_ura('/ura/listacontrato/', payload, timeout=30)
                self.send_response(resp.status_code)
                self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(resp.content)
            except Exception as e:
                self._send_json(502, {"error": "Falha ao consultar SGP", "details": {"message": f"{type(e).__name__}: {str(e)}"}})
        else:
            self.send_response(404)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'Not Found')

os.chdir(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd())
class ReusableTCPServer(ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

with ReusableTCPServer(("", PORT), ProxyHandler) as httpd:
    print(f"Server running at http://localhost:{PORT}/")
    httpd.serve_forever()
