(function(){
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],E=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let sec='全部',q='',tag='',form='全部形式',C=null,page=0;const PER=30;
document.addEventListener('click',e=>{const t=e.target.closest('.chip.tag');if(t){e.preventDefault();tag=t.dataset.tag;page=0;apply();window.scrollTo({top:0,behavior:'smooth'});return}
 const c=e.target.closest('.cite');if(c){e.preventDefault();navigator.clipboard&&navigator.clipboard.writeText(c.dataset.cite).then(()=>{const o=c.textContent;c.textContent='已复制';setTimeout(()=>c.textContent=o,1200)});}});
$$('.tab').forEach(b=>b.onclick=()=>{sec=b.dataset.s;$$('.tab').forEach(x=>x.classList.toggle('on',x===b));page=0;apply()});
const qi=$('#q');if(qi)qi.oninput=()=>{q=qi.value.trim().toLowerCase();page=0;apply()};
const tf=$('#tagf');if(tf)tf.onclick=()=>{tag='';apply()};
const setForm=f=>{form=f;$$('#formBar button').forEach(x=>x.classList.toggle('on',x.dataset.f===f))};
$$('#formBar button').forEach(b=>b.onclick=()=>{setForm(b.dataset.f);page=0;apply()});
const rnd=$('#rnd');if(rnd)rnd.onclick=()=>{const a=$$('#feed article .stretch');if(a.length)window.open(a[Math.floor(Math.random()*a.length)].href,'_blank','noopener')};
const H=new URLSearchParams(location.hash.slice(1));if(H.get('tab')){sec=H.get('tab');$$('.tab').forEach(x=>x.classList.toggle('on',x.dataset.s===sec))}if(H.get('form'))setForm(H.get('form'));
if(H.get('tag'))tag=H.get('tag');if(H.get('q')&&qi){qi.value=H.get('q');q=qi.value.trim().toLowerCase()}
// keep the current filters in the URL hash so a filtered view can be bookmarked or shared
function saveHash(){if(!qi)return;const p=new URLSearchParams();if(sec!=='全部')p.set('tab',sec);if(form!=='全部形式')p.set('form',form);if(tag)p.set('tag',tag);if(qi.value.trim())p.set('q',qi.value.trim());
 const h=p.toString();if(h!==location.hash.slice(1))history.replaceState(null,'',h?'#'+h:location.pathname+location.search)}if(H.get('sub')==='conf'&&$('#feed'))location.replace('papers/');
function match(el){return (sec==='全部'||el.dataset.sec===sec)&&(form==='全部形式'||el.dataset.form===form)&&(!q||el.dataset.text.includes(q))&&(!tag||el.dataset.tags.split('|').includes(tag))}
function apply(){const fw=$('#featWrap');if(fw)fw.hidden=!!(sec!=='全部'||form!=='全部形式'||q||tag);
 if(tf){tf.hidden=!tag;tf.textContent='标签：'+tag+' ✕'}
 const cp=$('#confPanel'),feed=$('#feed');
 const conf=!!cp&&!feed;let n=0;
 if(conf){loadConf();n=1}else{$$('#feed .dayg').forEach(g=>{let k=0;const fv=fw&&!fw.hidden;g.querySelectorAll('article').forEach(a=>{const m=match(a)&&!(fv&&a.dataset.top);a.hidden=!m;k+=m});g.hidden=!k;const s=g.querySelector('.n');if(s)s.textContent=k;n+=k})}
 const em=$('#empty');if(em)em.hidden=!!n;saveHash()}
function loadConf(){if(C){renderConf();return}$('#confPanel').innerHTML='<div class="sk"></div><div class="sk"></div>';
 fetch($('#confPanel').dataset.src||'conf.json').then(r=>r.json()).then(d=>{C=d;const cs=[...new Set(d.map(x=>x.c))].sort(),ys=[...new Set(d.map(x=>String(x.y)))].sort().reverse(),ts=[...new Set(d.flatMap(x=>x.tg))].sort();
  const opt=(id,all,xs)=>`<select id="${id}"><option value="">${all}</option>${xs.map(x=>`<option>${E(x)}</option>`).join('')}</select>`;
  $('#confPanel').innerHTML=`<div class="filters">${opt('fc','全部会议',cs)}${opt('fy','全部年份',ys)}${opt('ft','全部标签',ts)}<span class="muted" id="fn" style="font-size:12px;align-self:center"></span></div><div class="feed" id="cl"></div><div class="pager" id="pg"></div>`;
  ['fc','fy','ft'].forEach(i=>$('#'+i).onchange=()=>{page=0;renderConf()});renderConf()}).catch(()=>{$('#confPanel').innerHTML='<div class="empty">加载失败</div>'})}
// cards are pre-rendered by hub.card() at build time (x.h) — same markup as the daily feed
function renderConf(){const fc=$('#fc').value,fy=$('#fy').value,ft=$('#ft').value||tag;
 const L=C.filter(x=>(sec==='全部'||x.sec===sec)&&(!fc||x.c===fc)&&(!fy||String(x.y)===fy)&&(!ft||x.tg.includes(ft))&&(!q||x.q.includes(q)));
 const P=Math.max(1,Math.ceil(L.length/PER));page=Math.min(page,P-1);$('#fn').textContent=`共 ${L.length} 篇`;
 $('#cl').innerHTML=L.slice(page*PER,(page+1)*PER).map(x=>x.h).join('')||'<div class="empty">没有匹配的论文</div>';
 $('#pg').innerHTML=`<button id="pp" ${page?'':'disabled'}>上一页</button><span class="mono">${page+1} / ${P}</span><button id="pn" ${page<P-1?'':'disabled'}>下一页</button>`;
 $('#pp').onclick=()=>{page--;renderConf();$('#confPanel').scrollIntoView()};$('#pn').onclick=()=>{page++;renderConf();$('#confPanel').scrollIntoView()}}
apply();
})();
