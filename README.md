# V-GUARD — Backend

API em FastAPI + SQLite (T33 a T35). Recebe o que a Raspberry Pi envia
e serve o dashboard:

```
Raspberry Pi                          backend                          navegador
EventManager ─► Sync ──PUT/POST /api/*──► app.py ──► vguard.db (SQLite)
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
| `database.py`     | Conexões, transações, WAL e chaves estrangeiras SQLite               |
| `migrate_postgres.py` | Importação opcional de dados existentes; veja [MIGRATION.md](MIGRATION.md) |
| `schema.sql`       | As 4 tabelas, aplicadas sozinhas quando o servidor sobe             |
| `test_app.py`      | Testes das rotas                                                     |
| `test_e2e.py`      | `EventManager` e `Sync` de verdade enviando para o backend          |
| `requirements.txt` | Dependências Python                                                  |

## Instalar

Python 3.10+ com `sqlite3` e SQLite com suporte a JSON (`json_valid`).
Não é necessário instalar um servidor de banco. Recomendamos o Python 3.11
com SQLite atualizado pelo sistema/distribuição.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 - <<'PYSQL'
import sqlite3
print(sqlite3.sqlite_version)
print(sqlite3.connect(":memory:").execute("SELECT json_valid('{}')").fetchone())
PYSQL
```

O banco é criado automaticamente ao iniciar a API ou cadastrar um dispositivo.
Para trazer dados do backend PostgreSQL anterior, siga [MIGRATION.md](MIGRATION.md)
**antes de iniciar a API com o arquivo de destino**.

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
DASHBOARD_PASSWORD=escolha-uma-senha uvicorn app:app --host 0.0.0.0 --workers 1
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

**Fotos:** o `Sync` envia as fotos pelo mesmo enlace dos dados, Wi-Fi ou 4G,
logo depois dos metadados do evento. O backend não distingue o enlace: aceita
a foto de qualquer um. Cada foto pesa cerca de 50 kB e conta na franquia do 4G
(`cellular_bytes` no `/api/health`).

### Variáveis de ambiente

| Variável             | Padrão            | Uso                                                |
|----------------------|-------------------|----------------------------------------------------|
| `DATABASE_PATH`      | `vguard.db` na pasta do backend | Caminho do arquivo SQLite; relativo à pasta do backend |
| `PHOTOS_DIR`         | `./fotos`         | Onde as fotos recebidas ficam (fora do git)        |
| `DASHBOARD_DIR`      | `../dashboard`    | Pasta do `index.html`                              |
| `DASHBOARD_USER`     | `vguard`          | Usuário do dashboard                               |
| `DASHBOARD_PASSWORD` | vazio             | Senha do dashboard. **Sem ela, o dashboard não abre** |

O cadastro e o servidor devem usar o mesmo `DATABASE_PATH`. `:memory:` e URLs
não são aceitos, pois cada transação abre sua própria conexão. A antiga
`DATABASE_URL` deixa de ser usada; se ela estiver definida sem `DATABASE_PATH`,
o backend interrompe a inicialização com uma mensagem de migração.

Há um exemplo em `.env.example`: copie para `.env`, escolha a senha e use
`uvicorn app:app --env-file .env --host 0.0.0.0 --workers 1`.
O `python3 app.py novo-dispositivo` não carrega `.env` automaticamente:
exporte as mesmas variáveis no terminal ao cadastrar.

### Demonstração isolada

No backend, os comandos abaixo substituem `createdb vguard_demo` e
`DATABASE_URL=dbname=vguard_demo` do README antigo do dashboard:

```bash
export DATABASE_PATH=./vguard_demo.db PHOTOS_DIR=./fotos_demo
python3 app.py novo-dispositivo demo
DASHBOARD_PASSWORD=demo uvicorn app:app --port 8001 --workers 1
```

Depois, use `demo_data.py` do dashboard com o token exibido e
`VGUARD_BACKEND=http://localhost:8001/api`.

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
| `events`    | UUID gerado na Pi    | Tipo, horário, score, sensores e janela (JSON texto), nome da foto |
| `telemetry` | (dispositivo, `ts`)  | Uma linha por segundo, em JSON texto                      |
| `health`    | id do dispositivo    | A última saúde recebida e quando chegou              |

## Testes

```bash
python3 test_app.py
```

```bash
python3 test_e2e.py
```

Os testes criam bancos e fotos temporários próprios, mesmo se o shell tiver
`DATABASE_PATH` definido, e imprimem `OK` no fim. `test_app.py` usa um HTML de
teste; o `test_e2e.py` precisa de `integracao/` ao lado (ou `VGUARD_EVENTS`
apontando para `integracao/events`).

```bash
python3 test_sqlite.py
```

`test_sqlite.py` cobre persistência após reinicialização, JSON e booleanos,
chaves estrangeiras, rollback, lotes concorrentes sem duplicação, banco ocupado
(503 e reenvio) e banco removido durante a execução. Para testar a importação
com um PostgreSQL real e descartável, veja [MIGRATION.md](MIGRATION.md).

- **`test_app.py`:** autenticação do dispositivo e do dashboard; envio repetido
  sem duplicar; id local reaproveitado pela Pi; tipos de evento novos aceitos;
  foto antes do evento, foto que não é JPEG e foto grande demais; formato de
  todas as rotas de consulta.
- **`test_e2e.py`:** o `EventManager` e o `Sync` **de verdade** enviando para o
  backend numa porta local. Cobre:
  - 4G com telemetria, metadados e fotos, que chegam idênticas às da Pi, e foto que falha atrasando só a si mesma (RF20, RF21);
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

## Armazenamento, publicação e backup

- Use um disco **persistente e local ao backend**, tanto para `DATABASE_PATH`
  quanto para `PHOTOS_DIR`. O SQLite do backend é independente do banco da Pi.
- WAL permite leituras durante uma gravação, mas há apenas um gravador por vez.
  As transações são curtas, cada uma tem sua conexão e espera até 5 s por um
  bloqueio. Se falhar, a API responde 503 e a Pi mantém o dado na fila.
- Chaves estrangeiras ficam ligadas em toda conexão e `synchronous=FULL`
  preserva a durabilidade das transações confirmadas. Há índice por `ts` para
  as consultas de telemetria do dashboard.
- Comece com **um worker**. Não compartilhe o arquivo por NFS ou entre réplicas
  em máquinas diferentes. Uma frota maior exige medir a carga de escrita.
- Em WAL, os arquivos `vguard.db-wal` e `vguard.db-shm` podem existir. Não os
  apague nem copie apenas o `.db` enquanto a aplicação estiver escrevendo.
- Para um backup consistente do banco **e das fotos**, pare o backend, faça o
  backup pela API SQLite abaixo e copie a pasta `PHOTOS_DIR`. Preserve também
  a configuração necessária para restaurar o serviço.

```bash
python3 - <<'PYBACKUP'
import os
from pathlib import Path
import sqlite3
import database

path = database.resolve_path(os.environ.get("DATABASE_PATH", "vguard.db"))
backup = Path("vguard-backup.db")
# Recusa sobrescrever um backup anterior.
with backup.open("xb"):
    pass
with database.connection(path) as source:
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
PYBACKUP
```

A API de backup também funciona com o banco ativo; parar o backend permite
copiar as fotos do mesmo estado. Para restaurar, pare o serviço e use o backup
em um **novo caminho**, sem journals antigos, junto da pasta de fotos salva.

Referências: [WAL](https://www.sqlite.org/wal.html),
[concorrência e implantação](https://www.sqlite.org/whentouse.html),
[backup no Python](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).

## Pendências

- **Publicação (T36.1):** precisa de um serviço com HTTPS e disco persistente
  para o SQLite e as fotos. Em muitos planos gratuitos o disco é apagado a
  cada reinício.
- **4G de verdade (T36.2):** depende do módulo SIM7670G e da Pi.
- **Consulta de dispositivos (T35):** falta uma rota listando os dispositivos;
  com mais de um veículo, as consultas ganham o id dele.
