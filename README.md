# V-GUARD — Backend

API em FastAPI + PostgreSQL (T33 a T35). Recebe o que a Raspberry Pi envia
e serve o dashboard:

```
Raspberry Pi                         backend                         navegador
EventManager ─► Sync ──PUT/POST /api/*──► app.py ──► PostgreSQL      
                                             │   └─► fotos/<id>.jpg  
                                             └──GET /api/* + index.html──► dashboard
```

O contrato já existia dos dois lados, e o backend não exigiu mudança neles:

- **recebimento:** o que o `Sync` envia ([integracao/events/sync.py](https://github.com/V-guard-org/integracao));
- **consulta:** as rotas do [README do dashboard](https://github.com/V-guard-org/dashboard).

A documentação OpenAPI é gerada sozinha em <http://localhost:8000/docs>.

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

As pastas `backend/`, `dashboard/` e `integracao/` ficam lado a lado em `v-guard/`.

```bash
DASHBOARD_PASSWORD=escolha-uma-senha uvicorn app:app --host 0.0.0.0
```

Abra <http://localhost:8000> e entre com o usuário `vguard` e a senha escolhida.
O backend serve o `index.html` do `../dashboard`.

### Ligar a Raspberry Pi (ou o notebook) ao backend

Cadastre o dispositivo; o comando mostra o token **uma única vez**:

```bash
python3 app.py novo-dispositivo vguard-01
```

No `integracao/`, rode o `vguard.py` com as variáveis que ele mostrou:

```bash
VGUARD_BACKEND=http://<ip-do-backend>:8000/api VGUARD_DEVICE=vguard-01 VGUARD_TOKEN=<token> python3 vguard.py --headless
```

O `Sync` envia a cada 60 s. As fotos só sobem por uma rede Wi-Fi listada em
`AUTHORIZED_WIFI` no `sync.py`.

### Variáveis de ambiente

| Variável             | Padrão            | Uso                                                |
|----------------------|-------------------|----------------------------------------------------|
| `DATABASE_URL`       | `dbname=vguard`   | Conexão do PostgreSQL (formato libpq)              |
| `PHOTOS_DIR`         | `./fotos`         | Onde as fotos recebidas ficam                      |
| `DASHBOARD_DIR`      | `../dashboard`    | Pasta do `index.html`                              |
| `DASHBOARD_USER`     | `vguard`          | Usuário do dashboard                               |
| `DASHBOARD_PASSWORD` | vazio             | Senha do dashboard. **Sem ela, o dashboard não abre** |

## Testes

```bash
python3 test_app.py
```

```bash
python3 test_e2e.py
```

Os dois usam o banco `vguard_test`, que é apagado a cada execução.

- **`test_app.py`:** autenticação do dispositivo e do dashboard; envio repetido
  sem duplicar; foto antes do evento, foto que não é JPEG e foto grande demais;
  formato de todas as rotas de consulta.
- **`test_e2e.py`:** o `EventManager` e o `Sync` **de verdade** enviando para o
  backend numa porta local. Cobre:
  - 4G sem fotos e, depois, Wi-Fi com fotos (RF20, RF21);
  - confirmação perdida e reenvio sem duplicar (RF22, RNF26);
  - servidor fora do ar sem perder nada (RF23);
  - janela de ±10 s e saúde do dispositivo chegando ao dashboard (RF17, RF32).

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
  recusa o que nunca vai servir: JSON sem `ts` ou foto que não é JPEG.
- **Respostas que fazem o `Sync` tentar de novo:**
  - token inválido: 401;
  - foto antes do evento: 409;
  - banco fora do ar: 503.
- **Fotos em disco**, não no banco. O banco guarda só o nome do arquivo, e o
  arquivo é gravado antes da linha no banco.
- **Um dispositivo:** as consultas ainda não filtram por dispositivo. Com a
  frota, as rotas ganham o id dele.

## Pendências

- **Publicação (T36.1):** precisa de conta num serviço com PostgreSQL e HTTPS,
  criada pela equipe. Em muitos planos gratuitos o disco é apagado a cada
  reinício, e as fotos precisam de disco persistente.
- **Foto ainda no cartão:** os metadados do evento não dizem se ele tem foto,
  então um evento cuja foto ainda espera o Wi-Fi aparece como "sem foto". Para
  distinguir, o `pending_events()` do `EventManager` precisaria enviar
  `has_photo`.
