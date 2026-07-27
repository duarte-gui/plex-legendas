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
  .l { display:flex; gap:.7rem; padding:.32rem .7rem; border-bottom:1px solid var(--linha); align-items:baseline }
  .l:last-child { border-bottom:0 }
  .l .ep { min-width:3.4rem; color:var(--tenue); font-variant-numeric:tabular-nums }
  .l .tit { min-width:11rem; flex:0 1 15rem; color:var(--tinta); overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .l .st { min-width:6.5rem; font-weight:600 }
  .l .de { color:var(--tenue); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1 1 8rem }
  .season { position:sticky; top:0; background:var(--papel); font-weight:700; font-size:.82rem;
            letter-spacing:.03em; padding:.4rem .7rem; border-bottom:2px solid var(--acento); color:var(--acento) }
  .badge-en { display:inline-block; font-size:.62rem; font-weight:700; letter-spacing:.05em;
              padding:.02rem .28rem; margin-left:.35rem; border:1px solid var(--acento);
              color:var(--acento); border-radius:2px; vertical-align:middle }
  .fonte { display:inline-block; font-size:.66rem; font-weight:700; margin-left:.4rem;
           padding:.02rem .3rem; border-radius:3px; vertical-align:middle }
  .fonte.ok { color:var(--ok); border:1px solid var(--ok) }
  .fonte.no { color:#fff; background:#c0392b }
  .fonte.tem { color:var(--tenue); border:1px solid var(--linha) }
  .grade { display:flex; flex-direction:column; gap:.4rem }
  .card { display:flex; align-items:center; gap:.8rem; padding:.6rem .8rem; background:var(--papel);
          border:1px solid var(--linha); border-left:4px solid var(--linha); cursor:pointer }
  .card:hover { border-left-color:var(--acento) }
  .card.tem { border-left-color:var(--ok) }
  .card.fraca { border-left-color:var(--alerta) }
  .card .cnum { font-family:var(--mono); font-weight:700; color:var(--tenue); min-width:2.6rem }
  .card .cinfo { flex:1; min-width:0 }
  .card .ctit { font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .card .csub { font-size:.78rem; color:var(--tenue); margin-top:.1rem }
  .pill { display:inline-block; font-size:.68rem; font-weight:600; padding:.08rem .4rem;
          border-radius:3px; margin-right:.3rem }
  .pill.ok { background:var(--ok); color:#fff } .pill.no { background:var(--linha); color:var(--suave) }
  .pill.wa { background:var(--alerta); color:#fff }
  .card .cgo { color:var(--tenue); font-size:1.1rem }
  .baixada .st { color:var(--ok) } .score_baixo .st { color:var(--alerta) }
  .erro .st,.sem_candidato .st { color:var(--erro) }
  .ja_tinha { opacity:.55 }
  .cabec { display:flex; gap:.8rem; align-items:center }
  .capa { width:52px; height:78px; object-fit:cover; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.35); flex:0 0 auto }
  .cob { font-size:.9rem; color:var(--suave); min-height:1.3em; padding:.2rem 0 }
  .cob.ativo { color:var(--tenue) }
  .cob.ok { color:var(--ok); font-weight:600 }
  .cob.parcial { color:var(--alerta); font-weight:600 }
  .l { cursor:pointer } .l:hover { background:var(--papel) }
  .modal { position:fixed; inset:0; background:rgba(0,0,0,.5); display:flex;
           align-items:center; justify-content:center; padding:1rem; z-index:10 }
  .modal[hidden] { display:none }
  .modal-cx { background:var(--fundo); border:1px solid var(--linha); max-width:680px;
              width:100%; max-height:82vh; overflow:auto; padding:1.1rem }
  .modal-hd { display:flex; justify-content:space-between; align-items:center;
              font-weight:700; margin-bottom:.5rem }
  .modal-arq { font-family:var(--mono); font-size:.78rem; color:var(--tenue); margin-bottom:.8rem;
               word-break:break-all }
  .cand { display:flex; gap:.7rem; align-items:center; padding:.5rem .3rem;
          border-bottom:1px solid var(--linha) }
  .cand .info { flex:1; min-width:0 }
  .cand .rel { font-size:.85rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .cand .met { font-family:var(--mono); font-size:.72rem; color:var(--tenue) }
  .cand .met b { color:var(--ok) }
  .cand.outra { opacity:.7 } .cand.outra .rel { color:var(--tenue) }
  .cand .warn { color:var(--erro); font-weight:600 }
  .modal-nav { display:flex; align-items:center; gap:.4rem }
  .modal-nav button { padding:.35rem .7rem; font-size:.82rem }
  #modal-pos { font-size:.8rem; color:var(--tenue); min-width:4.5rem; text-align:center }
  .modal-auto { display:flex; align-items:center; gap:.4rem; margin-top:.9rem;
                font-size:.82rem; text-transform:none; letter-spacing:0; color:var(--suave) }
  .modal-auto input { min-width:auto }
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
    <label>Idioma
      <select id="idioma"><option>…</option></select>
    </label>
    <label>Biblioteca
      <select id="lib"><option>…</option></select>
    </label>
    <label>Série
      <select id="serie"><option>carregando…</option></select>
    </label>
    <label id="lbl-temp">Temporada
      <select id="temp"><option>—</option></select>
    </label>
    <label>Score mínimo
      <input id="score" type="number" value="__SCORE__" min="0" step="100">
    </label>
    <button id="ir">Buscar nesta temporada</button>
    <button id="fonte" class="sec" title="Verifica, por episódio, se há inglês (embutido ou online) para traduzir — ou se só o Whisper resolve">Checar inglês</button>
    <button id="exp" class="sec" title="__EXPT__" __EXPD__>Exportar para arquivo</button>
  </div>

  <div class="cabec">
    <img id="capa" class="capa" alt="" hidden>
    <div id="resumo-cob" class="cob"></div>
  </div>
  <div class="prog"><i id="pi"></i></div>

  <div id="grade" class="grade"></div>

  <div id="modal" class="modal" hidden>
    <div class="modal-cx">
      <div class="modal-hd">
        <span id="modal-tit">Escolher legenda</span>
        <span class="modal-nav">
          <button id="modal-prev" class="sec">‹ anterior</button>
          <span id="modal-pos"></span>
          <button id="modal-next" class="sec">próximo ›</button>
          <button id="modal-x" class="sec">fechar</button>
        </span>
      </div>
      <div id="modal-arq" class="modal-arq"></div>
      <div id="modal-lista"></div>
      <label class="modal-auto"><input type="checkbox" id="modal-avanca" checked>
        avançar para o próximo ao aplicar</label>
    </div>
  </div>

  <footer>
    Score alto indica que a legenda corresponde ao mesmo release do arquivo — daí o
    sincronismo vir correto. Abaixo do mínimo o candidato é descartado em vez de
    aplicado, evitando legenda de outra obra. Veja o <code>README</code> para detalhes.
  </footer>
</div>

<script>
const $ = s => document.querySelector(s);
const MAPA_IDIOMA = {english:'EN', portugu:'PT', 'español':'ES', espanol:'ES', spanish:'ES',
  français:'FR', francais:'FR', french:'FR', deutsch:'DE', german:'DE', italiano:'IT',
  italian:'IT', japan:'JA', chinese:'ZH', 'português (brasil)':'PT-BR'};
function codigoIdioma(lang){
  const l=(lang||'').toLowerCase();
  for(const k in MAPA_IDIOMA) if(l.includes(k)) return MAPA_IDIOMA[k];
  return (lang||'??').slice(0,2).toUpperCase();
}

// idioma-alvo (código Plex) e nome de exibição, escolhidos no dropdown
let idiomaAlvo='pt-BR', idiomaNome='Português (BR)';
const qi=()=>'&idioma='+encodeURIComponent(idiomaAlvo);

// ---- 0a) idiomas (alvo da legenda) ----
fetch('api/idiomas').then(r=>r.json()).then(d=>{
  const sel=$('#idioma'); sel.innerHTML='';
  for(const l of d.idiomas){ const o=document.createElement('option');
    o.value=l.code; o.textContent=l.nome; sel.appendChild(o); }
  idiomaAlvo=d.padrao||'pt-BR'; sel.value=idiomaAlvo;
  idiomaNome=(d.idiomas.find(l=>l.code===idiomaAlvo)||{}).nome||idiomaAlvo;
}).catch(()=> $('#idioma').innerHTML='<option>falha</option>');

// ---- 0b) bibliotecas (Séries / Animes / Filmes) ----
fetch('api/bibliotecas').then(r=>r.json()).then(ls=>{
  const sel=$('#lib'); sel.innerHTML='';
  for(const l of ls){ const o=document.createElement('option');
    o.value=l.key; o.dataset.tipo=l.tipo; o.textContent=l.titulo; sel.appendChild(o); }
  if(ls.length) carregarSeries(sel.value);
}).catch(()=> $('#lib').innerHTML='<option>falha</option>');

// tipo (show/movie) da série selecionada — governa se há temporada
function serieTipo(){ const o=$('#serie').selectedOptions[0]; return o?o.dataset.tipo:'show'; }

// ---- 1) séries da biblioteca escolhida ----
function carregarSeries(lib){
  const sel=$('#serie'); sel.innerHTML='<option>carregando…</option>';
  fetch('api/series?lib='+encodeURIComponent(lib)).then(r=>r.json()).then(ss=>{
    sel.innerHTML='';
    for(const s of ss){ const o=document.createElement('option');
      o.value=s.rating_key; o.dataset.tipo=s.tipo||'show'; o.textContent=s.titulo;
      sel.appendChild(o); }
    if(ss.length) carregarTemporadas(sel.value);
    else { $('#serie').innerHTML='<option>vazia</option>'; $('#grade').innerHTML=''; }
  }).catch(()=> sel.innerHTML='<option>falha ao consultar o Plex</option>');
}

// ---- 2) temporadas da série (filme não tem: vai direto ao item) ----
async function carregarTemporadas(serie){
  const filme=serieTipo()==='movie';
  $('#lbl-temp').style.display=filme?'none':'';
  $('#ir').style.display=filme?'none':'';   // busca em lote é por temporada
  $('#grade').innerHTML=''; $('#resumo-cob').textContent='';
  if(filme){ carregarEpisodios(); return; }
  const sel=$('#temp'); sel.innerHTML='<option>…</option>';
  try{
    const ts = await (await fetch(`api/temporadas?serie=${serie}`)).json();
    sel.innerHTML='';
    for(const t of ts){ const o=document.createElement('option'); o.value=t; o.textContent='Temporada '+t; sel.appendChild(o); }
    if(ts.length) carregarEpisodios();
    else $('#grade').innerHTML='<p style="color:var(--tenue)">Série sem temporadas numeradas.</p>';
  }catch(e){ sel.innerHTML='<option>falha</option>'; }
}

// ---- 3) episódios da temporada (lista interativa) ----
let epToken=0;
let episodiosAtuais=[];   // ordem da temporada, para navegar no seletor
async function carregarEpisodios(){
  const serie=$('#serie').value, temp=$('#temp').value, filme=serieTipo()==='movie';
  const capa=$('#capa');
  if(serie){ capa.hidden=false; capa.src='api/capa?rk='+encodeURIComponent(serie);
             capa.onerror=()=>{capa.hidden=true;}; }
  else capa.hidden=true;
  if(!serie || (!filme && !temp)) return;
  const meu=++epToken;
  const grade=$('#grade'); grade.innerHTML=''; episodiosAtuais=[];
  const box=$('#resumo-cob'); box.className='cob ativo'; box.textContent=filme?'carregando filme…':'carregando episódios…';
  let com=0, total=0; const embSet=new Set();
  try{
    const url=(filme?`api/episodios?serie=${serie}&tipo=movie`:`api/episodios?serie=${serie}&temporada=${temp}`)+qi();
    const r=await fetch(url);
    const leitor=r.body.getReader(); const dec=new TextDecoder(); let resto='';
    while(true){
      const {done,value}=await leitor.read(); if(done) break;
      if(meu!==epToken){ leitor.cancel(); return; }
      resto+=dec.decode(value,{stream:true});
      const partes=resto.split('\\n'); resto=partes.pop();
      for(const p of partes) if(p.trim()){ try{ const d=JSON.parse(p);
        total=d.total; if(d.tem_pt) com++; (d.emb_langs||[]).forEach(l=>embSet.add(codigoIdioma(l)));
        const card=cardEpisodio(d); grade.appendChild(card);
        episodiosAtuais.push({rk:d.rk, ep:d.ep, card}); $('#pi').style.width=(100*d.i/d.total)+'%';
      }catch(e){} }
    }
    box.className='cob '+(com<total?'parcial':'ok');
    const embs=[...embSet].sort();
    box.textContent=(filme?`${com} de ${total} com legenda em ${idiomaNome}`:`Temporada ${temp}: ${com} de ${total} com legenda em ${idiomaNome}`)
      +(com<total?` · ${total-com} sem`:' · completa ✓')
      +(embs.length?` · legenda embutida no arquivo: ${embs.join(', ')}`:'');
    $('#pi').style.width='0';
  }catch(e){ box.textContent='falha ao carregar episódios'; box.className='cob'; }
}

function cardEpisodio(d){
  // "fraca" só quando a legenda veio de PROVEDOR e casa mal (afinidade baixa);
  // sidecar nossa (afinidade 0 por não ter release no título) conta como boa.
  const fraca = d.tem_pt && d.de_provedor && d.afinidade < 2;
  const el=document.createElement('div');
  el.className='card'+(d.tem_pt?(fraca?' fraca':' tem'):'');
  el.dataset.rk=d.rk; el.dataset.ep=d.ep;
  let sub;
  if(!d.tem_pt) sub='<span class="pill no">sem legenda</span>clique para escolher';
  else if(fraca) sub=`<span class="pill wa">legenda pt · afinidade ${d.afinidade}</span>pode não casar — clique para trocar`;
  else if(d.de_provedor) sub=`<span class="pill ok">legenda pt · afinidade ${d.afinidade}</span>`;
  else sub='<span class="pill ok">legenda pt (arquivo)</span>';
  el.innerHTML=`<div class="cnum">E${String(d.numero).padStart(2,'0')}</div>`
    +`<div class="cinfo"><div class="ctit"></div><div class="csub">${sub}</div></div>`
    +`<div class="cgo">›</div>`;
  el.querySelector('.ctit').textContent=d.titulo||d.ep;
  const ctit=el.querySelector('.ctit');
  for(const lang of (d.emb_langs||[])){
    const s=document.createElement('span'); s.className='badge-en'; s.textContent=codigoIdioma(lang);
    s.title='legenda embutida no arquivo: '+lang; ctit.appendChild(s);
  }
  el.onclick=()=>abrirIdx(episodiosAtuais.findIndex(x=>x.rk===d.rk));
  return el;
}

// ---- seletor manual, com navegação episódio a episódio ----
let idxAtual=-1;
function abrirIdx(idx){
  if(idx<0 || idx>=episodiosAtuais.length) return;
  idxAtual=idx;
  const e=episodiosAtuais[idx];
  $('#modal-pos').textContent=`${idx+1} de ${episodiosAtuais.length}`;
  $('#modal-prev').disabled = idx===0;
  $('#modal-next').disabled = idx===episodiosAtuais.length-1;
  $('#modal-tit').textContent='Legenda · '+e.ep;
  $('#modal-arq').textContent='consultando candidatos…';
  $('#modal-lista').innerHTML=''; $('#modal').hidden=false;
  carregarCandidatos(e);
}
async function carregarCandidatos(e){
  try{
    const d=await (await fetch(`api/candidatos?ep=${e.rk}&serie=${$('#serie').value}`+qi())).json();
    if(e!==episodiosAtuais[idxAtual]) return;   // já navegou para outro
    $('#modal-arq').textContent=d.arquivo||'';
    const lista=$('#modal-lista'); lista.innerHTML='';
    if(!d.candidatos.length){ lista.innerHTML='<p>Nenhum candidato para este episódio.</p>'; return; }
    for(const c of d.candidatos){
      const row=document.createElement('div'); row.className='cand'+(c.mesma_serie?'':' outra');
      const aviso=c.mesma_serie?'':' <span class="warn">⚠ outra série</span>';
      row.innerHTML=`<div class="info"><div class="rel"></div>`
        +`<div class="met">afinidade <b>${c.afinidade}</b> · score ${c.score} · ${c.provedor||''}${aviso}</div></div>`
        +`<button class="apl">Aplicar</button>`;
      row.querySelector('.rel').textContent=c.titulo;
      const btn=row.querySelector('.apl');
      btn.onclick=async()=>{
        btn.textContent='aplicando…'; btn.disabled=true;
        try{
          const rr=await fetch(`api/aplicar?ep=${e.rk}&stream=${encodeURIComponent(c.stream)}`+qi());
          const jj=await rr.json();
          if(jj.ok){ atualizarCard(e.card,c);
            if($('#modal-avanca').checked && idxAtual<episodiosAtuais.length-1) abrirIdx(idxAtual+1);
            else $('#modal').hidden=true;
          } else { btn.textContent='falhou'; btn.disabled=false; }
        }catch(err){ btn.textContent='erro'; btn.disabled=false; }
      };
      lista.appendChild(row);
    }
  }catch(e2){ $('#modal-arq').textContent='falha ao consultar candidatos'; }
}
function atualizarCard(card,c){
  if(!card) return;
  card.className='card '+(c.afinidade>=2?'tem':'fraca');
  card.querySelector('.csub').innerHTML=`<span class="pill ${c.afinidade>=2?'ok':'wa'}">aplicada · afinidade ${c.afinidade}</span>${c.titulo}`;
}
$('#modal-prev').onclick=()=>abrirIdx(idxAtual-1);
$('#modal-next').onclick=()=>abrirIdx(idxAtual+1);
$('#modal-x').onclick=()=>$('#modal').hidden=true;
$('#modal').addEventListener('click',e=>{ if(e.target.id==='modal') $('#modal').hidden=true; });
document.addEventListener('keydown',e=>{ if($('#modal').hidden) return;
  if(e.key==='ArrowRight') abrirIdx(idxAtual+1);
  else if(e.key==='ArrowLeft') abrirIdx(idxAtual-1);
  else if(e.key==='Escape') $('#modal').hidden=true; });

// ---- buscar (automático) na temporada selecionada ----
function marcaCard(card, d){
  if(!card) return;
  const sub=card.querySelector('.csub');
  if(d.estado==='baixada'){ atualizarCard(card,{afinidade:d.afinidade??0,titulo:d.release||''}); }
  else if(d.estado==='score_baixo'){ card.className='card fraca';
    sub.innerHTML=`<span class="pill wa">nada bom</span>melhor score ${d.score} — abaixo do mínimo · clique para ver`; }
  else if(d.estado==='sem_candidato'){ card.className='card';
    sub.innerHTML='<span class="pill no">sem candidato</span>nenhuma legenda encontrada'; }
}
async function buscarTemporada(){
  const serie=$('#serie').value, temp=$('#temp').value, score=$('#score').value;
  $('#ir').disabled=true; $('#ir').textContent='buscando…';
  const box=$('#resumo-cob'); box.className='cob ativo';
  const c={baixada:0,ja_tinha:0,ja_otima:0,score_baixo:0,sem_candidato:0,erro:0};
  try{
    const r=await fetch(`api/processar?serie=${serie}&temporada=${temp}&score=${score}`+qi());
    const leitor=r.body.getReader(); const dec=new TextDecoder(); let resto='';
    while(true){
      const {done,value}=await leitor.read(); if(done) break;
      resto+=dec.decode(value,{stream:true});
      const partes=resto.split('\\n'); resto=partes.pop();
      for(const p of partes) if(p.trim()){ try{ const d=JSON.parse(p);
        if(d.estado in c) c[d.estado]++;
        if(d.total){ $('#pi').style.width=(100*d.i/d.total)+'%'; box.textContent=`buscando… ${d.i}/${d.total}`; }
        marcaCard(document.querySelector(`.card[data-rk="${d.rk}"]`), d);
      }catch(e){} }
    }
    $('#pi').style.width='0';
    box.className='cob '+(c.baixada?'ok':'parcial');
    let msg=`${c.baixada} baixada(s)`;
    if(c.ja_tinha) msg+=` · ${c.ja_tinha} já tinham`;
    if(c.score_baixo) msg+=` · ${c.score_baixo} sem legenda boa (score < ${score})`;
    if(c.sem_candidato) msg+=` · ${c.sem_candidato} sem candidato`;
    if(!c.baixada && c.score_baixo) msg+=' — abaixe o Score mínimo ou clique num episódio para escolher à mão';
    box.textContent=msg;
  } finally { $('#ir').disabled=false; $('#ir').textContent='Buscar nesta temporada'; }
}

// ---- checar fonte de tradução (inglês embutido/online/nenhum) ----
function marcaFonte(rk, classe){
  const card=document.querySelector(`.card[data-rk="${rk}"]`); if(!card) return;
  const ctit=card.querySelector('.ctit'); const velho=ctit.querySelector('.fonte'); if(velho) velho.remove();
  const M={emb:['inglês embutido','ok'],online:['inglês online','ok'],sem:['sem inglês · Whisper','no'],tem:['',''] };
  const [txt,cls]=M[classe]||['',''];
  if(!txt) return;
  const b=document.createElement('span'); b.className='fonte '+cls; b.textContent=txt;
  b.title = classe==='sem' ? 'Nenhuma legenda em inglês (embutida ou online) — só Whisper resolve'
          : 'Dá para traduzir a partir do inglês ('+(classe==='emb'?'embutido no arquivo':'achável online')+')';
  ctit.appendChild(b);
}
async function checarFonte(){
  const serie=$('#serie').value, temp=$('#temp').value;
  if(!serie||!temp){ return; }
  $('#fonte').disabled=true; $('#fonte').textContent='checando…';
  const box=$('#resumo-cob'); box.className='cob ativo'; box.textContent='checando inglês…';
  const c={tem:0,emb:0,online:0,sem:0};
  try{
    const r=await fetch(`api/fonte?serie=${serie}&temporada=${temp}`+qi());
    const leitor=r.body.getReader(); const dec=new TextDecoder(); let resto='';
    while(true){
      const {done,value}=await leitor.read(); if(done) break;
      resto+=dec.decode(value,{stream:true});
      const partes=resto.split('\\n'); resto=partes.pop();
      for(const p of partes) if(p.trim()){ try{ const d=JSON.parse(p);
        if(d.classe in c) c[d.classe]++;
        marcaFonte(d.rk,d.classe);
        if(d.total){ $('#pi').style.width=(100*d.i/d.total)+'%'; box.textContent=`checando inglês… ${d.i}/${d.total}`; }
      }catch(e){} }
    }
    $('#pi').style.width='0';
    const trad=c.emb+c.online;
    box.className='cob '+(c.sem?'parcial':'ok');
    box.textContent=`${trad} traduzível (${c.emb} embutido + ${c.online} online) · ${c.sem} sem inglês (só Whisper) · ${c.tem} já têm ${idiomaNome}`;
  } finally { $('#fonte').disabled=false; $('#fonte').textContent='Checar inglês'; }
}
$('#fonte').onclick=checarFonte;

$('#idioma').addEventListener('change',()=>{ idiomaAlvo=$('#idioma').value;
  idiomaNome=$('#idioma').selectedOptions[0].textContent; carregarEpisodios(); });
$('#lib').addEventListener('change',()=>carregarSeries($('#lib').value));
$('#serie').addEventListener('change',()=>carregarTemporadas($('#serie').value));
$('#temp').addEventListener('change',carregarEpisodios);
$('#ir').onclick=buscarTemporada;
$('#exp').onclick=async()=>{ if(!confirm('Exportar as legendas do banco do Plex para arquivo, via Bazarr?')) return;
  const r=await fetch('api/exportar'); await r.text(); alert('Exportação concluída.'); };
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

        if rota == "/api/idiomas":
            from .plex import IDIOMAS
            libs = [{"code": c, "nome": v["nome"]} for c, v in IDIOMAS.items()]
            return self._envia(json.dumps({"idiomas": libs, "padrao": self.cfg.idioma}).encode(),
                               "application/json")

        if rota == "/api/bibliotecas":
            libs = [{"key": k, "tipo": t, "titulo": nome}
                    for k, t, nome in self.plex.bibliotecas(("show", "movie"))]
            # séries/animes primeiro, filmes depois; alfabético dentro de cada grupo
            libs.sort(key=lambda l: (l["tipo"] != "show", l["titulo"].lower()))
            return self._envia(json.dumps(libs).encode(), "application/json")

        if rota == "/api/series":
            lib = (q.get("lib") or [""])[0] or None
            dados = [s.__dict__ for s in self.plex.series(lib)]
            return self._envia(json.dumps(dados).encode(), "application/json")

        if rota == "/api/processar":
            return self._fluxo_processar(q)

        if rota == "/api/exportar":
            return self._fluxo_exportar()

        if rota == "/api/cobertura":
            return self._fluxo_cobertura(q)

        if rota == "/api/temporadas":
            serie = (q.get("serie") or [""])[0]
            if not serie:
                return self._envia(b"informe a serie", "text/plain", 400)
            from .plex import temporadas
            self._envia(json.dumps(temporadas(self.plex, serie)).encode(), "application/json")
            return

        if rota == "/api/episodios":
            return self._fluxo_episodios(q)

        if rota == "/api/fonte":
            return self._fluxo_fonte(q)

        if rota == "/api/capa":
            rk = (q.get("rk") or [""])[0]
            img = self.plex.capa(rk) if rk else None
            if not img:
                return self._envia(b"sem capa", "text/plain", 404)
            self._envia(img[0], img[1])
            return

        if rota == "/api/candidatos":
            return self._candidatos(q)

        if rota == "/api/aplicar":
            return self._aplicar(q)

        self._envia(b"nao encontrado", "text/plain", 404)

    def _fluxo_episodios(self, q):
        from .plex import episodios_status, filme_status
        serie = (q.get("serie") or [""])[0]
        temp = (q.get("temporada") or [""])[0]
        tipo = (q.get("tipo") or [""])[0]
        if not serie or (tipo != "movie" and not temp):
            return self._envia(b"informe serie e temporada", "text/plain", 400)
        idioma = self._idioma(q)
        self._abre_fluxo()
        try:
            eventos = (filme_status(self.plex, serie, idioma) if tipo == "movie"
                       else episodios_status(self.plex, serie, temp, idioma))
            for evt in eventos:
                self._emite(evt)
        except Exception as e:  # noqa: BLE001
            self._emite({"erro": str(e)})

    def _idioma(self, q):
        """Idioma-alvo do request (query `idioma`), com o do .env como padrão."""
        from .plex import IDIOMAS
        cod = (q.get("idioma") or [""])[0]
        return cod if cod in IDIOMAS else self.cfg.idioma

    def _fluxo_fonte(self, q):
        from .plex import fonte_traducao
        serie = (q.get("serie") or [""])[0]
        temp = (q.get("temporada") or [""])[0]
        if not serie or not temp:
            return self._envia(b"informe serie e temporada", "text/plain", 400)
        tks = self.plex.tokens_serie(serie)
        self._abre_fluxo()
        try:
            for evt in fonte_traducao(self.plex, serie, temp, self._idioma(q), tks):
                self._emite(evt)
        except Exception as e:  # noqa: BLE001
            self._emite({"erro": str(e)})

    def _candidatos(self, q):
        """Lista os candidatos de um episódio, para escolha manual no painel."""
        rk = (q.get("ep") or [""])[0]
        if not rk:
            return self._envia(b"informe o ep", "text/plain", 400)
        arquivo = self.plex.arquivo_do_episodio(rk)
        serie = (q.get("serie") or [""])[0]
        tks = self.plex.tokens_serie(serie) if serie else None
        # sem filtro de série: mostra tudo, marcando os divergentes, para o
        # usuário escolher no caso de o nome divergir.
        cands = self.plex.buscar(rk, self._idioma(q), arquivo, filtrar_serie=False,
                                 tokens_serie=tks)
        dados = [{"stream": c.stream_id, "titulo": c.titulo, "score": c.score,
                  "afinidade": c.afinidade, "provedor": c.provedor,
                  "mesma_serie": c.mesma_serie} for c in cands]
        self._envia(json.dumps({"arquivo": arquivo, "candidatos": dados}).encode(),
                    "application/json")

    def _aplicar(self, q):
        """Baixa e aplica um candidato específico escolhido pelo usuário."""
        rk = (q.get("ep") or [""])[0]
        sid = (q.get("stream") or [""])[0]
        if not rk or not sid:
            return self._envia(b"informe ep e stream", "text/plain", 400)
        try:
            self.plex.aplicar(rk, sid)
            self._envia(b'{"ok":true}', "application/json")
        except Exception as e:  # noqa: BLE001
            self._envia(json.dumps({"ok": False, "erro": str(e)}).encode(),
                        "application/json", 500)

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
        temp = (q.get("temporada") or [""])[0]
        self._abre_fluxo()
        try:
            for evt in processar_serie(self.plex, serie, self._idioma(q), score,
                                       temporada=temp):
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
            for evt in cobertura_serie(self.plex, serie, self._idioma(q)):
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
