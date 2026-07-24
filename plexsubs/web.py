"""Interface web. Usa só a biblioteca padrão — nenhuma dependência externa."""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config
from .plex import Plex, processar_serie

PAGINA = """<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Legendas via Plex</title>
<style>
  :root {
    --fundo:#f3f5f8; --papel:#fff; --linha:#d3dae3; --tinta:#1b2430;
    --suave:#5d6b7a; --tenue:#8b98a7; --acento:#2f6191;
    --ok:#2f7d4f; --alerta:#a8712a; --erro:#b4462f;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root { --fundo:#12171d; --papel:#1a212a; --linha:#2e3945; --tinta:#e2e8ef;
            --suave:#98a5b3; --tenue:#6b7885; --acento:#6ba3d8;
            --ok:#5fbe85; --alerta:#d9a05b; --erro:#e0785e; }
  }
  * { box-sizing:border-box }
  body { margin:0; padding:2rem 1.25rem 4rem; background:var(--fundo); color:var(--tinta);
         font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
         font-variant-numeric:tabular-nums }
  .env { max-width:940px; margin:0 auto; display:flex; flex-direction:column; gap:1.5rem }
  header { border-bottom:2px solid var(--tinta); padding-bottom:.9rem }
  h1 { margin:0 0 .25rem; font-size:1.7rem; letter-spacing:-.02em }
  header p { margin:0; color:var(--suave); max-width:60ch }
  .barra { display:flex; flex-wrap:wrap; gap:.75rem; align-items:end }
  label { display:flex; flex-direction:column; gap:.3rem; font-size:.78rem;
          text-transform:uppercase; letter-spacing:.07em; color:var(--tenue) }
  select,input { font:inherit; padding:.5rem .6rem; background:var(--papel);
                 color:var(--tinta); border:1px solid var(--linha); min-width:9rem }
  select { min-width:20rem }
  button { font:inherit; font-weight:600; padding:.55rem 1.1rem; cursor:pointer;
           background:var(--acento); color:#fff; border:0 }
  button:disabled { opacity:.5; cursor:default }
  button.sec { background:transparent; color:var(--acento); border:1px solid var(--acento) }
  .placar { display:flex; flex-wrap:wrap; gap:1.75rem; padding:.9rem 0;
            border-top:1px solid var(--linha); border-bottom:1px solid var(--linha) }
  .n { font-size:1.5rem; font-weight:700; line-height:1 }
  .r { font-size:.7rem; text-transform:uppercase; letter-spacing:.08em;
       color:var(--tenue); margin-top:.25rem }
  .prog { height:5px; background:var(--linha); overflow:hidden }
  .prog i { display:block; height:100%; background:var(--acento); width:0; transition:width .25s }
  .linhas { border:1px solid var(--linha); background:var(--papel);
            max-height:26rem; overflow:auto; font-family:var(--mono); font-size:.8rem }
  .l { display:flex; gap:.7rem; padding:.32rem .7rem; border-bottom:1px solid var(--linha) }
  .l:last-child { border-bottom:0 }
  .l .ep { min-width:5.5rem; color:var(--suave) }
  .l .st { min-width:7rem; font-weight:600 }
  .l .de { color:var(--tenue); overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .baixada .st { color:var(--ok) } .score_baixo .st { color:var(--alerta) }
  .erro .st,.sem_candidato .st { color:var(--erro) }
  .ja_tinha { opacity:.55 }
  .cob { font-size:.9rem; color:var(--suave); min-height:1.3em; padding:.2rem 0 }
  .cob.ativo { color:var(--tenue) }
  .cob.ok { color:var(--ok); font-weight:600 }
  .cob.parcial { color:var(--alerta); font-weight:600 }
  footer { color:var(--tenue); font-size:.78rem; border-top:1px solid var(--linha); padding-top:.9rem }
  code { font-family:var(--mono); font-size:.85em }
</style></head><body>
<div class="env">
  <header>
    <h1>Legendas via Plex</h1>
    <p>Busca legendas pela API do Plex e aplica a de melhor pontuação, descartando
       correspondências fracas. Opcionalmente exporta para arquivo através do Bazarr.</p>
  </header>

  <div class="barra">
    <label>Série
      <select id="serie"><option>carregando…</option></select>
    </label>
    <label>Score mínimo
      <input id="score" type="number" value="__SCORE__" min="0" step="100">
    </label>
    <button id="cob" class="sec">Ver cobertura</button>
    <button id="ir">Buscar legendas</button>
    <button id="exp" class="sec" title="__EXPT__" __EXPD__>Exportar para arquivo</button>
  </div>

  <div id="resumo-cob" class="cob"></div>

  <div class="prog"><i id="pi"></i></div>

  <div class="placar">
    <div><div class="n" id="c-baixada">0</div><div class="r">baixadas</div></div>
    <div><div class="n" id="c-ja_tinha">0</div><div class="r">já tinham</div></div>
    <div><div class="n" id="c-score_baixo">0</div><div class="r">score baixo</div></div>
    <div><div class="n" id="c-sem_candidato">0</div><div class="r">sem candidato</div></div>
    <div><div class="n" id="c-erro">0</div><div class="r">erros</div></div>
  </div>

  <div class="linhas" id="log"></div>

  <footer>
    Score alto indica que a legenda corresponde ao mesmo release do arquivo — daí o
    sincronismo vir correto. Abaixo do mínimo o candidato é descartado em vez de
    aplicado, evitando legenda de outra obra. Veja o <code>README</code> para detalhes.
  </footer>
</div>

<script>
const $ = s => document.querySelector(s);
const contagem = {baixada:0, ja_tinha:0, score_baixo:0, sem_candidato:0, erro:0};

fetch('api/series').then(r=>r.json()).then(ss=>{
  const sel = $('#serie'); sel.innerHTML='';
  for (const s of ss) {
    const o=document.createElement('option');
    o.value=s.rating_key; o.textContent=`${s.titulo}  ·  ${s.biblioteca}`;
    sel.appendChild(o);
  }
  if (ss.length) cobertura(sel.value);   // já mostra a cobertura da 1ª série
}).catch(()=> $('#serie').innerHTML='<option>falha ao consultar o Plex</option>');

function zera(){ for(const k in contagem){contagem[k]=0; $('#c-'+k).textContent='0';}
                 $('#log').innerHTML=''; $('#pi').style.width='0'; }

function anota(d){
  if (d.estado in contagem) { contagem[d.estado]++; $('#c-'+d.estado).textContent=contagem[d.estado]; }
  const det = d.release ? `afinidade ${d.afinidade ?? 0} · score ${d.score} · ${d.release}`
            : d.score !== undefined ? `melhor score ${d.score}`
            : d.detalhe || d.titulo || '';
  const el=document.createElement('div');
  el.className='l '+d.estado;
  el.innerHTML=`<span class="ep">${d.ep||''}</span>`+
               `<span class="st">${(d.estado||'').replace('_',' ')}</span>`+
               `<span class="de"></span>`;
  el.querySelector('.de').textContent=det;
  const log=$('#log'); log.appendChild(el); log.scrollTop=log.scrollHeight;
  if (d.total) $('#pi').style.width = (100*d.i/d.total)+'%';
}

async function fluxo(url){
  zera(); $('#ir').disabled=true; $('#exp').disabled=true;
  try {
    const r = await fetch(url);
    const leitor = r.body.getReader(); const dec=new TextDecoder(); let resto='';
    while (true) {
      const {done, value} = await leitor.read(); if (done) break;
      resto += dec.decode(value, {stream:true});
      const partes = resto.split('\\n'); resto = partes.pop();
      for (const p of partes) if (p.trim()) { try { anota(JSON.parse(p)); } catch(e){} }
    }
  } finally { $('#ir').disabled=false; $('#exp').disabled=false; }
}

// Cobertura: varre a série (sem baixar) e mostra quantos já têm legenda.
let cobToken = 0;
async function cobertura(serie){
  const meu = ++cobToken;               // cancela scan anterior se trocar de série
  const box = $('#resumo-cob');
  box.textContent = 'consultando o Plex…'; box.className = 'cob ativo';
  try {
    const r = await fetch(`api/cobertura?serie=${serie}`);
    const leitor = r.body.getReader(); const dec = new TextDecoder(); let resto='';
    let com=0, total=0, i=0;
    while (true) {
      const {done, value} = await leitor.read(); if (done) break;
      if (meu !== cobToken) { leitor.cancel(); return; }
      resto += dec.decode(value, {stream:true});
      const partes = resto.split('\\n'); resto = partes.pop();
      for (const p of partes) if (p.trim()) {
        try { const d=JSON.parse(p); com=d.com; total=d.total; i=d.i;
              box.textContent = `escaneando… ${i}/${total} · ${com} com legenda`; } catch(e){}
      }
    }
    const falta = total - com;
    box.textContent = `${com} de ${total} episódios já têm legenda` + (falta ? ` · ${falta} sem` : ' · completo ✓');
    box.className = 'cob ' + (falta ? 'parcial' : 'ok');
  } catch(e){ box.textContent = 'falha ao consultar cobertura'; box.className='cob'; }
}

$('#serie').addEventListener('change', () => cobertura($('#serie').value));
$('#cob').onclick = () => cobertura($('#serie').value);
$('#ir').onclick = () => fluxo(`api/processar?serie=${$('#serie').value}&score=${$('#score').value}`);
$('#exp').onclick = () => { if (confirm('Exportar as legendas do banco do Plex para arquivo, via Bazarr?')) fluxo('api/exportar'); };
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    cfg: Config
    plex: Plex

    def log_message(self, *a):  # silencia o log padrão
        pass

    def _envia(self, corpo: bytes, tipo="text/html; charset=utf-8", codigo=200):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):  # noqa: N802
        partes = urllib.parse.urlparse(self.path)
        rota = partes.path.rstrip("/") or "/"
        q = urllib.parse.parse_qs(partes.query)

        if rota == "/":
            html = (PAGINA
                    .replace("__SCORE__", str(self.cfg.score_min))
                    .replace("__EXPD__", "" if self.cfg.tem_bazarr else "disabled")
                    .replace("__EXPT__", "Envia ao Bazarr, que grava o arquivo"
                             if self.cfg.tem_bazarr else
                             "Defina BAZARR_URL e BAZARR_APIKEY para habilitar"))
            return self._envia(html.encode())

        if rota == "/api/series":
            dados = [s.__dict__ for s in self.plex.series()]
            return self._envia(json.dumps(dados).encode(), "application/json")

        if rota == "/api/processar":
            return self._fluxo_processar(q)

        if rota == "/api/exportar":
            return self._fluxo_exportar()

        if rota == "/api/cobertura":
            return self._fluxo_cobertura(q)

        self._envia(b"nao encontrado", "text/plain", 404)

    # ---------- respostas em fluxo (uma linha JSON por evento) ----------

    def _abre_fluxo(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _emite(self, d: dict):
        self.wfile.write((json.dumps(d, ensure_ascii=False) + "\n").encode())
        self.wfile.flush()

    def _fluxo_processar(self, q):
        serie = (q.get("serie") or [""])[0]
        if not serie:
            return self._envia(b"informe a serie", "text/plain", 400)
        try:
            score = int((q.get("score") or [self.cfg.score_min])[0])
        except ValueError:
            score = self.cfg.score_min
        self._abre_fluxo()
        try:
            for evt in processar_serie(self.plex, serie, self.cfg.idioma, score):
                self._emite(evt)
        except Exception as e:  # noqa: BLE001
            self._emite({"estado": "erro", "detalhe": str(e), "ep": ""})

    def _fluxo_cobertura(self, q):
        from .plex import cobertura_serie
        serie = (q.get("serie") or [""])[0]
        if not serie:
            return self._envia(b"informe a serie", "text/plain", 400)
        self._abre_fluxo()
        try:
            for evt in cobertura_serie(self.plex, serie):
                self._emite(evt)
        except Exception as e:  # noqa: BLE001
            self._emite({"estado": "erro", "detalhe": str(e), "ep": ""})

    def _fluxo_exportar(self):
        if not self.cfg.tem_bazarr:
            return self._envia(b"bazarr nao configurado", "text/plain", 400)
        from .exportar import AcessoPlexDB, Bazarr, exportar
        import os
        self._abre_fluxo()
        try:
            db = AcessoPlexDB(
                dir_bancos=os.environ["PLEX_DB_DIR"],
                sqlite_bin=os.environ.get(
                    "PLEX_SQLITE", "/usr/lib/plexmediaserver/Plex SQLite"),
                ssh_destino=os.environ.get("PLEX_SSH") or None,
                prefixo=(os.environ.get("PLEX_CMD_PREFIXO") or "").split() or None,
            )
            bz = Bazarr(self.cfg.bazarr_url, self.cfg.bazarr_key)
            for i, evt in enumerate(exportar(db, bz, self.cfg.idioma), 1):
                self._emite({**evt, "ep": str(i),
                             "estado": {"enviada": "baixada"}.get(evt["estado"], evt["estado"])})
        except KeyError as e:
            self._emite({"estado": "erro", "ep": "", "detalhe": f"variável {e} não definida"})
        except Exception as e:  # noqa: BLE001
            self._emite({"estado": "erro", "ep": "", "detalhe": str(e)})


def servir(cfg: Config) -> None:
    Handler.cfg = cfg
    Handler.plex = Plex(cfg.plex_url, cfg.plex_token)
    srv = ThreadingHTTPServer(("0.0.0.0", cfg.porta), Handler)
    print(f"→ http://localhost:{cfg.porta}   (Plex em {cfg.plex_url})")
    if not cfg.tem_bazarr:
        print("  exportação desabilitada: defina BAZARR_URL e BAZARR_APIKEY")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrado")
