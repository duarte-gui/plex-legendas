# plex-legendas

Automatiza a busca de legendas pela API do Plex — o mesmo recurso que a interface
oferece episódio a episódio, mas para uma série inteira de uma vez.

Opcionalmente extrai as legendas de dentro do banco do Plex e as grava como
arquivo, através do Bazarr.

Sem dependências: só a biblioteca padrão do Python 3.10+.

## Por que existe

O Plex tem uma busca de legendas muito boa, com pontuação por proximidade ao
release do arquivo. Mas ela só funciona **um item por vez, pela interface**. Numa
biblioteca com centenas de episódios pendentes, isso é inviável na mão.

Numa série de 144 episódios, este projeto levou **2 minutos e 17 segundos** e
resolveu 73 episódios — os demais já tinham legenda ou não tinham candidato bom
o suficiente.

## O que ele faz

1. Lista as séries do seu servidor Plex.
2. Para cada episódio sem legenda no idioma desejado, consulta os candidatos.
3. Aplica o de maior pontuação — **desde que passe de um mínimo configurável**.

Ao selecionar uma série, o painel mostra automaticamente a **cobertura atual**
(quantos episódios já têm legenda) — um scan só-leitura, sem baixar nada.

O resultado é agrupado **por temporada** e mostra o **nome do episódio**.
Clicar num episódio abre um seletor manual com todos os candidatos daquele
episódio (título, afinidade, score) — inclusive os de outra série, marcados
com aviso — para escolher e aplicar à mão quando o automático não acertar.
4. Opcionalmente, exporta as legendas para arquivo `.srt` ao lado do vídeo.

## Instalação

```bash
git clone https://github.com/duarte-gui/plex-legendas
cd plex-legendas
cp config.example.env .env
$EDITOR .env          # preencha PLEX_URL e PLEX_TOKEN
```

## Uso

```bash
python -m plexsubs                      # interface web em http://localhost:8770
python -m plexsubs series               # lista as séries e seus ratingKeys
python -m plexsubs processar 3243       # processa uma série
python -m plexsubs exportar --simular   # mostra o que seria exportado
python -m plexsubs processar 3243 --reavaliar                 # troca legendas mal casadas
python -m plexsubs processar 3243 --reavaliar --so-existentes  # idem, sem preencher episódios vazios
```

## Como o candidato é escolhido

O score do Plex mistura avaliação, downloads e casamento de release — então o
**mais bem votado nem sempre casa com o seu arquivo**. Na prática, isso morde:
uma legenda de 720p muito votada, aplicada a um arquivo 1080p de outro release,
fica dessincronizada, mesmo havendo uma legenda 1080p igualmente boa na lista.

Por isso a escolha ordena **do maior para o menor, primeiro pela correspondência
com o nome do arquivo, depois pela avaliação**:

1. **Afinidade de release** — compara resolução, fonte (WEB-DL/HDTV/BluRay…),
   codec e grupo entre o nome do arquivo e o título de cada candidato
   (`afinidade_release` em `plexsubs/plex.py`). Resolução pesa mais, porque é o
   que mais afeta o sincronismo.
2. **Score do Plex** — desempata entre candidatos de mesma afinidade.

Se nenhum candidato casa com o arquivo (afinidade 0 para todos), a decisão
recai sobre o mais bem votado — o comportamento antigo, como último recurso.

O `--reavaliar` reconsidera episódios que já têm legenda e só troca por um
candidato de afinidade **estritamente maior**. Duas salvaguardas o tornam seguro
em qualquer série:

- **Não toca em legenda que não veio de provedor.** Legendas geradas por outro
  meio (tradução, extração de faixa embutida) ficam como arquivo sidecar sem
  `providerTitle`; o `reavaliar` as protege, pois trocá-las por uma de provedor
  poderia introduzir uma legenda mal casada.
- **`--so-existentes`** impede preencher episódios vazios — útil para conteúdo
  dublado que deve permanecer sem legenda.

O `SCORE_MIN` (padrão 1000) segue valendo como piso: abaixo dele o candidato é
descartado, porque uma legenda errada é pior que nenhuma. Faixas observadas:

| Situação | Score |
|---|---|
| Legenda do mesmo release | 5.000 – 24.000 |
| Outro release da mesma obra | 1.000 – 5.000 |
| Correspondência espúria | 0 – 600 |

## Onde o Plex guarda a legenda baixada

**Não é num arquivo.** A legenda vira um blob comprimido em gzip dentro de
`com.plexapp.plugins.library.blobs.db`, e o stream correspondente fica com
`url = 'blob://'`.

Consequências:

- funciona no Plex;
- **nenhum outro player a enxerga**;
- gerenciadores como o Bazarr continuam considerando o episódio pendente.

O comando `exportar` resolve isso: lê os blobs, descomprime e envia cada legenda
ao Bazarr, que grava o arquivo ao lado do vídeo com as permissões corretas e o
registra. Usar o Bazarr como intermediário evita ter de dar acesso de escrita ao
compartilhamento de mídia.

## Notas sobre a API do Plex

Estes endpoints não são documentados oficialmente. O que foi observado:

```
GET /library/metadata/{rk}/subtitles?language=pt-BR
    → lista de candidatos, cada um com score e id de stream

PUT /library/metadata/{rk}/subtitles?key=/library/streams/{id}
    → baixa e aplica; responde 200 ou 204
```

Duas armadilhas encontradas:

**O código de idioma precisa ter duas partes.** `pt-BR` e `pt` funcionam;
`por` e `pob` fazem o servidor responder **HTTP 500**.

**`PUT /library/parts/{id}?subtitleStreamID=…` não serve para baixar.** Ele só
seleciona uma faixa já presente; para um candidato de busca, responde HTTP 400.

## Limitações conhecidas

**A qualidade da busca depende dos metadados.** Séries cujo título foi
localizado para outro idioma podem produzir resultados ruins: numa biblioteca de
testes, um episódio devolveu candidatos de três séries sem relação, todos com
score abaixo de 600 — corretamente descartados pelo mínimo, mas sem nada útil
para aplicar.

**Só episódios.** Filmes usam outro endpoint no Bazarr e ainda não estão
implementados na exportação.

**O nome do arquivo exportado é decidido pelo Bazarr**, que pode gravar como
`.pt.srt` ou `.pt-BR.srt` conforme sua configuração.

## Licença

MIT.
