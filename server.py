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
import traceback

SGP_BASE = 'https://sgp.net4you.com.br/api'
AUTH = ('robo', 'Ox(?YMae?0V3V#}HIGcF')
PORT = 8000

APP_TOKEN = '37ab7243-9e9c-45bc-a393-e2ccbf76eff2'
APP_NAME = 'financeiro'
AUTH_ENABLED = True
AUTH_JWT_SECRET = "dashboard-secret-change-in-production"
AUTH_ADMIN_GROUP = "financeiro"
AUTH_TOKEN_TTL_SECONDS = 12 * 60 * 60
REQUERER_CONTEUDO = "Financeiro - Negociação (Suspenso)"
REQUERER_OCORRENCIA_TIPO = 4018
REQUERER_MOTIVO_OS = None
REQUERER_SETOR = 1
REQUERER_PRIORIDADE = 2
REQUERER_STATUS_FIELD = "status"
REQUERER_STATUS_VALUE = "Encerrada"
REQUERER_ENCERRAR_OS_FIELD = "encerrar_os"
REQUERER_ENCERRAR_OS_VALUE = "1"
# Campo/valor opcionais para "classificação" (algumas instâncias do SGP exigem/aceitam chaves diferentes).
REQUERER_CLASSIFICACAO_FIELD = "classificacao"
REQUERER_CLASSIFICACAO_VALUE = "Suspenso"
REQUERER_LOOKUP_ENDPOINTS = [
    "/ura/ocorrencia/list/",
    "/ura/ocorrencias/list/",
    "/ura/chamado/list/",
    "/ura/chamados/list/",
    "/ura/ordemservico/list/",
]

OC_LOOKUP_ENDPOINTS = [
    "/ura/ordemservico/list/",
    "/ura/chamado/list/",
    "/ura/ocorrencia/list/",
]
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
            if v is not None and str(v).strip() != "":
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
            REQUERER_STATUS_FIELD = str(req_cfg.get('status_field') or REQUERER_STATUS_FIELD).strip()
        except Exception:
            pass
        try:
            REQUERER_STATUS_VALUE = str(req_cfg.get('status_value') or REQUERER_STATUS_VALUE).strip()
        except Exception:
            pass
        try:
            REQUERER_ENCERRAR_OS_FIELD = str(req_cfg.get('encerrar_os_field') or REQUERER_ENCERRAR_OS_FIELD).strip()
        except Exception:
            pass
        try:
            v = req_cfg.get('encerrar_os_value')
            if v is not None:
                REQUERER_ENCERRAR_OS_VALUE = str(v).strip()
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
        try:
            eps = req_cfg.get("lookup_endpoints")
            if isinstance(eps, list) and eps:
                REQUERER_LOOKUP_ENDPOINTS = [str(x).strip() for x in eps if str(x).strip()]
        except Exception:
            pass
        try:
            eps = req_cfg.get("oc_lookup_endpoints")
            if isinstance(eps, list) and eps:
                OC_LOOKUP_ENDPOINTS = [str(x).strip() for x in eps if str(x).strip()]
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
    auth_sessions = {}
    contrato_cache = {}
    contrato_status_cache = {}
    results_cache = {}
    RESULTS_TTL_SECONDS = 300
    titulos_cache = {}
    TITULOS_TTL_SECONDS = 300
    ocorrencias_cache = {}
    OCORRENCIAS_TTL_SECONDS = 120

    def _sanitize_for_log(self, value):
        secret_keys = {"token", "password", "passwd", "senha", "authorization", "auth"}
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                key_norm = str(k or "").strip().lower()
                if key_norm in secret_keys:
                    out[k] = "***"
                else:
                    out[k] = self._sanitize_for_log(v)
            return out
        if isinstance(value, list):
            return [self._sanitize_for_log(v) for v in value]
        return value

    def _log_requerer_diag(self, stage, payload):
        try:
            entry = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "requerer_diag",
                "stage": str(stage or "").strip() or "unknown",
                "data": self._sanitize_for_log(payload),
            }
            print(json.dumps(entry, ensure_ascii=False), flush=True)
        except Exception:
            print(
                json.dumps({
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "kind": "requerer_diag",
                    "stage": "logger_error",
                    "data": {"error": traceback.format_exc(limit=1)},
                }, ensure_ascii=False),
                flush=True,
            )

    @classmethod
    def _cleanup_auth_sessions(cls):
        now = int(time.time())
        expired = [key for key, session in cls.auth_sessions.items() if int((session or {}).get("exp") or 0) <= now]
        for key in expired:
            cls.auth_sessions.pop(key, None)

    @classmethod
    def _store_auth_session(cls, jti: str, username: str, password: str, exp: int, user_info: dict):
        cls._cleanup_auth_sessions()
        if not jti:
            return
        cls.auth_sessions[str(jti)] = {
            "username": str(username or "").strip(),
            "password": str(password or ""),
            "exp": int(exp or 0),
            "user_info": user_info if isinstance(user_info, dict) else {},
        }

    @classmethod
    def _pop_auth_session(cls, jti: str):
        if not jti:
            return None
        return cls.auth_sessions.pop(str(jti), None)

    @classmethod
    def _get_auth_session(cls, jti: str):
        cls._cleanup_auth_sessions()
        if not jti:
            return None
        session = cls.auth_sessions.get(str(jti))
        if not session:
            return None
        if int((session or {}).get("exp") or 0) <= int(time.time()):
            cls.auth_sessions.pop(str(jti), None)
            return None
        return session

    def _post_sgp(self, path, payload, timeout=60, auth_override=None):
        url = f'{SGP_BASE}{path}'
        basic_auth = auth_override or AUTH
        # Tenta JSON primeiro (padrão moderno), depois form-encoded (alguns endpoints do SGP aceitam melhor)
        try:
            return requests.post(url, auth=basic_auth, json=payload, timeout=timeout), "json"
        except Exception:
            return requests.post(url, auth=basic_auth, data=payload, timeout=timeout), "form"

    def _post_ura(self, path, data, timeout=60, auth_override=None):
        url = f'{SGP_BASE}{path}'
        base = {"token": APP_TOKEN, "app": APP_NAME}
        base.update(data or {})
        basic_auth = auth_override or AUTH

        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "dashboard-financeiro/1.0",
        }

        def as_files(payload):
            return {k: (None, "" if v is None else str(v)) for k, v in payload.items()}

        # URA (conforme Postman) costuma usar form-data; tentamos multipart primeiro.
        try:
            resp = requests.post(url, auth=basic_auth, files=as_files(base), headers=headers, timeout=timeout, allow_redirects=False)
            return resp, "multipart"
        except Exception:
            # fallback: x-www-form-urlencoded
            resp = requests.post(url, auth=basic_auth, data=base, headers=headers, timeout=timeout, allow_redirects=False)
            return resp, "urlencoded"

    def _list_ura(self, path, payload, timeout=40, auth_override=None):
        resp, _mode = self._post_ura(path, payload, timeout=timeout, auth_override=auth_override)
        ct = (resp.headers.get("Content-Type") or "").lower()
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"URA {path} resposta não JSON (HTTP {resp.status_code}): {(resp.text or '')[:200]}")
        if resp.status_code != 200:
            msg = None
            if isinstance(data, dict):
                msg = data.get("message") or data.get("msg") or data.get("detail")
            raise RuntimeError(f"URA {path} HTTP {resp.status_code}: {msg or str(data)[:200]}")
        return data

    def _extract_list(self, data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "data", "results", "list", "rows"):
                v = data.get(key)
                if isinstance(v, list):
                    return v
        return None

    def _fetch_ocorrencias_contrato(self, contrato_id, nocache=False, timeout=40, max_total_seconds=20, auth_override=None):
        contrato_id = str(contrato_id or "").strip()
        if not contrato_id:
            return [], []
        key = f"{contrato_id}"
        now = time.time()
        if not nocache:
            cached = ProxyHandler.ocorrencias_cache.get(key)
            if cached and (now - cached.get("ts", 0) < ProxyHandler.OCORRENCIAS_TTL_SECONDS):
                return cached.get("items") or [], cached.get("attempts") or []

        attempts = []
        started = time.time()
        payloads = [
            {"contrato": contrato_id},
            {"contrato_id": contrato_id},
            {"contrato": int(contrato_id)} if contrato_id.isdigit() else {"contrato": contrato_id},
        ]

        for ep in REQUERER_LOOKUP_ENDPOINTS:
            if max_total_seconds is not None and (time.time() - started) > float(max_total_seconds):
                attempts.append({"endpoint": str(ep), "error": "TimeLimit: max_total_seconds"})
                break
            ep = str(ep or "").strip()
            if not ep:
                continue
            if not ep.startswith("/"):
                ep = "/" + ep
            for pld in payloads:
                if max_total_seconds is not None and (time.time() - started) > float(max_total_seconds):
                    attempts.append({"endpoint": ep, "payload_keys": sorted(list(pld.keys())), "error": "TimeLimit: max_total_seconds"})
                    break
                try:
                    data = self._list_ura(ep, pld, timeout=timeout, auth_override=auth_override)
                    lst = self._extract_list(data)
                    if lst is None:
                        raise RuntimeError(f"URA {ep} resposta inesperada: {type(data).__name__}")
                    ProxyHandler.ocorrencias_cache[key] = {"ts": now, "items": lst, "attempts": attempts}
                    return lst, attempts
                except Exception as e:
                    attempts.append({"endpoint": ep, "payload_keys": sorted(list(pld.keys())), "error": f"{type(e).__name__}: {str(e)}"})

        ProxyHandler.ocorrencias_cache[key] = {"ts": now, "items": [], "attempts": attempts}
        return [], attempts

    def _fetch_ocorrencia_por_id(self, oc_id, nocache=False, timeout=40, max_total_seconds=20, auth_override=None):
        oc_id = str(oc_id or "").strip()
        if not oc_id:
            return None, []
        attempts = []
        started = time.time()

        id_payloads = [
            {"os_id": oc_id},
            {"id": oc_id},
            {"ocorrencia_id": oc_id},
            {"chamado_id": oc_id},
            {"os": oc_id},
            {"protocolo": oc_id},
            {"codigo": oc_id},
            {"numero": oc_id},
            {"num": oc_id},
            {"os_id": int(oc_id)} if oc_id.isdigit() else {"os_id": oc_id},
            {"id": int(oc_id)} if oc_id.isdigit() else {"id": oc_id},
            {"protocolo": int(oc_id)} if oc_id.isdigit() else {"protocolo": oc_id},
            {"codigo": int(oc_id)} if oc_id.isdigit() else {"codigo": oc_id},
            {"numero": int(oc_id)} if oc_id.isdigit() else {"numero": oc_id},
        ]

        def matches(item):
            if not isinstance(item, dict):
                return False
            for k in (
                "os_id",
                "id",
                "ocorrencia_id",
                "chamado_id",
                "osId",
                "protocolo",
                "protocolo_id",
                "codigo",
                "codigo_os",
                "os_codigo",
                "numero",
                "num",
            ):
                v = item.get(k)
                if v is None:
                    continue
                if str(v).strip() == oc_id:
                    return True
            # tenta também em campos aninhados
            inner = self._pick_first(item, ["os", "ocorrencia", "chamado", "ordem_servico", "ordemservico"])
            if isinstance(inner, dict):
                for k in ("id", "os_id", "protocolo", "codigo", "numero"):
                    v = inner.get(k)
                    if v is not None and str(v).strip() == oc_id:
                        return True
            return False

        endpoints = OC_LOOKUP_ENDPOINTS if isinstance(OC_LOOKUP_ENDPOINTS, list) and OC_LOOKUP_ENDPOINTS else REQUERER_LOOKUP_ENDPOINTS
        for ep in endpoints:
            if max_total_seconds is not None and (time.time() - started) > float(max_total_seconds):
                attempts.append({"endpoint": str(ep), "error": "TimeLimit: max_total_seconds"})
                break
            ep = str(ep or "").strip()
            if not ep:
                continue
            if not ep.startswith("/"):
                ep = "/" + ep
            for pld in id_payloads:
                if max_total_seconds is not None and (time.time() - started) > float(max_total_seconds):
                    attempts.append({"endpoint": ep, "payload_keys": sorted(list(pld.keys())), "error": "TimeLimit: max_total_seconds"})
                    break
                try:
                    data = self._list_ura(ep, pld, timeout=timeout, auth_override=auth_override)
                    lst = self._extract_list(data)
                    if lst is None:
                        # às vezes retorna 1 item dict
                        if isinstance(data, dict) and matches(data):
                            return data, attempts
                        raise RuntimeError(f"URA {ep} resposta inesperada: {type(data).__name__}")
                    for item in lst:
                        if matches(item):
                            return item, attempts
                except Exception as e:
                    attempts.append({"endpoint": ep, "payload_keys": sorted(list(pld.keys())), "error": f"{type(e).__name__}: {str(e)}"})
        return None, attempts

    def _pick_ocorrencia_requerer(self, ocorrencias):
        if not isinstance(ocorrencias, list):
            return None
        wanted_tipo = int(REQUERER_OCORRENCIA_TIPO)
        wanted_motivo = None
        try:
            if REQUERER_MOTIVO_OS is not None and str(REQUERER_MOTIVO_OS).strip() != "":
                wanted_motivo = int(REQUERER_MOTIVO_OS)
        except Exception:
            wanted_motivo = None

        def norm_text(value):
            s = str(value or "").strip().lower()
            if not s:
                return ""
            # remove acentos para evitar mismatch (ex.: "Negociacao" vs "Negociação")
            s = unicodedata.normalize("NFKD", s)
            return "".join(ch for ch in s if not unicodedata.combining(ch))

        wanted_class = norm_text(REQUERER_CLASSIFICACAO_VALUE)
        wanted_conteudo = norm_text(REQUERER_CONTEUDO)

        def to_int(x):
            try:
                return int(x)
            except Exception:
                return None

        candidates = []
        for oc in ocorrencias:
            if not isinstance(oc, dict):
                continue
            tipo = to_int(oc.get("ocorrenciatipo") or oc.get("ocorrencia_tipo") or oc.get("ocorrenciaTipo") or oc.get("tipo") or oc.get("tipo_id"))
            motivo = to_int(oc.get("motivoos") or oc.get("motivo_os") or oc.get("motivo") or oc.get("motivo_id"))
            conteudo = norm_text(oc.get("conteudo") or oc.get("assunto") or oc.get("titulo"))
            tipo_label = norm_text(
                oc.get("ocorrenciatipo_descricao")
                or oc.get("ocorrenciatipo_label")
                or oc.get("tipo_label")
                or oc.get("tipo_descricao")
                or oc.get("tipo_descricao_label")
            )
            classificacao = norm_text(oc.get("classificacao") or oc.get("classificação") or oc.get("status") or oc.get("situacao"))

            if tipo is not None and tipo != wanted_tipo:
                continue
            if wanted_motivo is not None and motivo is not None and motivo != wanted_motivo:
                continue
            if wanted_conteudo:
                # tenta bater com o "conteudo/assunto" e/ou com o label do tipo
                if conteudo and wanted_conteudo not in conteudo and tipo_label and wanted_conteudo not in tipo_label:
                    continue
                if conteudo and wanted_conteudo not in conteudo and not tipo_label:
                    continue
                if tipo_label and wanted_conteudo not in tipo_label and not conteudo:
                    continue
            if wanted_class and classificacao and wanted_class not in classificacao:
                continue

            candidates.append(oc)

        if not candidates:
            return None

        def sort_key(oc):
            # tenta ordenar por data de criação/atualização (ISO-like)
            for k in ("data", "data_cadastro", "data_abertura", "criado_em", "created_at", "createdAt"):
                v = oc.get(k)
                d = self._parse_date(v) if v else None
                if d:
                    return d.toordinal()
            return 0

        candidates.sort(key=sort_key, reverse=True)
        return candidates[0]

    def _norm_key(self, value):
        s = str(value or "").strip().lower()
        if not s:
            return ""
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        # keep only alnum to make matching resilient: "Aberta Por:" -> "abertapor"
        return "".join(ch for ch in s if ch.isalnum())

    def _stringify_actor(self, value):
        if value is None:
            return None
        if isinstance(value, dict):
            for k in ("nome", "usuario", "username", "login", "label", "descricao", "descrição"):
                v = value.get(k)
                if v not in (None, ""):
                    return str(v).strip() or None
            return None
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value).strip()
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s in ("", ".", "..", "...", "-", "—"):
            return None
        return s or None

    def _walk_strings(self, value):
        if isinstance(value, str):
            yield value
            return
        if isinstance(value, dict):
            for inner in value.values():
                yield from self._walk_strings(inner)
            return
        if isinstance(value, list):
            for inner in value:
                yield from self._walk_strings(inner)

    def _find_field_by_key_tokens(self, value, token_groups):
        groups = []
        for group in (token_groups or []):
            toks = tuple(self._norm_key(x) for x in group if self._norm_key(x))
            if toks:
                groups.append(toks)
        if not groups:
            return None

        def scan(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    nk = self._norm_key(k)
                    if nk and v not in (None, "", [], {}):
                        for group in groups:
                            if all(tok in nk for tok in group):
                                return v
                    got = scan(v)
                    if got not in (None, "", [], {}):
                        return got
            elif isinstance(node, list):
                for entry in node:
                    got = scan(entry)
                    if got not in (None, "", [], {}):
                        return got
            return None

        return scan(value)

    def _extract_actor_from_item(self, item):
        if not isinstance(item, dict):
            return None
        actor = (
            item.get("responsavel")
            or item.get("responsável")
            or item.get("usuario")
            or item.get("usuario_abertura")
            or item.get("criado_por")
            or item.get("created_by")
            or item.get("autor")
        )
        actor = self._stringify_actor(actor)
        if actor:
            return actor

        actor = self._get_any_field_by_norm(item, [
            "aberta por",
            "aberto por",
            "abertapor",
            "abertopor",
            "aberta_por",
            "aberto_por",
            "criado por",
            "criadopor",
            "criado_por",
            "created by",
            "created_by",
            "usuario_abertura",
            "usuariodeabertura",
        ])
        actor = self._stringify_actor(actor)
        if actor:
            return actor

        actor = self._find_field_by_key_tokens(item, [
            ("criado", "por"),
            ("created", "by"),
            ("aberto", "por"),
            ("aberta", "por"),
            ("usuario", "abertura"),
            ("user", "create"),
            ("author",),
            ("autor",),
        ])
        actor = self._stringify_actor(actor)
        if actor:
            return actor

        return self._extract_aberta_por_from_text(item)

    def _get_any_field_by_norm(self, item, wanted_norm_keys):
        if not isinstance(item, dict):
            return None
        wanted = {self._norm_key(k) for k in (wanted_norm_keys or []) if str(k or "").strip()}
        if not wanted:
            return None
        for k, v in item.items():
            if self._norm_key(k) in wanted and v not in (None, ""):
                return v

        # Alguns endpoints retornam campos como lista (ex.: [{"label":"Aberta Por:","value":"..."}])
        for container_key in ("campos", "fields", "dados", "custom_fields", "customFields", "extras"):
            container = item.get(container_key)
            if isinstance(container, list):
                for entry in container:
                    if not isinstance(entry, dict):
                        continue
                    label = (
                        entry.get("label")
                        or entry.get("nome")
                        or entry.get("campo")
                        or entry.get("descricao")
                        or entry.get("descrição")
                        or entry.get("title")
                        or entry.get("name")
                    )
                    if self._norm_key(label) in wanted:
                        return entry.get("value") or entry.get("valor") or entry.get("text") or entry.get("conteudo")
            if isinstance(container, dict):
                for k, v in container.items():
                    if self._norm_key(k) in wanted and v not in (None, ""):
                        return v
        return None

    def _extract_aberta_por_from_text(self, item):
        if not isinstance(item, dict):
            return None
        patterns = [
            r"\babert[ao]\s+por\s*:\s*([^\n\r<|;]+)",
            r"\bcriad[oa]\s+por\s*:\s*([^\n\r<|;]+)",
            r"\bautor\s*:\s*([^\n\r<|;]+)",
            r"\busu[aá]rio\s+de\s+abertura\s*:\s*([^\n\r<|;]+)",
        ]
        for raw in self._walk_strings(item):
            if not isinstance(raw, str):
                continue
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    actor = self._stringify_actor(m.group(1))
                    if actor:
                        return actor
        return None

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

    def _extract_created_record_id(self, value):
        if isinstance(value, dict):
            for key in ("id", "os_id", "ocorrencia_id", "chamado_id", "codigo", "protocolo", "numero"):
                got = value.get(key)
                if got not in (None, "", 0, "0"):
                    return str(got).strip()
            for inner in value.values():
                got = self._extract_created_record_id(inner)
                if got:
                    return got
            return None
        if isinstance(value, list):
            for inner in value:
                got = self._extract_created_record_id(inner)
                if got:
                    return got
        return None

    def _payload_has_business_error(self, value):
        bad_tokens = (
            "erro",
            "error",
            "invalid",
            "invalido",
            "inválido",
            "nao autorizado",
            "não autorizado",
            "forbidden",
            "denied",
            "negado",
            "falha",
            "failure",
            "exception",
        )
        if isinstance(value, dict):
            for key in ("ok", "success", "sucesso", "created"):
                if key in value and value.get(key) is False:
                    return True
            for key in ("error", "erro", "errors", "mensagem", "message", "msg", "detail", "details"):
                got = value.get(key)
                if self._payload_has_business_error(got):
                    return True
            for inner in value.values():
                if self._payload_has_business_error(inner):
                    return True
            return False
        if isinstance(value, list):
            return any(self._payload_has_business_error(inner) for inner in value)
        if isinstance(value, str):
            text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
            return any(tok in text for tok in bad_tokens)
        return False

    def _is_successful_creation_response(self, payload):
        created_id = self._extract_created_record_id(payload)
        if created_id:
            return True, created_id
        if self._payload_has_business_error(payload):
            return False, None
        return False, None

    def _get_auth_override_from_auth_payload(self, auth_payload):
        session = (auth_payload or {}).get("_sgp_session") if isinstance(auth_payload, dict) else None
        if not isinstance(session, dict):
            return None
        username = str(session.get("username") or "").strip()
        password = session.get("password")
        if not username or password in (None, ""):
            return None
        return (username, str(password))

    def _confirm_requerer_creation(self, contrato, created_id=None, auth_override=None, timeout=10, max_attempts=4, sleep_seconds=1.2):
        contrato = str(contrato or "").strip()
        created_id = str(created_id or "").strip()
        if not contrato and not created_id:
            return None, []

        attempts = []
        if contrato:
            ProxyHandler.ocorrencias_cache.pop(contrato, None)

        for idx in range(max(1, int(max_attempts))):
            if created_id:
                item, lookup_attempts = self._fetch_ocorrencia_por_id(
                    created_id,
                    nocache=True,
                    timeout=timeout,
                    max_total_seconds=max(timeout, 12),
                    auth_override=auth_override,
                )
                attempts.append({"kind": "by_id", "attempt": idx + 1, "lookup_attempts": lookup_attempts})
                if isinstance(item, dict):
                    return item, attempts

            if contrato:
                items, lookup_attempts = self._fetch_ocorrencias_contrato(
                    contrato,
                    nocache=True,
                    timeout=timeout,
                    max_total_seconds=max(timeout, 12),
                    auth_override=auth_override,
                )
                item = self._pick_ocorrencia_requerer(items)
                attempts.append({"kind": "by_contract", "attempt": idx + 1, "lookup_attempts": lookup_attempts})
                if isinstance(item, dict):
                    return item, attempts

            if idx + 1 < max_attempts:
                time.sleep(float(sleep_seconds))

        return None, attempts

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
        if AUTH_ENABLED:
            session = self._get_auth_session(payload.get("jti"))
            if not session:
                self._send_json(401, {"ok": False, "message": "Sessão SGP expirada. Faça login novamente."})
                return None
            payload = dict(payload)
            payload["_sgp_session"] = session
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

        if path_only.startswith("/api/requerer-info"):
            auth = self._require_auth() if self._is_protected_path(path_only) else None
            if self._is_protected_path(path_only) and not auth:
                return
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            contrato_id = (params.get("contrato", [""])[0] or "").strip()
            debug = params.get('debug', ['0'])[0] in ('1', 'true', 'yes')
            nocache = params.get('nocache', ['0'])[0] in ('1', 'true', 'yes')
            if not contrato_id:
                self._send_json(400, {"ok": False, "message": "Parâmetro obrigatório ausente: contrato"})
                return
            try:
                started = time.time()
                auth_override = self._get_auth_override_from_auth_payload(auth)
                items, attempts = self._fetch_ocorrencias_contrato(
                    contrato_id,
                    nocache=nocache,
                    timeout=10,
                    max_total_seconds=18,
                    auth_override=auth_override,
                )
                oc = self._pick_ocorrencia_requerer(items)
                responsavel = None
                oc_id = None
                if isinstance(oc, dict):
                    oc_id = oc.get("os_id") or oc.get("id") or oc.get("ocorrencia_id") or oc.get("chamado_id")
                    responsavel = self._extract_actor_from_item(oc)
                payload = {
                    "ok": True,
                    "contrato_id": str(contrato_id),
                    "found": bool(oc),
                    "responsavel": str(responsavel or "").strip() or None,
                    "ocorrencia_id": oc_id,
                }
                if debug:
                    def _summarize_oc(x):
                        if not isinstance(x, dict):
                            return {"type": type(x).__name__}
                        # tenta extrair o "Aberta Por" mesmo sem match
                        actor = self._extract_actor_from_item(x)

                        return {
                            "id": x.get("os_id") or x.get("id") or x.get("ocorrencia_id") or x.get("chamado_id"),
                            "tipo_id": x.get("ocorrenciatipo") or x.get("ocorrencia_tipo") or x.get("ocorrenciaTipo") or x.get("tipo") or x.get("tipo_id"),
                            "tipo_label": x.get("ocorrenciatipo_descricao") or x.get("ocorrenciatipo_label") or x.get("tipo_label") or x.get("tipo_descricao"),
                            "motivo_id": x.get("motivoos") or x.get("motivo_os") or x.get("motivo") or x.get("motivo_id"),
                            "conteudo": x.get("conteudo") or x.get("assunto") or x.get("titulo"),
                            "classificacao": x.get("classificacao") or x.get("classificação") or x.get("status") or x.get("situacao"),
                            "aberta_por": actor,
                            "keys_head": list(x.keys())[:24],
                        }

                    payload["debug"] = {
                        "attempts": attempts,
                        "found": bool(oc),
                        "want": {
                            "conteudo": REQUERER_CONTEUDO,
                            "ocorrenciatipo": REQUERER_OCORRENCIA_TIPO,
                            "motivoos": REQUERER_MOTIVO_OS,
                            "classificacao_value": REQUERER_CLASSIFICACAO_VALUE,
                            "lookup_endpoints": REQUERER_LOOKUP_ENDPOINTS,
                        },
                        "sample_count": len(items),
                        "sample_summary": [_summarize_oc(x) for x in (items[:5] if isinstance(items, list) else [])],
                        "seconds": round(time.time() - started, 2),
                    }
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(502, {"ok": False, "message": "Falha ao consultar ocorrências no SGP.", "details": {"message": f"{type(e).__name__}: {str(e)}"}})
            return

        if path_only.startswith("/api/ocorrencia-info"):
            if self._is_protected_path("/api/ocorrencia-info"):
                auth = self._require_auth()
                if not auth:
                    return
            else:
                auth = None
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            oc_id = (params.get("id", [""])[0] or params.get("oc", [""])[0] or params.get("os", [""])[0] or "").strip()
            debug = params.get('debug', ['0'])[0] in ('1', 'true', 'yes')
            nocache = params.get('nocache', ['0'])[0] in ('1', 'true', 'yes')
            if not oc_id:
                self._send_json(400, {"ok": False, "message": "Parâmetro obrigatório ausente: id"})
                return
            try:
                started = time.time()
                auth_override = self._get_auth_override_from_auth_payload(auth)
                item, attempts = self._fetch_ocorrencia_por_id(
                    oc_id,
                    nocache=nocache,
                    timeout=10,
                    max_total_seconds=18,
                    auth_override=auth_override,
                )
                if not item:
                    payload = {"ok": True, "id": str(oc_id), "found": False}
                    if debug:
                        payload["debug"] = {"attempts": attempts, "seconds": round(time.time() - started, 2)}
                    self._send_json(200, payload)
                    return

                actor = self._extract_actor_from_item(item)

                summary = {
                    "id": item.get("os_id") or item.get("id") or item.get("ocorrencia_id") or item.get("chamado_id") or str(oc_id),
                    "contrato": item.get("contrato") or item.get("contrato_id") or item.get("contratoId"),
                    "conteudo": item.get("conteudo") or item.get("assunto") or item.get("titulo"),
                    "tipo_id": item.get("ocorrenciatipo") or item.get("ocorrencia_tipo") or item.get("ocorrenciaTipo") or item.get("tipo") or item.get("tipo_id"),
                    "tipo_label": item.get("ocorrenciatipo_descricao") or item.get("ocorrenciatipo_label") or item.get("tipo_label") or item.get("tipo_descricao"),
                    "motivo_id": item.get("motivoos") or item.get("motivo_os") or item.get("motivo") or item.get("motivo_id"),
                    "classificacao": item.get("classificacao") or item.get("classificação") or item.get("status") or item.get("situacao"),
                    "aberta_por": actor,
                    "keys_head": list(item.keys())[:40],
                }
                payload = {"ok": True, "id": str(oc_id), "found": True, "summary": summary}
                if debug:
                    payload["debug"] = {"attempts": attempts, "seconds": round(time.time() - started, 2)}
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(502, {"ok": False, "message": "Falha ao consultar ocorrência no SGP.", "details": {"message": f"{type(e).__name__}: {str(e)}"}})
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
                jti = secrets.token_urlsafe(16)
                payload = {
                    "sub": str(info.get("usuario") or username),
                    "nome": str(info.get("nome") or ""),
                    "email": str(info.get("email") or ""),
                    "grupos": info.get("grupos") or [],
                    "isAdmin": True,
                    "iat": now,
                    "exp": now + int(AUTH_TOKEN_TTL_SECONDS),
                    "jti": jti,
                }
                self._store_auth_session(jti, username, password, payload["exp"], info)
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
            token = self._extract_bearer_token()
            payload = _verify_simple_jwt(token, AUTH_JWT_SECRET) if token else None
            if isinstance(payload, dict):
                self._pop_auth_session(payload.get("jti"))
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
                "setor": int(REQUERER_SETOR),
                "os_prioridade": int(REQUERER_PRIORIDADE),
            }
            if REQUERER_MOTIVO_OS is not None and str(REQUERER_MOTIVO_OS).strip() != "":
                payload["motivoos"] = int(REQUERER_MOTIVO_OS)
            if cliente_nome:
                payload["contato_nome"] = cliente_nome
            if telefone:
                payload["contato_telefone"] = telefone
            if requested_by:
                # No SGP, este campo costuma aceitar o usuário/nome do responsável.
                payload["responsavel"] = requested_by
            status_field = str(REQUERER_STATUS_FIELD or "").strip()
            status_value = str(REQUERER_STATUS_VALUE or "").strip()
            if status_field and status_value:
                payload[status_field] = status_value
            encerrar_field = str(REQUERER_ENCERRAR_OS_FIELD or "").strip()
            encerrar_value = str(REQUERER_ENCERRAR_OS_VALUE or "").strip()
            if encerrar_field and encerrar_value:
                payload[encerrar_field] = encerrar_value

            class_field = str(REQUERER_CLASSIFICACAO_FIELD or "").strip()
            class_value = str(REQUERER_CLASSIFICACAO_VALUE or "").strip()
            if class_field and class_value:
                payload[class_field] = class_value

            try:
                auth_override = self._get_auth_override_from_auth_payload(auth)
                self._log_requerer_diag("before_post", {
                    "contrato": contrato,
                    "requested_by": requested_by,
                    "sgp_auth_user": auth_override[0] if isinstance(auth_override, tuple) and len(auth_override) else (AUTH[0] if isinstance(AUTH, tuple) and len(AUTH) else None),
                    "path": "/ura/chamado/",
                    "payload": payload,
                })
                resp, _mode = self._post_ura("/ura/chamado/", payload, timeout=40, auth_override=auth_override)
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
                self._log_requerer_diag("after_post", {
                    "contrato": contrato,
                    "path": "/ura/chamado/",
                    "http_status": resp.status_code,
                    "content_type": content_type,
                    "response": out,
                })
                if resp.status_code != 200:
                    self._send_json(resp.status_code, {"ok": False, "message": "Falha ao criar ocorrência no SGP.", "sgp": out})
                    return
                created_ok, created_id = self._is_successful_creation_response(out)
                confirmed_item = None
                confirm_attempts = []
                if not created_ok:
                    confirmed_item, confirm_attempts = self._confirm_requerer_creation(
                        contrato,
                        created_id=created_id,
                        auth_override=auth_override,
                        timeout=4,
                        max_attempts=2,
                        sleep_seconds=0.8,
                    )
                if not confirmed_item and not created_ok:
                    self._log_requerer_diag("confirm_failed", {
                        "contrato": contrato,
                        "created_ok": created_ok,
                        "created_id": created_id,
                        "confirm_attempts": confirm_attempts,
                        "response": out,
                    })
                    self._send_json(502, {
                        "ok": False,
                        "message": "SGP respondeu sem confirmar a criação da ocorrência.",
                        "sgp": out,
                        "confirm_attempts": confirm_attempts,
                        "sgp_auth_user": AUTH[0] if isinstance(AUTH, tuple) and len(AUTH) else None,
                    })
                    return
                confirmed_id = None
                confirmed_actor = None
                if isinstance(confirmed_item, dict):
                    confirmed_id = (
                        confirmed_item.get("os_id")
                        or confirmed_item.get("id")
                        or confirmed_item.get("ocorrencia_id")
                        or confirmed_item.get("chamado_id")
                    )
                    confirmed_actor = self._extract_actor_from_item(confirmed_item)
                self._log_requerer_diag("confirm_success", {
                    "contrato": contrato,
                    "created_ok": created_ok,
                    "created_id": created_id,
                    "confirmed": bool(confirmed_item),
                    "confirmed_id": confirmed_id,
                    "confirmed_actor": confirmed_actor,
                    "confirm_attempts": confirm_attempts,
                })
                self._send_json(200, {
                    "ok": True,
                    "message": "Ocorrência criada.",
                    "ocorrencia_id": confirmed_id or created_id,
                    "responsavel": confirmed_actor or requested_by or None,
                    "confirmed": bool(confirmed_item),
                    "confirm_attempts": confirm_attempts,
                    "sgp": out,
                    "sgp_auth_user": AUTH[0] if isinstance(AUTH, tuple) and len(AUTH) else None,
                })
            except Exception as e:
                self._log_requerer_diag("exception", {
                    "contrato": contrato,
                    "requested_by": requested_by,
                    "error_type": type(e).__name__,
                    "error": str(e),
                })
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
