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
| `1.5.3+shvia.2` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Failover fast-fail: `usage_limit_reached` agora é terminal (o primário para rápido e resumível em vez de martelar retries) — enviado ao upstream como [PR #1174](https://github.com/usestrix/strix/pull/1174) e já incorporado no fork. `strix-run --auto` mais ágil (threshold 2). Config migrada do `.bashrc` para `.env`. |
| `1.5.3+shvia.1` | `1.5.3` (`bfaaa90`) | 2026-08-26 | Base do fork. Fix do import-race thread-unsafe do `openai-agents` no warmup (enviado ao upstream como [PR #1173](https://github.com/usestrix/strix/pull/1173)). Launcher `bin/strix-run` (primário/secundário config-driven via `.env`, budget por provedor, resume manual e `--auto`). Scaffolding: `.env.example`, `FORK.md`, `CLAUDE.md`, `.continue/`, `.claude/`. |

## Contribuições enviadas ao upstream

| PR | Título | Status |
|----|--------|--------|
| [#1173](https://github.com/usestrix/strix/pull/1173) | fix(warmup): pre-import the agents SDK to avoid a thread-race crash | aberto |
| [#1174](https://github.com/usestrix/strix/pull/1174) | fix(retry): treat provider usage-limit errors as terminal, not transient | aberto |
