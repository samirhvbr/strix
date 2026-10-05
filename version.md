# Versão — fork SHVIA do Strix

**Current version:** `1.7.0+shvia.20`

> **Fonte da verdade (máquina):** [`.fork-version`](.fork-version) — uma linha.
> **Changelog por entrega:** [`FORK.md`](FORK.md).
> Este é o doc de versão no padrão da casa (todo repo tem um `version.md`): guarda a
> **regra de incremento** e espelha a versão corrente. Ao bumpar, mexa nos **três
> juntos**: `.fork-version`, a linha "Current version" aqui, e uma linha nova no topo da
> tabela do `FORK.md`.

---

## Por que aqui não é `X.Y.Z` como nos outros repos

Os projetos **próprios** da casa versionam em `X.Y.Z`, com o `version.md` como fonte
da verdade (ver skill-COMMITTER, AUDITOR, SHVIA-WEB): **Z** = cada entrega; **Y** =
mudança estrutural / fase concluída / quebra de contrato; **X** = release estável.

Este repo é um **fork de um projeto externo**
([`usestrix/strix`](https://github.com/usestrix/strix)). Então:

- O `X.Y.Z` **é do upstream** — não editamos o `version` do `pyproject.toml` (senão
  conflita em todo merge do upstream).
- A **nossa** versão é um *local version* PEP 440 por cima da deles:

  ```
  <versão-upstream>+shvia.<n>        ex.: 1.5.3+shvia.12
  ```

## Regra de funcionamento e incremento

| Parte | O que é | Quando muda |
|---|---|---|
| `<versão-upstream>` | A release do upstream em que estamos baseados (coluna "Baseado no upstream" do `FORK.md`) | **Só** ao sincronizar com o upstream (`git merge main`). Nunca à mão. |
| `shvia.<n>` | Contador **monotônico** das nossas entregas sobre essa base | **+1 a cada entrega validada em `master`** que muda comportamento, ferramenta, regra, segurança ou testes. **Uma entrega = uma linha no `FORK.md`.** |

**Mapa para a regra da casa:** o `<versão-upstream>` cobre o `X.Y` (vem do upstream); o
**`shvia.<n>` faz o papel do `Z`** (incremento por entrega). O fork não tem major/minor
próprios — é uma série linear de patches nossos.

**Não bumpa** `n`: correção de redação, formatação/lint, ou mudança que não altera
comportamento nem contrato.

### Como bumpar (checklist)

1. Entrega validada em `master` (rodou / testou).
2. `.fork-version`: `shvia.<n>` → `shvia.<n+1>`.
3. `version.md`: atualiza a linha **"Current version"** (no topo).
4. `FORK.md`: linha nova no **topo** da tabela — versão do fork, base do upstream, data, o que mudou.
5. Commit no estilo do fork (conventional-commits, ex.: `feat(strix-run): …` ou `docs(fork): shvia.<n> — …`) + `git push origin master`.
6. Tag and Release. Every fork version gets an annotated tag `v<version>` (e.g.
   `v1.6.2+shvia.16`) and a GitHub Release, otherwise the repo's Releases box shows
   nothing but the upstream tags:

   ```bash
   V="v$(cat .fork-version)"
   git tag -a "$V" -m "$(cat .fork-version)" && git push origin "$V"
   gh release create "$V" --verify-tag --latest --title "$V" --notes-file <notes.md>
   ```

   The notes come from the new `FORK.md` row, in English. Never create a Release for an
   older version without `--latest=false`, or it steals the "Latest" badge.

> **`build-release.yml` is inert on the fork, and should stay that way.** It is inherited
> from upstream and fires on every `v*` tag (5-OS PyInstaller build + an auto-created
> Release). Forks do not run upstream workflows until the owner enables them in the
> Actions tab, so tagging here builds nothing. If that is ever enabled, tag pushes will
> start burning CI minutes and creating their own Releases.

> O **COMMITTER** da casa **não opera** neste repo (sem `.committer.yml`): o
> versionamento aqui é **manual**, feito pelo agente que entrega. Os commits também
> seguem conventional-commits (para os PRs ao upstream saírem limpos), diferente do
> `X.Y.Z - descrição` dos repos próprios.
