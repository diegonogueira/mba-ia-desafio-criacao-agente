"""API do assistente do Residencial Aurora (contrato do enunciado)."""

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from pydantic import BaseModel

from . import banco, config
from .agentes import root_agent
from .tools.sessao import CHAVE_APARTAMENTO

logger = logging.getLogger("aurora.api")

# Nome da function call que o ADK emite quando uma tool pede confirmação.
CONFIRMACAO = "adk_request_confirmation"


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


config.ESTADO.mkdir(exist_ok=True)
banco.garantir_banco()

session_service = SqliteSessionService(str(config.BANCO_SESSOES))
# is_resumable=True faz o Runner entregar a resposta da confirmação ao agente
# que a pediu (o especialista), e não ao agente principal.
aurora_app = App(
    name=config.APP_NAME,
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
runner = Runner(app=aurora_app, session_service=session_service)

# Uma execução por sessão por vez: a checagem de pendência e o envio da
# resposta acontecem sem outra execução da mesma sessão no meio.
_travas: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await runner.close()


api = FastAPI(title="Residencial Aurora", lifespan=lifespan)


async def _sessao(session_id: str) -> Session:
    sessao = await session_service.get_session(
        app_name=config.APP_NAME, user_id=config.USER_ID, session_id=session_id
    )
    if sessao is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return sessao


def confirmacoes_pendentes(sessao: Session) -> list[dict]:
    """Pedidos de confirmação da sessão que ainda não receberam resposta.

    A fonte é o próprio histórico de eventos: cada function call
    ``adk_request_confirmation`` sem function response com o mesmo id.
    """
    respondidas = {
        fr.id for evento in sessao.events for fr in evento.get_function_responses() if fr.id
    }
    pendentes = []
    for evento in sessao.events:
        for fc in evento.get_function_calls():
            if fc.name != CONFIRMACAO or fc.id in respondidas:
                continue
            args = fc.args or {}
            original = args.get("originalFunctionCall") or {}
            pedido = args.get("toolConfirmation") or {}
            pendentes.append(
                {
                    "id": fc.id,
                    "acao": original.get("name", ""),
                    "detalhes": pedido.get("payload") or original.get("args") or {},
                }
            )
    return pendentes


async def _executar(session_id: str, conteudo: types.Content) -> dict:
    textos = []
    try:
        async for evento in runner.run_async(
            user_id=config.USER_ID, session_id=session_id, new_message=conteudo
        ):
            if evento.author == "user" or evento.partial or not evento.content:
                continue
            for parte in evento.content.parts or []:
                if parte.text and not parte.thought:
                    textos.append(parte.text.strip())
    except Exception:
        # Falha do modelo (ex.: 429 de cota do Google AI Studio). O que as
        # tools já gravaram continua gravado; a resposta deixa isso explícito.
        logger.exception("Falha ao executar o assistente na sessão %s", session_id)
        raise HTTPException(
            status_code=503,
            detail="O assistente está indisponível no momento (falha ao chamar o modelo). Tente de novo.",
        )
    sessao = await _sessao(session_id)
    return {
        "resposta": "\n\n".join(t for t in textos if t),
        "confirmacoes_pendentes": confirmacoes_pendentes(sessao),
    }


@api.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao):
    if corpo.apartamento not in banco.apartamentos():
        raise HTTPException(status_code=422, detail="Apartamento inexistente.")
    # Garantia 2: o apartamento entra no state uma única vez, aqui.
    sessao = await session_service.create_session(
        app_name=config.APP_NAME,
        user_id=config.USER_ID,
        state={CHAVE_APARTAMENTO: corpo.apartamento},
    )
    return {"session_id": sessao.id}


@api.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: Mensagem):
    async with _travas[session_id]:
        await _sessao(session_id)
        conteudo = types.Content(role="user", parts=[types.Part(text=corpo.texto)])
        return await _executar(session_id, conteudo)


@api.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao):
    async with _travas[session_id]:
        sessao = await _sessao(session_id)
        # Garantia 1: só um id pendente nesta sessão é aceito. Já respondido,
        # de outra sessão ou inventado: 409 e nada executa.
        if corpo.id not in {p["id"] for p in confirmacoes_pendentes(sessao)}:
            return JSONResponse(
                status_code=409,
                content={"detail": "Não existe confirmação pendente com esse id nesta sessão."},
            )
        resposta = types.FunctionResponse(
            id=corpo.id, name=CONFIRMACAO, response={"confirmed": corpo.confirmado}
        )
        conteudo = types.Content(role="user", parts=[types.Part(function_response=resposta)])
        return await _executar(session_id, conteudo)


@api.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str):
    sessao = await _sessao(session_id)
    return [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in sessao.events]


@api.get("/apartamentos/{numero}/reservas")
async def reservas_do_apartamento(numero: str):
    return banco.reservas_do_apartamento(numero)


@api.get("/apartamentos/{numero}/visitantes")
async def visitantes_do_apartamento(numero: str):
    return banco.visitantes_do_apartamento(numero)


def main() -> None:
    uvicorn.run(api, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
