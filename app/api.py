"""API HTTP. Regras: loja e usuário vêm sempre da sessão; toda rota exige sessão,
exceto /saude e POST /sessao; o banco é a última barreira (papel stp_app)."""
import datetime as dt
import hashlib
import logging
import os
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from app.config import Config
from app.erros import http_de_banco
from app import importacao

log = logging.getLogger("stp")
PH = PasswordHasher()
HASH_FALSO = PH.hash("senha-inexistente-para-tempo-constante")
COOKIE = "stp_sessao"
JPEG = b"\xff\xd8\xff"
PNG = b"\x89PNG\r\n\x1a\n"
PAPEIS_UPLOAD = {"repositor", "gerente", "admin"}
PAPEIS_IMPORTACAO = {"analista", "admin"}


@dataclass(frozen=True)
class Usuario:
    id: int
    loja_id: int
    papel: str


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    login: str = Field(min_length=3, max_length=40)
    senha: str = Field(min_length=1, max_length=200)


class Transicao(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    versao: int = Field(ge=1, le=1_000_000)
    para: Literal["em_verificacao", "confirmado", "rejeitado", "resolvido"]
    causa: Literal["reposicao_pendente", "estoque_fantasma", "fora_de_posicao", "planograma_desatualizado", "outra"] | None = None
    motivo: Literal["produto_presente", "foto_inadequada", "planograma_errado", "outro"] | None = None
    comentario: str | None = Field(default=None, max_length=2000)


def criar_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    cfg.preparar_diretorios()
    pool = ConnectionPool(cfg.dsn_app, min_size=1, max_size=10, timeout=5, open=False,
                          kwargs={"options": "-c statement_timeout=10000 -c idle_in_transaction_session_timeout=15000"})
    @asynccontextmanager
    async def ciclo(_app):
        pool.open(wait=True)
        yield
        pool.close()

    app = FastAPI(title="Shelf-to-Profit", docs_url=None, redoc_url=None, openapi_url="/openapi.json", lifespan=ciclo)
    app.state.cfg, app.state.pool = cfg, pool

    @app.middleware("http")
    async def cabecalhos_e_origem(request: Request, call_next):
        # CSRF: métodos que alteram estado só aceitam Origin igual à configurada (quando enviado).
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origem = request.headers.get("origin")
            if origem is not None and origem != cfg.origem_permitida:
                return JSONResponse({"erro": "origem não permitida"}, status_code=403)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ sessão
    def usuario_atual(request: Request) -> Usuario:
        token = request.cookies.get(COOKIE)
        if not token or len(token) > 100:
            raise HTTPException(401, {"erro": "sessão necessária"})
        h = hashlib.sha256(token.encode()).hexdigest()
        try:
            with pool.connection() as c:
                r = c.execute("""SELECT u.id, u.loja_id, u.papel FROM op.sessao s JOIN op.usuario u ON u.id = s.usuario_id
                                  WHERE s.token_sha256 = %s AND s.expira_em > now() AND u.ativo""", (h,)).fetchone()
        except psycopg.Error as e:
            raise http_de_banco(e)
        if not r:
            raise HTTPException(401, {"erro": "sessão inválida ou expirada"})
        return Usuario(*r)

    def exige(*papeis):
        def dep(u: Usuario = Depends(usuario_atual)) -> Usuario:
            if u.papel not in papeis:
                raise HTTPException(403, {"erro": "papel sem permissão"})
            return u
        return dep

    @app.get("/saude")
    def saude():
        try:
            with pool.connection() as c:
                c.execute("SELECT 1")
        except Exception:
            return JSONResponse({"banco": "indisponivel"}, status_code=503)
        return {"banco": "ok"}

    @app.post("/sessao")
    def entrar(dados: Login, response: Response):
        try:
            with pool.connection() as c:
                r = c.execute("""SELECT u.id, cr.senha_hash FROM op.usuario u JOIN op.credencial cr ON cr.usuario_id = u.id
                                  WHERE u.login = %s AND u.ativo""", (dados.login,)).fetchone()
                ok = False
                try:
                    ok = PH.verify(r[1] if r else HASH_FALSO, dados.senha) and r is not None
                except (VerificationError, InvalidHashError):
                    ok = False
                if not ok:
                    raise HTTPException(401, {"erro": "login ou senha inválidos"})
                token = secrets.token_urlsafe(32)
                c.execute("INSERT INTO op.sessao (token_sha256, usuario_id, expira_em) VALUES (%s, %s, now() + %s * interval '1 hour')",
                          (hashlib.sha256(token.encode()).hexdigest(), r[0], cfg.sessao_horas))
        except psycopg.Error as e:
            raise http_de_banco(e)
        response.set_cookie(COOKIE, token, httponly=True, secure=cfg.cookie_seguro, samesite="strict",
                            max_age=cfg.sessao_horas * 3600, path="/")
        return {"ok": True}

    @app.delete("/sessao")
    def sair(request: Request, response: Response, u: Usuario = Depends(usuario_atual)):
        h = hashlib.sha256(request.cookies[COOKIE].encode()).hexdigest()
        with pool.connection() as c:
            c.execute("DELETE FROM op.sessao WHERE token_sha256 = %s", (h,))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # ------------------------------------------------------------ upload de foto
    @app.post("/capturas", status_code=201)
    async def enviar_foto(request: Request, response: Response,
                          capturado_em: dt.datetime = Query(...),
                          u: Usuario = Depends(exige(*PAPEIS_UPLOAD))):
        if capturado_em.tzinfo is None:
            raise HTTPException(422, {"erro": "capturado_em precisa de fuso horário"})
        agora = dt.datetime.now(dt.timezone.utc)
        if not (agora - dt.timedelta(days=2) <= capturado_em <= agora + dt.timedelta(minutes=5)):
            raise HTTPException(422, {"erro": "capturado_em fora do intervalo aceito"})
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > cfg.max_upload_bytes):
            raise HTTPException(413, {"erro": "arquivo grande demais"})
        if shutil.disk_usage(cfg.dir_dados).free < cfg.reserva_disco_bytes:
            raise HTTPException(507, {"erro": "armazenamento insuficiente"})

        tmp = cfg.tmp / f"{uuid.uuid4()}.part"
        h, total, inicio = hashlib.sha256(), 0, b""
        try:
            with open(tmp, "xb") as f:
                async for bloco in request.stream():
                    total += len(bloco)
                    if total > cfg.max_upload_bytes:
                        raise HTTPException(413, {"erro": "arquivo grande demais"})
                    if len(inicio) < 8:
                        inicio += bloco[:8]
                        if len(inicio) >= 8 and not (inicio.startswith(JPEG) or inicio.startswith(PNG)):
                            raise HTTPException(415, {"erro": "somente JPEG ou PNG"})
                    h.update(bloco)
                    f.write(bloco)
                f.flush(); os.fsync(f.fileno())
            if total == 0 or not (inicio.startswith(JPEG) or inicio.startswith(PNG)):
                raise HTTPException(415 if total else 400, {"erro": "somente JPEG ou PNG" if total else "arquivo vazio"})
            chave = uuid.uuid4()
            destino = cfg.quarentena / str(chave)
            os.chmod(tmp, 0o600)
            os.rename(tmp, destino)                   # arquivo primeiro, banco depois
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

        sha = h.hexdigest()
        try:
            with pool.connection() as c:
                r = c.execute("""INSERT INTO op.captura (loja_id, sha256_original, quarentena_chave, capturado_em)
                                 VALUES (%s, %s, %s, %s) ON CONFLICT (loja_id, sha256_original) DO NOTHING RETURNING id""",
                              (u.loja_id, sha, chave, capturado_em)).fetchone()
                if r is None:                         # mesma foto já enviada: idempotente
                    destino.unlink(missing_ok=True)
                    existente = c.execute("SELECT id, estado FROM op.captura WHERE loja_id = %s AND sha256_original = %s",
                                          (u.loja_id, sha)).fetchone()
                    response.status_code = 200
                    return {"id": existente[0], "estado": existente[1], "duplicada": True}
        except psycopg.Error as e:
            destino.unlink(missing_ok=True)           # se falhar, a reconciliação também remove
            raise http_de_banco(e)
        return {"id": r[0], "estado": "recebida", "duplicada": False}

    @app.get("/capturas/{captura_id}")
    def ver_captura(captura_id: int, u: Usuario = Depends(usuario_atual)):
        with pool.connection() as c:
            r = c.execute("""SELECT id, estado, motivo_recusa, capturado_em FROM op.captura
                              WHERE id = %s AND loja_id = %s""", (captura_id, u.loja_id)).fetchone()
        if not r:
            raise HTTPException(404, {"erro": "não encontrado"})
        return {"id": r[0], "estado": r[1], "motivo_recusa": r[2], "capturado_em": r[3].isoformat()}

    # ------------------------------------------------------------ importação de estoque
    @app.post("/importacoes/estoque", status_code=201)
    async def importar_estoque(request: Request, u: Usuario = Depends(exige(*PAPEIS_IMPORTACAO))):
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > cfg.max_csv_bytes):
            raise HTTPException(413, {"erro": "arquivo grande demais"})
        corpo = bytearray()
        async for bloco in request.stream():
            corpo += bloco
            if len(corpo) > cfg.max_csv_bytes:
                raise HTTPException(413, {"erro": "arquivo grande demais"})
        try:
            with pool.connection() as c:
                res = importacao.importar_estoque(c, u.loja_id, u.id, bytes(corpo), cfg.max_csv_linhas)
        except psycopg.Error as e:
            raise http_de_banco(e)
        if res["status"] == "rejeitada":
            return JSONResponse(res, status_code=422)
        if res["status"] == "duplicada":
            return JSONResponse(res, status_code=409)
        return res

    @app.get("/importacoes/tentativas/{tid}/relatorio.csv")
    def relatorio_csv(tid: int, u: Usuario = Depends(exige(*PAPEIS_IMPORTACAO))):
        with pool.connection() as c:
            r = c.execute("SELECT relatorio FROM op.importacao_tentativa WHERE id = %s AND loja_id = %s",
                          (tid, u.loja_id)).fetchone()
        if not r:
            raise HTTPException(404, {"erro": "não encontrado"})
        return PlainTextResponse(importacao.relatorio_para_csv(r[0]), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": 'attachment; filename="relatorio.csv"'})

    # ------------------------------------------------------------ alertas
    @app.get("/alertas")
    def listar_alertas(status: Literal["aberto", "em_verificacao", "confirmado", "rejeitado", "resolvido"] | None = None,
                       limite: int = Query(100, ge=1, le=500), u: Usuario = Depends(usuario_atual)):
        with pool.connection() as c, c.cursor(row_factory=dict_row) as cur:
            # row_factory no cursor, nunca na conexão: a conexão volta ao pool e seria reutilizada
            rows = cur.execute("""SELECT id, posicao_id, produto_id, tipo, status, versao, criado_em
                                  FROM op.alerta WHERE loja_id = %s AND (%s::text IS NULL OR status = %s)
                                 ORDER BY criado_em DESC LIMIT %s""", (u.loja_id, status, status, limite)).fetchall()
        return [{**r, "criado_em": r["criado_em"].isoformat()} for r in rows]

    @app.post("/alertas/{alerta_id}/transicoes")
    def transicionar(alerta_id: int, t: Transicao, u: Usuario = Depends(usuario_atual)):
        try:
            with pool.connection() as c:
                v = c.execute("SELECT op.registrar_transicao(%s, %s, %s, %s, %s, %s, %s)",
                              (alerta_id, t.versao, t.para, u.id, t.causa, t.motivo, t.comentario)).fetchone()[0]
        except psycopg.Error as e:
            raise http_de_banco(e)
        return {"versao": v}

    return app
