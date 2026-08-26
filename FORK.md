# Fork SHVIA do Strix

Fork de [`usestrix/strix`](https://github.com/usestrix/strix) mantido em
[`samirhvbr/strix`](https://github.com/samirhvbr/strix). Este arquivo rastreia a
**nossa** evolução versus a do upstream.

## Versão

A versão do fork usa o formato PEP 440 de *local version*:

```
<versão-upstream>+shvia.<n>
```

A versão corrente fica em [`.fork-version`](.fork-version). Não alteramos o campo
`version` do `pyproject.toml` (para não conflitar em todo merge com o upstream).

A **regra de funcionamento e incremento** (quando o `shvia.<n>` sobe, e o mapa para o
`X.Y.Z` da casa) está em [`version.md`](version.md).

## Modelo de branches

| Branch        | Papel                                                                 |
|---------------|-----------------------------------------------------------------------|
| `master`      | **Nossa branch principal** (default do fork). Todas as features entram aqui. |
| `main`        | **Espelho do upstream.** Só recebe sync do upstream; base para PRs upstream. |
| `feat/*`      | Feature nossa → sai de `master` → volta para `master`.                 |
| `fix/*`       | Correção destinada ao upstream → sai de `main` (limpo) → PR para o upstream. |

**Sincronizar com o upstream:**

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main
git checkout master && git merge main        # traz a evolução deles para a nossa linha
```

## Changelog do fork

| Versão do fork | Baseado no upstream | Data       | Mudanças |
|----------------|---------------------|------------|----------|
| `1.5.3+shvia.12` | `1.5.3` (`bfaaa90`) | 2026-08-26 | `strix-run --auto` com **TUI ao vivo + failover**: o launcher roda o Strix interativo em background no mesmo process group (`< /dev/tty`); um watcher lê `strix.log`/`run.json` do run e, ao esgotar **janela/budget/pausa**, mata a árvore e **reabre a TUI no próximo agente** (resume). **Auto-continue** no resume via `--instruction` (o `runner` injeta como msg high-priority no root agent). **Budget por-provedor** no resume — cada provedor ganha o próprio teto sobre o já gasto (`STRIX_BUDGET_MODE=global` mantém o teto cumulativo antigo). Corrige o parsing `--resume -2 <run>` (antes o run caía em `TARGET`). Sem terminal → headless (modo antigo). Patches em `bin/strix-run` (commit `cb56b7a`). |
| `1.5.3+shvia.12` | `1.5.3` (`bfaaa90`) | 2026-08-26 | `strix-run pdf <run> [saida.pdf]`: gera o **PDF do relatório LOCAL** (via `generate_report_pdf`, reportlab) — sem e-mail, sem criptografia, sem relay na nuvem. O botão web "Export to PDF" continua usando o relay (criptografa + manda pro e-mail); este é o caminho local direto. |
| `1.5.3+shvia.11` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Viewer local **sem gate de e-mail**: `/api/runs` (lista "Past runs") e `/api/run|vulnerabilities|report|transcript` de runs históricos deixam de exigir `auth.is_verified()`. Mantém só o token de sessão do processo (a segurança real, HTTP 403 sem ele — mesmo com `--host`). Testado: com sessão `locked:false` (7 runs), sem sessão `locked:true`. Patch em `viewer/server.py` (fork-only; upstream gateia p/ growth). |
| `1.5.3+shvia.10` | `1.5.3` (`bfaaa90`) | 2026-08-26 | `strix-run list`: lista os runs locais no terminal (nome/status/vulns/custo/tokens) lendo `run.json`/`vulnerabilities.json` — sem o gate de e-mail da página web "Past runs" (que é recurso de conta na nuvem; o view por-run já é local/tokenizado). |
| `1.5.3+shvia.9` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Atalho `strix-run view [run]`: roda `strix view` já dentro do `STRIX_WORKDIR` (senão `strix view` procura em `./strix_runs` do cwd e diz "No runs found"). |
| `1.5.3+shvia.8` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Go 1.24 instalado → **TUI nativa** disponível. `strix-run` abre a TUI por padrão nos runs **manuais** (novo scan / resume); `-n`/`--headless` força headless; `--auto` continua SEMPRE headless (supervisão exige `-n` + `strix view`). |
| `1.5.3+shvia.7` | `1.5.3` (`bfaaa90`) | 2026-08-26 | `strix-run` avisa quando um provedor pago fica **sem budget** no `.env` (rodaria sem teto de custo). `.env.example` já traz `STRIX_BUDGET_MOONSHOT`. |
| `1.5.3+shvia.6` | `1.5.3` (`bfaaa90`) | 2026-08-26 | `--auto` vira **fila de N agentes** (primário→secundário→**terciário**): ao esgotar **janela** (`usage_limit_reached`) OU **budget** (`Token budget of`) de um, passa ao próximo (RESUME se há run, SCAN NOVO se não); para quando um **conclui** (`run.json status=completed`). Terceiro agente = Kimi (`moonshot/kimi-k3`, `MOONSHOT_API_KEY`, `-3/--tertiary`). |
| `1.5.3+shvia.5` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Review do PR #1174 (Greptile P1): usage-limit de provedor **não-OpenAI** (LiteLLM) agora também cai na parada resumível (`run_strix_scan` roteava só `openai.RateLimitError`) + teste de regressão do caso LiteLLM. |
| `1.5.3+shvia.4` | `1.5.3` (`bfaaa90`) | 2026-08-26 | `--auto` robusto: captura a saída do primário e detecta `usage_limit_reached` mesmo quando falha no **preflight** (antes de criar run). Failover inteligente — **RESUME** se há run, **SCAN NOVO** no secundário se o esgotamento foi no preflight. Detecta no 1º marcador (removido o threshold). |
| `1.5.3+shvia.3` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Correções do `bin/strix-run`: resolve symlink para carregar o `.env` (antes, via `~/.local/bin`, não achava as chaves); aceita `-t/--target`; detecta run NOVO no `--auto` (evita failover falso com run antigo) e aborta se o primário não criar run; `latest_run` ignora dirs sem `run.json`; blindado contra `pipefail`. |
| `1.5.3+shvia.2` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Failover fast-fail: `usage_limit_reached` agora é terminal (o primário para rápido e resumível em vez de martelar retries) — enviado ao upstream como [PR #1174](https://github.com/usestrix/strix/pull/1174) e já incorporado no fork. `strix-run --auto` mais ágil (threshold 2). Config migrada do `.bashrc` para `.env`. |
| `1.5.3+shvia.1` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Base do fork. Fix do import-race thread-unsafe do `openai-agents` no warmup (enviado ao upstream como [PR #1173](https://github.com/usestrix/strix/pull/1173)). Launcher `bin/strix-run` (primário/secundário config-driven via `.env`, budget por provedor, resume manual e `--auto`). Scaffolding: `.env.example`, `FORK.md`, `CLAUDE.md`, `.continue/`, `.claude/`. |

## Contribuições enviadas ao upstream

| PR | Título | Status |
|----|--------|--------|
| [#1173](https://github.com/usestrix/strix/pull/1173) | fix(warmup): pre-import the agents SDK to avoid a thread-race crash | aberto |
| [#1174](https://github.com/usestrix/strix/pull/1174) | fix(retry): treat provider usage-limit errors as terminal, not transient | aberto |
