# PostgreSQL → SQLite

A branch usa SQLite no backend, mantendo os contratos HTTP da Pi e do dashboard.
O arquivo do backend deve ser diferente de `integracao/dados/vguard.db`.

## Banco novo

Instale `requirements.txt`, defina `DATABASE_PATH` e cadastre o dispositivo
conforme o README. Nenhum driver ou servidor PostgreSQL é necessário.

## Banco com dados existentes

1. Reserve uma janela de manutenção e pare o backend antigo. A Pi continuará
   guardando dados pendentes e tentará enviar novamente quando a API voltar.
   Mantenha as gravações do backend antigo interrompidas até concluir a troca:
   a importação é uma fotografia, não uma sincronização contínua.
2. Faça backup do PostgreSQL (`pg_dump`), da pasta `PHOTOS_DIR` e da configuração.
   Preserve o código anterior para uma eventual volta.
3. Preserve a pasta de fotos no mesmo lugar ou copie-a integralmente para o
   novo servidor. O importador confere que cada foto referenciada existe;
   fotos ainda pendentes na Pi não precisam estar presentes.
4. Em um ambiente de importação, instale `requirements-migration.txt`. O driver
   `psycopg` é usado somente neste comando, nunca na API SQLite.
5. Execute a importação com um destino que **ainda não exista**:

   ```bash
   pip install -r requirements-migration.txt
   export POSTGRES_DSN='dbname=vguard'
   python3 migrate_postgres.py --output ./vguard.db --photos-dir ./fotos
   unset POSTGRES_DSN
   ```

   A conexão aceita o formato libpq e URL PostgreSQL; use `.pgpass` ou as
   variáveis de conexão do PostgreSQL para credenciais. Não versione senhas.
   Caminhos relativos de `--output` são relativos à pasta do backend, como
   `DATABASE_PATH`; `--photos-dir` é relativo ao terminal.

   O comando lê as quatro tabelas em uma transação `REPEATABLE READ READ ONLY`,
   transfere lotes de 1.000 registros e confere as contagens, as chaves
   estrangeiras e a integridade SQLite. Preserva IDs, hashes dos tokens, fotos,
   JSON, timestamps e sinalização de foto pendente. Datas de auditoria viram
   texto ISO 8601 UTC, `ts` mantém os segundos Unix e booleanos viram 0/1.
   O JSON volta a objetos/listas e os indicadores voltam a booleanos na API.

   O PostgreSQL é somente lido. O destino só aparece depois do sucesso;
   arquivos existentes nunca são sobrescritos. Em caso de falha, corrija a
   causa e execute novamente. JSON fora do formato esperado (objeto para
   `data`, lista para `event_window`) interrompe a migração para evitar perda
   silenciosa. A importação espera o schema PostgreSQL atual do backend,
   incluindo `photo_expected`.
6. Remova `DATABASE_URL` da configuração e exporte `DATABASE_PATH` apontando
   para o arquivo importado. Configure o mesmo `PHOTOS_DIR`, usuário e senha
   do dashboard. Suba o backend:

   ```bash
   unset DATABASE_URL
   export DATABASE_PATH=./vguard.db PHOTOS_DIR=./fotos
   # Defina DASHBOARD_PASSWORD com a senha do seu ambiente.
   uvicorn app:app --host 0.0.0.0 --workers 1
   ```

7. Confira eventos, telemetria, saúde e fotos no dashboard e um ciclo de envio
   da Pi. **Não recadastre os dispositivos migrados:** os hashes são preservados
   e seus tokens atuais continuam válidos. Recadastrar troca o token.

Os dados recebidos após a troca existem apenas no SQLite. Para voltar ao
PostgreSQL, pare o backend novo, preserve SQLite e fotos e planeje a transferência
desses dados antes de reativar o backend antigo. A Pi pode já ter confirmado e
limpado dados do SD; o rollback não deve depender de um reenvio automático.

## Testar a importação

Use um PostgreSQL descartável, separado do banco real:

```bash
pip install -r requirements-migration.txt
TEST_POSTGRES_DSN='dbname=vguard_migration_test' python3 test_migration.py
```

O teste cria um schema de nome aleatório e remove somente esse schema no fim.
Cobre mais de um lote, UUID, JSON Unicode, datas com fuso, nulos, booleanos,
preservação do hash, contagens, recusa de destino existente e falhas de foto/JSON
sem publicar um banco parcial. Sem `TEST_POSTGRES_DSN`, o teste é pulado.

Os testes normais (`test_app.py`, `test_sqlite.py`, `test_e2e.py`) rodam sem
PostgreSQL. Veja o README para backup e implantação com disco persistente.
