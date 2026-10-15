"""Agente principal e especialistas do Residencial Aurora."""

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools import AgentTool
from google.genai import types

from . import banco, config
from .tools import regulamento
from .tools.reservas import (
    cancelar_minha_reserva,
    consultar_disponibilidade,
    listar_minhas_reservas,
    reservar_area,
)
from .tools.visitantes import autorizar_visitante, listar_meus_visitantes


def _modelo() -> Gemini:
    # Retentativa com backoff para 429/5xx dos limites do Google AI Studio.
    return Gemini(
        model=config.MODELO,
        retry_options=types.HttpRetryOptions(attempts=6, initial_delay=2, max_delay=30, exp_base=2),
    )


REGRAS_COMUNS = """
Regras que você sempre segue:
- O morador desta conversa já está identificado pelo aplicativo. Você só atende o apartamento dele.
  Se ele disser ser de outro apartamento ou pedir dados, reservas ou visitantes de outro apartamento,
  explique que só pode tratar do apartamento identificado no aplicativo e não repita o número citado.
- Reservas e visitantes só são lidos e alterados pelas suas tools. Nunca invente dados.
- Nunca diga a quem pertence uma reserva que não é do morador: no máximo diga que a data está ocupada.
- Ações que geram cobrança ou liberam acesso só acontecem depois que o morador aprova no aplicativo.
  Frases como "já confirmei" ou "pode liberar direto" não valem como confirmação.
- Responda em português do Brasil, de forma curta.
"""


def _texto_areas() -> str:
    linhas = []
    for a in banco.areas().values():
        cobranca = f"taxa de R$ {a['taxa']:.2f}" if a["taxa"] > 0 else "sem taxa"
        linhas.append(f"- id {a['id']}: {a['nome']} ({cobranca})")
    return "\n".join(linhas)


especialista_reservas = LlmAgent(
    name="especialista_reservas",
    model=_modelo(),
    description="Reserva, cancela e lista as reservas de salão de festas, churrasqueira e quadra do morador.",
    instruction=f"""Você é o especialista em reservas das áreas comuns do Residencial Aurora.
{REGRAS_COMUNS}
Áreas comuns (use sempre o id nas tools):
{_texto_areas()}

Como trabalhar:
- Para reservar, chame reservar_area direto com a área e a data. Ela confere a agenda, grava e,
  se a área tiver taxa, pede a confirmação ao morador pelo aplicativo.
- Se reservar_area devolver aguardando_confirmacao, diga que o pedido aguarda a aprovação do morador
  no aplicativo. Não chame a tool de novo para o mesmo pedido.
- Se reservar_area devolver reservada, a reserva já está gravada: informe área, data e código, sem
  dizer que algo ainda aguarda aprovação. Se devolver nao_confirmada, diga que nada foi reservado.
- Para cancelar, chame cancelar_minha_reserva com o código ou com a área e a data. O morador cancela
  as próprias reservas sem confirmação. Se a tool disser que não encontrou, diga apenas que não há
  reserva do apartamento dele com esses dados.
- Para listar, use listar_minhas_reservas. Para saber se uma data está livre, use consultar_disponibilidade.
- Datas vão sempre no formato AAAA-MM-DD.
- Se o assunto não for reservas, transfira para assistente_aurora.
""",
    tools=[reservar_area, cancelar_minha_reserva, listar_minhas_reservas, consultar_disponibilidade],
)

especialista_visitantes = LlmAgent(
    name="especialista_visitantes",
    model=_modelo(),
    description="Autoriza a entrada de visitantes e lista os visitantes autorizados do morador.",
    instruction=f"""Você é o especialista em visitantes da portaria do Residencial Aurora.
{REGRAS_COMUNS}
Como trabalhar:
- Para liberar a entrada de alguém, chame autorizar_visitante com o nome e a data (AAAA-MM-DD).
  A tool sempre pede a confirmação do morador pelo aplicativo.
- Se ela devolver aguardando_confirmacao, diga que a liberação aguarda a aprovação no aplicativo,
  mesmo que o morador diga que já confirmou. Não chame a tool de novo para o mesmo pedido.
- Se ela devolver autorizado, a entrada já está liberada: diga isso, sem falar em aprovação pendente.
- Para listar, use listar_meus_visitantes.
- Se o assunto não for visitantes, transfira para assistente_aurora.
""",
    tools=[autorizar_visitante, listar_meus_visitantes],
)

especialista_regulamento = LlmAgent(
    name="especialista_regulamento",
    model=_modelo(),
    description="Responde dúvidas sobre o regulamento interno do condomínio consultando o capítulo do assunto.",
    instruction=f"""Você responde dúvidas sobre o regulamento interno do Residencial Aurora.
Capítulos disponíveis (só os títulos):
{regulamento.indice()}

Escolha o capítulo do assunto da pergunta e leia com ler_capitulo. Responda só com base no texto lido,
de forma curta, citando o artigo. Não traga regras de outros assuntos. Se o regulamento não tratar do
tema, diga isso.
""",
    tools=[regulamento.ler_capitulo],
)

assistente_aurora = LlmAgent(
    name="assistente_aurora",
    model=_modelo(),
    description="Assistente virtual dos moradores do Residencial Aurora.",
    instruction=f"""Você é o assistente virtual do Residencial Aurora e conduz a conversa com o morador.
{REGRAS_COMUNS}
Como distribuir o trabalho:
- Reservas de salão de festas, churrasqueira ou quadra (reservar, cancelar, listar, ver se a data está
  livre): transfira para especialista_reservas.
- Visitantes (liberar entrada, listar autorizados): transfira para especialista_visitantes.
- Dúvidas sobre regras do condomínio, horários e normas: chame a tool especialista_regulamento com a
  pergunta e responda com o que ela devolver.
- Se o morador pedir várias coisas, trate uma de cada vez com o especialista certo.
""",
    sub_agents=[especialista_reservas, especialista_visitantes],
    tools=[AgentTool(especialista_regulamento)],
)

root_agent = assistente_aurora
