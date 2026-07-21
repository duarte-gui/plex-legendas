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
```

## O score, e por que ele importa

Cada candidato vem com uma pontuação que mede o quanto ele corresponde ao
**seu arquivo específico** — release, resolução, codec, grupo.

Na prática, a separação é nítida:

| Situação | Score observado |
|---|---|
| Legenda do mesmo release | 5.000 – 24.000 |
| Legenda de outro release da mesma obra | 1.000 – 5.000 |
| Correspondência espúria, de outra obra | 0 – 600 |

O padrão de `SCORE_MIN=1000` fica na fronteira. Reduza se estiver perdendo
legendas legítimas; aumente se aparecer legenda de outra série.

Descartar é proposital: uma legenda errada é pior que nenhuma, porque parece
resolvido até você começar a assistir.

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
