# Residencial Aurora: assistente virtual com Google ADK

Assistente do aplicativo dos moradores do Residencial Aurora. Pelo chat, o morador reserva salão de festas, churrasqueira e quadra, cancela as próprias reservas, autoriza visitantes e tira dúvidas sobre o regulamento. O assistente é construído com Google ADK (`google-adk==2.11.0`) e exposto por uma API FastAPI em `http://localhost:8000`.

A filosofia do projeto: **o modelo decide o caminho, o código decide o que é permitido**. As cinco garantias moram em tools, no esquema do banco e na API, e não no prompt.

```
aurora/
  api.py            rotas do contrato, Runner/App, sessões persistidas, confirmações
  agentes.py        agente principal e especialistas
  banco.py          SQLite do condomínio (reservas, visitantes, códigos emitidos)
  restaurar.py      comando de restauração dos dados iniciais
  config.py         caminhos, nome do app, modelo
  tools/
    sessao.py       apartamento da sessão (única fonte do apartamento nas tools)
    reservas.py     tools de reservas
    visitantes.py   tools de visitantes
    regulamento.py  leitura do regulamento por capítulo
dados/              estado inicial do condomínio (não alterado)
estado/             gerado em tempo de execução: condominio.db e sessoes.db (fora do Git)
```

## Arquitetura

```
morador ──HTTP──▶ API (aurora/api.py) ──▶ Runner + App(resumable) ──▶ assistente_aurora (principal)
                                                                      ├─ transfer ─▶ especialista_reservas
                                                                      ├─ transfer ─▶ especialista_visitantes
                                                                      └─ AgentTool ─▶ especialista_regulamento
```

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `assistente_aurora` | Conduz a conversa e distribui o trabalho. Não tem tools de dados e não recebe o regulamento nas instruções. | Raiz do `App`, recebe toda mensagem nova. | Um ponto de entrada só, com instrução curta, que só encaminha. |
| `especialista_reservas` | Reservar, cancelar, listar reservas do morador e consultar se uma data está livre (`reservar_area`, `cancelar_minha_reserva`, `listar_minhas_reservas`, `consultar_disponibilidade`). | Sub-agente por **transferência** (`sub_agents`). | A confirmação de cobrança precisa voltar para o agente que a pediu, na sessão persistida. Como sub-agente, o pedido de confirmação e a retomada ficam no histórico da sessão do morador. Com `ResumabilityConfig(is_resumable=True)`, o Runner entrega a resposta a esse agente. |
| `especialista_visitantes` | Autorizar entrada de visitantes e listar os autorizados (`autorizar_visitante`, `listar_meus_visitantes`). | Sub-agente por **transferência**. | Mesmo motivo: liberar acesso exige confirmação, e a retomada tem que chegar a ele. |
| `especialista_regulamento` | Responder dúvidas sobre o regulamento lendo só o capítulo do assunto (`ler_capitulo`). | **`AgentTool`** nas tools do principal. | O `AgentTool` roda o especialista num Runner próprio com `InMemorySessionService`. Os eventos dele (instrução, leitura do capítulo) não entram na sessão do morador: ao principal volta só a resposta final. É o que mantém o regulamento fora do histórico (Garantia 4). |

Os especialistas de reservas e visitantes transferem de volta para `assistente_aurora` quando o assunto muda. Todos os agentes usam o modelo de `AURORA_MODELO` (padrão `gemini-3.5-flash-lite`), com retentativa e backoff para 429/5xx (`_modelo` em `aurora/agentes.py`).

Reservas e visitantes são sempre lidos e gravados por tools que chamam `aurora/banco.py`. O modelo nunca responde com dados que lembra ou inventa, e as rotas de verificação leem o mesmo banco direto, sem passar pelo modelo.

**Armazenamento.** Os dados do condomínio ficam em SQLite (`estado/condominio.db`, módulo `sqlite3`). As sessões do ADK ficam em `estado/sessoes.db`, com o `SqliteSessionService` do próprio ADK. Não há serviço externo para subir. Na primeira subida da API, se `condominio.db` não existir, ele é criado com os dados de `dados/` (`garantir_banco`).

**Decisões de comportamento livre:**
- A restauração volta reservas e visitantes ao estado de `dados/`, mas **não apaga as sessões** nem a tabela `codigos_emitidos`. Por isso um código já emitido continua sem poder se repetir.
- Criar sessão com apartamento fora de `dados/apartamentos.json` responde 422.
- Se o modelo falhar (por exemplo, cota do Google AI Studio esgotada), as rotas de conversa respondem 503 com uma mensagem. O que uma tool já gravou continua gravado.
- Uma nova mensagem com confirmação pendente não executa nada. A pendência continua listada e pode ser respondida pela rota de confirmações.

## Garantias

### Garantia 1: cobrança ou acesso só com confirmação

**Onde:** `aurora/tools/reservas.py` (`reservar_area`), `aurora/tools/visitantes.py` (`autorizar_visitante`), `aurora/api.py` (`confirmacoes_pendentes` e `responder_confirmacao`).

A própria tool decide se pede confirmação, a partir da taxa em `dados/areas.json`:

```python
# aurora/tools/reservas.py — reservar_area
if taxa > 0:
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        ...
        tool_context.request_confirmation(hint=..., payload={"area": area, "data": data, "taxa": taxa})
        return {"status": "aguardando_confirmacao", ...}
    if not confirmacao.confirmed:
        return {"status": "nao_confirmada", ...}
```

`autorizar_visitante` faz o mesmo sempre, com `payload={"nome": nome, "data": data}`. A quadra (taxa 0) é gravada direto, e o cancelamento (`cancelar_minha_reserva`) não pede confirmação.

O `request_confirmation` faz o ADK gravar na sessão uma function call `adk_request_confirmation`, e a execução para ali. A API lista como pendente toda `adk_request_confirmation` que ainda não tem function response com o mesmo id (`confirmacoes_pendentes`). `acao` é o nome da tool original e `detalhes` é o `payload`. A rota de confirmações:

```python
# aurora/api.py — responder_confirmacao
if corpo.id not in {p["id"] for p in confirmacoes_pendentes(sessao)}:
    return JSONResponse(status_code=409, ...)
resposta = types.FunctionResponse(id=corpo.id, name=CONFIRMACAO, response={"confirmed": corpo.confirmado})
```

A resposta vai ao Runner como function response. Com `ResumabilityConfig(is_resumable=True)` (definido em `aurora/api.py`), o Runner a entrega ao especialista que pediu a confirmação, e o ADK reexecuta a tool com `tool_context.tool_confirmation` preenchido.

**Por que não depende do modelo:** o modelo não tem como produzir uma function response `adk_request_confirmation`. Ela só nasce na rota de confirmações. Uma frase como "já estou confirmando aqui" é só texto, e a tool continua sem `tool_confirmation`. Depois de respondida, a confirmação deixa de ser pendente, então reenviar o mesmo id (ou mandar um id inventado, ou de outra sessão) recebe 409 e nada executa. Há ainda uma defesa no banco: `reservas.confirmacao_id` e `visitantes.confirmacao_id` são `UNIQUE` (`ESQUEMA` em `aurora/banco.py`), então uma confirmação aprovada grava no máximo uma vez. A checagem e o envio rodam sob um `asyncio.Lock` por sessão (`_travas`).

### Garantia 2: cada sessão pertence a um apartamento

**Onde:** `aurora/api.py` (`criar_sessao`), `aurora/tools/sessao.py` (`apartamento_da_sessao`), as tools em `aurora/tools/reservas.py` e `aurora/tools/visitantes.py`, as consultas em `aurora/banco.py`.

O apartamento entra no `state` da sessão uma única vez, na criação:

```python
# aurora/api.py — criar_sessao
sessao = await session_service.create_session(..., state={CHAVE_APARTAMENTO: corpo.apartamento})
```

```python
# aurora/tools/sessao.py
def apartamento_da_sessao(tool_context: ToolContext) -> str:
    apartamento = tool_context.state.get(CHAVE_APARTAMENTO)
    if apartamento not in banco.apartamentos():
        raise PermissionError("Sessão sem apartamento válido.")
    return apartamento
```

**Por que não depende do modelo:** nenhuma tool tem parâmetro de apartamento, e nenhuma tool ou `output_key` escreve a chave `apartamento` no state. Todas as leituras e gravações passam o apartamento da sessão para `banco.py`, que filtra por ele (`reservas_do_apartamento`, `visitantes_do_apartamento`, `cancelar_reserva ... WHERE apartamento = ?`). Pedir para cancelar a reserva de outro apartamento devolve `nao_encontrada`, sem revelar nada. A agenda de outros apartamentos só é consultada para dizer se a data está livre: `consultar_disponibilidade` devolve `disponivel: true/false`, e `reservar_area` devolve só "A área já está reservada nesta data". Nenhum código ou número de apartamento de terceiros chega à conversa.

### Garantia 3: nada se perde no reinício

**Onde:** `aurora/api.py` (`session_service = SqliteSessionService(str(config.BANCO_SESSOES))`), `aurora/banco.py` (`conectar`, em `estado/condominio.db`).

As sessões e todos os seus eventos ficam em `estado/sessoes.db`, e reservas e visitantes em `estado/condominio.db`. Reiniciar a API não toca nesses arquivos. A sessão continua com os mesmos eventos e aceita novas mensagens, inclusive a resposta a uma confirmação que ficou pendente antes do reinício. As pendências são calculadas a partir dos eventos persistidos, não de memória do processo.

**Por que não depende do modelo:** persistência é do serviço de sessão e do banco, gravada a cada evento ou transação.

### Garantia 4: o regulamento é consultado, não carregado

**Onde:** `aurora/tools/regulamento.py` (`capitulos`, `indice`, `ler_capitulo`) e `aurora/agentes.py` (`especialista_regulamento` e `tools=[AgentTool(especialista_regulamento)]` em `assistente_aurora`).

- A instrução do `assistente_aurora` não tem nenhum texto do regulamento.
- O `especialista_regulamento` recebe na instrução só os títulos dos capítulos (`indice()`). Com `ler_capitulo(numero)`, ele lê um único capítulo, recortado de `dados/regulamento.md` pelos cabeçalhos `## Capítulo`.
- Ele é acionado como `AgentTool`, que roda em sessão própria em memória (`InMemorySessionService` dentro do ADK). Na sessão do morador fica só a function call `especialista_regulamento` e a resposta final, por exemplo "Aos domingos, a piscina funciona das 9h às 20h (Art. 22, II)".

**Por que não depende do modelo:** o modelo não tem nenhuma tool que devolva o arquivo inteiro, e a leitura do capítulo acontece fora da sessão persistida. O histórico do morador não carrega capítulos de outros assuntos, e as próximas chamadas ao modelo não pagam por eles.

### Garantia 5: dois moradores, uma reserva

**Onde:** `aurora/banco.py` (`ESQUEMA`: índice `reserva_ativa_unica`; função `criar_reserva`) e `aurora/tools/reservas.py` (`reservar_area`).

```sql
CREATE UNIQUE INDEX IF NOT EXISTS reserva_ativa_unica
    ON reservas (area, data) WHERE status = 'ativa';
```

```python
# aurora/banco.py — criar_reserva
try:
    with transacao() as con:          # BEGIN IMMEDIATE ... COMMIT
        ...
        con.execute("INSERT INTO reservas (...) VALUES (...)", ...)
except sqlite3.IntegrityError:
    return {"status": "ocupada"}
```

A conferência de agenda antes de pedir a confirmação serve só para não pedir cobrança de uma data já ocupada. Quem garante a exclusividade é o índice único parcial, no instante do `INSERT`: se duas aprovações simultâneas chegarem, uma grava e a outra recebe `IntegrityError`. A tool converte isso em `{"status": "indisponivel", "mensagem": "A área já está reservada nesta data..."}`, e a API responde 200 normalmente. Cancelar muda `status` para `cancelada`, o que libera a data para nova reserva.

**Códigos que nunca se repetem:** `_novo_codigo` gera `RSV-` + 6 caracteres aleatórios e grava em `codigos_emitidos` (chave primária). Essa tabela recebe também os códigos iniciais e nunca é limpa, nem pela restauração, então nenhum código se repete, inclusive o de reservas canceladas.

**Por que não depende do modelo:** é uma restrição do banco, aplicada a cada gravação, qualquer que seja a ordem das chamadas.

## Como rodar

**Pré-requisitos**
- Python 3.12 ou superior
- [uv](https://docs.astral.sh/uv/)
- Uma chave do [Google AI Studio](https://aistudio.google.com/apikey)

**Variáveis do `.env`** (copie de `.env.example`):

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio. |
| `AURORA_MODELO` | não | Modelo Gemini dos agentes. Vazio usa `gemini-3.5-flash-lite`. |

No plano gratuito do AI Studio, alguns modelos têm cota de poucas requisições por dia por modelo (o `gemini-3.5-flash`, por exemplo, tinha 20 por dia no nosso projeto). O fluxo completo do avaliador faz algumas dezenas de chamadas, por isso o padrão é o `gemini-3.5-flash-lite`. Confira os limites do seu projeto no AI Studio e troque o modelo por `AURORA_MODELO`, se precisar.

**Instalação**

```bash
cp .env.example .env   # preencha GOOGLE_API_KEY
uv sync
```

**Restaurar os dados iniciais** (volta reservas e visitantes a `dados/*.json`; as sessões são mantidas):

```bash
uv run aurora-restaurar
```

**Subir a API** em `http://localhost:8000` (pare com Ctrl+C e suba de novo com o mesmo comando; sessões e dados continuam):

```bash
uv run aurora-api
```

Não há serviço externo para subir: tudo fica em arquivos SQLite em `estado/`.

**Exemplo rápido**

```bash
curl -s localhost:8000/apartamentos/101/reservas
S=$(curl -s -X POST localhost:8000/sessoes -H 'content-type: application/json' -d '{"apartamento":"101"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
curl -s -X POST localhost:8000/sessoes/$S/mensagens -H 'content-type: application/json' -d '{"texto":"Reserve o salão de festas para 2030-04-20."}'
# use o id de confirmacoes_pendentes:
curl -s -X POST localhost:8000/sessoes/$S/confirmacoes -H 'content-type: application/json' -d '{"id":"<id>","confirmado":true}'
curl -s localhost:8000/sessoes/$S/eventos
```

**Rotas**

| Método e caminho | Resposta |
|---|---|
| `POST /sessoes` `{"apartamento": "101"}` | 201 `{"session_id": "..."}` |
| `POST /sessoes/{session_id}/mensagens` `{"texto": "..."}` | 200 `{"resposta": "...", "confirmacoes_pendentes": [...]}` |
| `POST /sessoes/{session_id}/confirmacoes` `{"id": "...", "confirmado": true}` | 200 (mesmo formato) ou 409 |
| `GET /sessoes/{session_id}/eventos` | 200 lista completa de eventos, em ordem |
| `GET /apartamentos/{numero}/reservas` | 200 `[{"codigo", "area", "data"}]` (reservas ativas) |
| `GET /apartamentos/{numero}/visitantes` | 200 `[{"nome", "data"}]` |

As rotas com `{session_id}` respondem 404 quando a sessão não existe.
