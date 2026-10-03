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
git push origin main                         # publish the mirror; skipping this left origin/main 74 commits behind
git checkout master && git merge main        # traz a evolução deles para a nossa linha
```

## Changelog do fork

| Versão do fork | Baseado no upstream | Data       | Mudanças |
|----------------|---------------------|------------|----------|
| `1.6.2+shvia.19` | `1.6.2` + 43 commits (`99c0711`) | 2026-10-03 | **Third sync onto upstream `main`** (still no upstream release after v1.6.2, so the base version stays `1.6.2`): 7 commits after `shvia.17`'s base (`ef272b8`). The SDK route (Responses vs chat completions) is now chosen from the model instead of the base URL, `reasoning_effort` is sent as configured (an explicit `none` on chat completions included), and the `create_vulnerability_report` parameter descriptions are restored. Clean merge with no conflicts (`models.py` and `runner.py` auto-merged next to our usage-limit patch). Full suite green (2221 passed, 3 xfailed). Re-measured on the merged code with a local gateway: the request body for `minimax/MiniMax-M3` is unchanged (no `thinking`, no `reasoning_effort`, also at effort `none`), DeepSeek still gets `thinking: {type: enabled}`, and the launcher's key path and the M3 cost estimate (US$ 0.0009 for 1000 in / 500 out) still hold, so the `shvia.18` conclusions stand. Our three PRs to upstream (#1173, #1174, #1279) are still open and unreviewed. |
| `1.6.2+shvia.18` | `1.6.2` + 36 commits (`ef272b8`) | 2026-10-03 | **MiniMax-M3 as an agent option in `strix-run`.** The launcher had no entry for the `minimax` provider, so `minimax/MiniMax-M3` silently fell into the generic branch (`LLM_API_KEY` + `STRIX_BUDGET_DEFAULT`); it now reads `MINIMAX_API_KEY` and `STRIX_BUDGET_MINIMAX`, and `.env.example` carries both. Use it in any slot (`STRIX_SECONDARY_LLM=minimax/MiniMax-M3`) or ad hoc (`strix-run --agent minimax/MiniMax-M3 <target>`); the defaults are untouched. No engine change is needed for "thinking": MiniMax's Chat Completions API runs adaptive thinking when `thinking` is omitted (documented), `reasoning_effort` is ignored by M3, and Strix sends neither for this model (measured against a local gateway), which is the same as the opencode `thinking` variant the benchmark used. Measured: through the launcher's `LLM_API_KEY` path the key and model reach `/v1/chat/completions`, and Strix prices M3 (US$ 0.0009 for 1000 in / 500 out), so `--max-budget` is a real ceiling. Not measured: a live call (there is no MiniMax key in `.env`), so how M3's inline `<think>` text looks in the TUI is unverified. New `tests/test_strix_run_providers.py` runs the real launcher against a stub for minimax, deepseek, moonshot and the generic fallback (red on the minimax case before the fix). Benchmark context (LEB-100-A, median of 3): MiniMax-M3 thinking 462 (462 · 616 · 434), DeepSeek V4 Pro 496, DeepSeek V4.1 Flash 612. |
| `1.6.2+shvia.17` | `1.6.2` + 36 commits (`ef272b8`) | 2026-09-30 | **Second sync onto upstream `main` the same day**: 14 commits landed after `shvia.16`'s base (`6ae036e`), among them `budget_policy=pause` (agents park at the spend limit until the operator resumes), the per-run scope moved after the prompt cache point, OpenRouter sticky sessions with telemetry, and removal of the model-quality warning. One conflict, the import lines of `strix/core/runner.py` (kept our `codex` import for the usage-limit patch next to upstream's new `render_scope_prompt`). All fork patches intact: the 66 fork-specific tests (usage-limit, viewer gate/PDF) pass. Full suite green (2160 passed, 3 xfailed; the total dropped from 2220 because upstream deleted the parametrized model-allowlist tests together with the warning). |
| `1.6.2+shvia.16` | `1.6.2` + 22 commits (`6ae036e`) | 2026-09-30 | **Synced the fork onto upstream `main` (`6ae036e`), 22 commits past v1.6.2** (upstream has not cut a release since, so the base version stays `1.6.2`). All fork patches kept (usage-limit terminal, local PDF export, viewer without e-mail gate, `strix-run`). Two conflicts: `strix/config/models.py` (kept our `_not_usage_limit_error` next to upstream's `_routes_via_litellm`/`_litellm_provider`) and the generated viewer bundle in `static/` (rebuilt from the merged frontend sources, so it carries both upstream's `VulnReportDeleteRenderer` and our local-PDF button). Full suite green (2220 passed, 3 xfailed). Brings in lazy MCP initialization, the structured per-attempt provider request log, blank tool-call id fill-in, agent-deletable vulnerability reports, `STRIX_API_TYPE`, the Exa search query fix, read-only local sources as `:ro` bind mounts and TUI ctrl+z suspend. **Housekeeping:** first version with a git tag and a GitHub Release; every earlier fork version was tagged `v<version>` at its bump commit and published as a Release, and the upstream tags v1.6.0–v1.6.2 were pushed to the fork (the fork's Releases box showed only 27 stale upstream tags). `origin/main` fast-forwarded (it was 74 commits behind) and the mirrored upstream branches were removed from `origin`. |
| `1.6.2+shvia.15` | `1.6.2` (`ff5c8cc`) | 2026-09-06 | **Synced the fork onto upstream v1.6.2** (was 1.5.3). Reconciled our patches: adopted upstream's `wait_for_import_warmup()` and dropped our `_preimport_thread_unsafe_sdk` (upstream's own warm-up rework supersedes PR #1173; removed the obsolete `tests/test_warmup.py`); kept upstream's dedicated `except RateLimitError` and narrowed our F3 usage-limit handler to the non-RateLimitError/LiteLLM case (no dead code); rebuilt the viewer frontend bundle so the local-PDF button rides on upstream's new frontend; adapted the viewer history test to our session-only gating. Full suite green (1740). Brings in upstream MCP support, Exa web search, and viewer/telemetry fixes. |
| `1.5.3+shvia.14` | `1.5.3` (`bfaaa90`) | 2026-09-06 | **Cost report attributed the wrong provider for a shared model name.** `resolve_litellm_model` broke a price tie by returning the alphabetically first LiteLLM key; with litellm 1.90.1 an aggregator re-listing (`openrouter/x-ai/grok-4.5`) now sorts ahead of the native `xai/grok-4.5`. Now prefers the first-party listing (key == `<litellm_provider>/<name>`) before price consensus, with deterministic mock-backed regression tests. Fixes the red `test_resolves_common_bare_model_names` on the pinned litellm. Sent upstream as [PR #1279](https://github.com/usestrix/strix/pull/1279); carried here meanwhile. |
| `1.5.3+shvia.13` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Botão **"Export report to PDF" no viewer web baixa LOCAL, sem e-mail**. Server: novo `GET /api/report/pdf?run=` (PDF plano via `generate_report_pdf`, attachment, session-gated → 403 sem token). Front: os 2 botões (sidebar + CTA) repontados p/ baixar do endpoint; texto "Download the PDF report… no email, no cloud"; **front recompilado** (`static/`). Testado: com sessão 200/application/pdf, sem sessão 403. O botão de **email report** (relay) foi substituído pelo download local. |
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
| [#1279](https://github.com/usestrix/strix/pull/1279) | fix(pricing): prefer a native provider over aggregator re-listings | open |
| [#1173](https://github.com/usestrix/strix/pull/1173) | fix(warmup): pre-import the agents SDK to avoid a thread-race crash | superseded (upstream reworked warm-up in v1.6.x) |
| [#1174](https://github.com/usestrix/strix/pull/1174) | fix(retry): treat provider usage-limit errors as terminal, not transient | aberto |
