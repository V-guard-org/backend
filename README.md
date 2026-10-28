# V-GUARD — Backend

API em FastAPI + PostgreSQL (T33 a T35). Recebe o que a Raspberry Pi envia
e serve o dashboard:

```
Raspberry Pi                          backend                          navegador
EventManager ─► Sync ──PUT/POST /api/*──► app.py ──► PostgreSQL
                                             │   └─► fotos/<id>.jpg
                                             └──GET /api/* + index.html──► dashboard
```

O contrato já existia dos dois lados, e o backend se encaixou no meio:

- **recebimento:** o que o `Sync` envia ([integracao/events/sync.py](https://github.com/V-guard-org/integracao));
- **consulta:** as rotas do [README do dashboard](https://github.com/V-guard-org/dashboard).

A documentação OpenAPI é gerada sozinha em <http://localhost:8000/docs>.

## Arquivos

| Arquivo            | O que é                                                              |
|--------------------|----------------------------------------------------------------------|
| `app.py`           | A API inteira e o cadastro de dispositivos (`python3 app.py novo-dispositivo`) |
| `schema.sql`       | As 4 tabelas, aplicadas sozinhas quando o servidor sobe             |
| `test_app.py`      | Testes das rotas                                                     |
| `test_e2e.py`      | `EventManager` e `Sync` de verdade enviando para o backend          |
| `requirements.txt` | Dependências Python                                                  |

## Instalar

PostgreSQL (uma vez, precisa de `sudo`):

```bash
sudo apt install postgresql
```

```bash
sudo -u postgres createuser -s $USER
```

```bash
createdb vguard && createdb vguard_test
```

Dependências Python:

```bash
pip install --user -r requirements.txt
```

## Executar

As pastas ficam lado a lado em `v-guard/`:

```
v-guard/
├── integracao/     # o que roda na Raspberry Pi (vguard.py)
├── backend/
└── dashboard/      # o index.html que o backend serve
```

### 1. Subir o backend

```bash
DASHBOARD_PASSWORD=escolha-uma-senha uvicorn app:app --host 0.0.0.0
```

Abra <http://localhost:8000> e entre com o usuário `vguard` e a senha
escolhida. Se a janela de login não aparecer, use
`http://vguard:<senha>@localhost:8000`.

O primeiro `GET / 401 Unauthorized` no terminal é normal: é ele que faz o
navegador pedir o login.

### 2. Cadastrar o dispositivo (uma vez)

Em outro terminal:

```bash
python3 app.py novo-dispositivo vguard-01
```

Ele mostra duas linhas `export VGUARD_DEVICE=...` e `export VGUARD_TOKEN=...`.
**O token aparece uma única vez** e não fica guardado no servidor, só o hash
dele. Perdeu? Rode o comando de novo: ele gera um token novo e o antigo deixa
de valer.

### 3. Ligar a Raspberry Pi (ou o notebook) ao backend

No terminal onde o `vguard.py` vai rodar, cole as duas linhas `export` e depois:

```bash
cd ../integracao && VGUARD_BACKEND=http://localhost:8000/api python3 vguard.py
```

Na Pi, troque `localhost` pelo endereço do backend e acrescente `--headless`.

O `Sync` envia a cada 60 s. No terminal do `vguard.py` aparece
`enlace: wifi`, `enlace: cellular` ou `enlace: nenhum (os dados ficam no cartão)`;
no do backend aparecem `PUT /api/events/...` com `200 OK`.

**Fotos:** o `Sync` só envia fotos pelo Wi-Fi. Com o backend no **mesmo
computador**, o envio conta como Wi-Fi e as fotos sobem. Na Pi, o nome da
rede precisa estar em `AUTHORIZED_WIFI`, no `sync.py`; pelo 4G sobem só os
dados, e as fotos esperam o Wi-Fi.

### Variáveis de ambiente

| Variável             | Padrão            | Uso                                                |
|----------------------|-------------------|----------------------------------------------------|
| `DATABASE_URL`       | `dbname=vguard`   | Conexão do PostgreSQL (formato libpq)              |
| `PHOTOS_DIR`         | `./fotos`         | Onde as fotos recebidas ficam (fora do git)        |
| `DASHBOARD_DIR`      | `../dashboard`    | Pasta do `index.html`                              |
| `DASHBOARD_USER`     | `vguard`          | Usuário do dashboard                               |
| `DASHBOARD_PASSWORD` | vazio             | Senha do dashboard. **Sem ela, o dashboard não abre** |

### Se der problema

| O que aparece | O que fazer |
|---|---|
| `Address already in use` | Já há algo na porta 8000 (outro backend já aberto). Feche-o com Ctrl+C ou use `--port 8001`. |
| `401` seguidos no backend | Login errado: o usuário é `vguard` e a senha é a do `DASHBOARD_PASSWORD`. |
| `bash: token: Arquivo ou diretório inexistente` | O texto `<token>` foi colado literalmente. Use as linhas `export` do passo 2. |
| `VGUARD_TOKEN vazio` no `vguard.py` | Faltou colar as linhas `export` naquele terminal. |
| `enlace: nenhum` no `vguard.py` | A Pi não tem rede autorizada: confira `AUTHORIZED_WIFI` e o módulo 4G. Os dados ficam no cartão e sobem quando houver enlace. |
| Nada chega ao dashboard | Espere 60 s. Depois, confira no terminal do backend se aparecem `POST /api/health` e `PUT /api/events/...` com `200`. |

## Rotas

### Recebimento (Raspberry Pi)

Exigem os cabeçalhos `X-Device-Id` e `Authorization: Bearer <token>`, que o `Sync` envia.

| Rota | O que faz | Respostas |
|---|---|---|
| `POST /api/health` | Guarda a saúde do dispositivo (só a última) | 200 |
| `POST /api/telemetry` | Grava um lote de telemetria; linhas repetidas são ignoradas | 200 |
| `PUT /api/events/{id}` | Grava o evento, com a janela de ±10 s; reenvio não duplica | 200 |
| `PUT /api/events/{id}/photo` | Grava o JPEG do evento | 200; **409** evento ainda não chegou; **400** não é JPEG; **413** maior que 5 MB |

Token inválido responde **401** em todas.

### Consulta (dashboard)

Exigem login (HTTP Basic). Rotas descritas no README do dashboard:

| Rota | O que devolve |
|---|---|
| `GET /` | O `index.html` do dashboard, sem cache |
| `GET /api/health` | Última saúde recebida, com `last_contact` no horário do servidor |
| `GET /api/events?limit=200` | Eventos, do mais novo para o mais antigo |
| `GET /api/events/{id}` | Um evento, com a janela de ±10 s |
| `GET /api/events/{id}/photo` | A foto, ou 404 se ainda não chegou |
| `GET /api/telemetry` | As 10 000 linhas de telemetria mais recentes, em ordem de tempo |

Os formatos estão no README do dashboard.

## Modelo

| Tabela      | Chave                | O que guarda                                         |
|-------------|----------------------|------------------------------------------------------|
| `devices`   | id do dispositivo    | Hash do token                                        |
| `events`    | UUID gerado na Pi    | Tipo, horário, score, sensores e janela (JSONB), nome da foto |
| `telemetry` | (dispositivo, `ts`)  | Uma linha por segundo, em JSONB                      |
| `health`    | id do dispositivo    | A última saúde recebida e quando chegou              |

## Testes

```bash
python3 test_app.py
```

```bash
python3 test_e2e.py
```

Os dois usam o banco `vguard_test`, que é apagado a cada execução, e
imprimem `OK` no fim. O `test_e2e.py` precisa do `integracao/` ao lado.

- **`test_app.py`:** autenticação do dispositivo e do dashboard; envio repetido
  sem duplicar; id local reaproveitado pela Pi; tipos de evento novos aceitos;
  foto antes do evento, foto que não é JPEG e foto grande demais; formato de
  todas as rotas de consulta.
- **`test_e2e.py`:** o `EventManager` e o `Sync` **de verdade** enviando para o
  backend numa porta local. Cobre:
  - 4G sem fotos e, depois, Wi-Fi com fotos, que chegam idênticas às da Pi (RF20, RF21);
  - confirmação perdida e reenvio sem duplicar (RF22, RNF26);
  - servidor fora do ar sem perder nada, e o pendente subindo quando ele volta (RF23);
  - janela de ±10 s e saúde do dispositivo chegando ao dashboard, já com a
    fila vazia depois do envio (RF17, RF32).

## Decisões

- **Idempotência:**
  - evento pelo UUID gerado na Pi;
  - telemetria por `(dispositivo, ts)`, **não** pelo id local da Pi, porque o
    SQLite da Pi reaproveita ids quando a limpeza esvazia a tabela, e o reenvio
    seria descartado;
  - foto pelo id do evento, com reenvio sobrescrevendo o arquivo.
- **Validação tolerante:** o `Sync` trata as respostas 400, 413 e 422 como
  recusa definitiva: o item sai da fila e fica só no cartão. Por isso o
  servidor aceita campos extras e tipos de evento novos (RF34, RF35), e só
  recusa o que nunca vai servir: dado sem `ts` ou foto que não é JPEG.
- **Respostas que fazem o `Sync` tentar de novo:** token inválido (401), foto
  antes do evento (409) e banco fora do ar (503).
- **Saúde no começo e no fim de cada ciclo:** o `Sync` manda no começo como
  sinal de vida e, se a fila mudou, de novo no fim. Assim o dashboard mostra as
  pendências já atualizadas.
- **Fotos em disco**, não no banco. O banco guarda só o nome do arquivo, e o
  arquivo é gravado antes da linha no banco. A pasta `fotos/` fica fora do git.
- **Dashboard só com senha:** o servidor guarda fotos de motoristas. Sem
  `DASHBOARD_PASSWORD`, o dashboard não abre.
- **Um dispositivo:** as consultas ainda não filtram por dispositivo.

## Pendências

- **Publicação (T36.1):** precisa de conta num serviço com PostgreSQL e HTTPS,
  criada pela equipe. Em muitos planos gratuitos o disco é apagado a cada
  reinício, e as fotos precisam de disco persistente.
- **4G de verdade (T36.2):** depende do módulo SIM7670G e da Pi.
- **Consulta de dispositivos (T35):** falta uma rota listando os dispositivos;
  com mais de um veículo, as consultas ganham o id dele.
