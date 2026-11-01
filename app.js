let facilities = [];
let loadedPrefecture = '';
let designationIndex = [];
const cache = {};
const $ = id => document.getElementById(id);

function norm(s) {
  return String(s || '').normalize('NFKC').replace(/[\s　〒、,，・（）()‐‑‒–—-]/g, '').toLowerCase();
}
function esc(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function compatible(a,b) {
  if (!a || !b) return true;
  const x=String(a), y=String(b);
  if(x==='薬局'||y==='薬局') return x==='薬局'&&y==='薬局';
  if(x.includes('歯科')||y.includes('歯科')) return x.includes('歯科')&&y.includes('歯科');
  return !x.includes('薬局')&&!y.includes('薬局')&&!x.includes('歯科')&&!y.includes('歯科');
}
function autoMatch(x) {
  const code=norm(x.id||x.code||'');
  let hits=code?designationIndex.filter(d=>norm(d.code)===code&&compatible(x.kind,d.kind)):[];
  if(hits.length===1) return {status:'confirmed',authority:hits[0].authority,source:hits[0].source_page,method:'医療機関コード完全一致'};
  if(hits.length>1) return {status:'candidate',note:'コードが複数の名簿レコードに一致しました'};
  const name=norm(x.name), addr=norm(x.address);
  if(!name) return {status:'unknown'};
  const sameName=designationIndex.filter(d=>norm(d.name)===name&&compatible(x.kind,d.kind));
  const exact=sameName.filter(d=>addr&&norm(d.address)===addr);
  if(exact.length===1) return {status:'confirmed',authority:exact[0].authority,source:exact[0].source_page,method:'施設名・住所完全一致'};
  if(exact.length>1) return {status:'candidate',note:'同一名称・住所の名簿が複数あります'};
  if(sameName.length) return {status:'candidate',note:'同名施設がありますが住所が一致しません'};
  return {status:'unknown'};
}
async function loadDesignation() {
  if(designationIndex.length) return;
  const r=await fetch('data/designation-index.json');
  if(!r.ok) throw new Error('指定名簿を取得できません');
  designationIndex=await r.json();
}
async function loadPrefecture(code) {
  if(cache[code]) {
    facilities=cache[code];
    loadedPrefecture=code;
    return;
  }
  $('status').textContent='施設データを読み込んでいます…';
  const r=await fetch(`data/facilities/${code}.json`);
  if(!r.ok) throw new Error('施設データを取得できません');
  const data=await r.json();
  cache[code]=data;
  facilities=data;
  loadedPrefecture=code;
}
async function search(e) {
  e.preventDefault();
  const code=$('prefecture').value;
  const area=norm($('area').value);
  const dept=norm($('dept').value);
  const kind=$('kind').value;
  if(!code) {
    $('status').textContent='都道府県を選択してください。';
    $('results').innerHTML='';
    return;
  }
  try {
    await loadPrefecture(code);
    await loadDesignation();
    const found=facilities.filter(x=>
      (!area||norm(x.address).includes(area)||norm(x.city).includes(area)) &&
      (kind==='all'||x.kind===kind||(kind==='診療所'&&x.kind==='診療所・医科')) &&
      (!dept||norm(x.departments).includes(dept)||norm(x.name).includes(dept))
    ).slice(0,100);
    $('status').textContent=`検索結果：${found.length}件（最大100件表示）。${facilities.length.toLocaleString()}件の地域データを対象に検索しました。`;
    $('results').innerHTML=found.length?found.map(card).join(''):'<div class="empty">該当する施設が見つかりません。</div>';
  } catch(err) {
    $('status').textContent='データの読み込みに失敗しました。時間をおいて再度お試しください。';
    $('results').innerHTML='';
    console.error(err);
  }
}
function card(x) {
  const aid=x.aid_status==='参加確認済み'
    ?'<span class="badge ok">医療扶助オンライン資格確認：参加確認済み</span>'
    :x.aid_status==='要確認'
    ?'<span class="badge warn">医療扶助オンライン資格確認：要確認</span>'
    :'<span class="badge warn">医療扶助オンライン資格確認：未確認</span>';
  const m=autoMatch(x);
  const designation=m.status==='confirmed'
    ?'<span class="badge ok">生活保護法の指定：公式名簿と一致</span>'
    :m.status==='candidate'
    ?'<span class="badge warn">生活保護法の指定：候補・要確認</span>'
    :'<span class="badge warn">生活保護法の指定：未確認</span>';
  const q=encodeURIComponent(x.name+' '+x.address);
  return `<article class="result"><h3>${esc(x.name)}</h3><div class="badges"><span class="badge">${esc(x.kind)}</span>${aid}${designation}</div><div class="meta">住所：${esc(x.address)}<br>診療科・業務：${esc(x.departments||'公開データに記載なし')}<br>電話：${esc(x.phone||'公開データに記載なし')}<br>診療時間：${esc(x.hours||'公開データに記載なし')}<br>情報基準日：${esc(x.source_as_of||'不明')}<br>指定照合：${esc(m.method||m.note||'公式名簿に一致するデータなし（未確認）')}<br>${m.authority?'管轄：'+esc(m.authority):''}</div>${m.source?`<div class="links"><a target="_blank" rel="noopener" href="${esc(m.source)}">一致した公式指定名簿</a></div>`:''}<div class="links"><a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${q}">地図を開く</a>${x.website?`<a target="_blank" rel="noopener" href="${esc(x.website)}">公式サイト</a>`:''}</div></article>`;
}
async function init() {
  try {
    await loadDesignation();
    $('status').textContent='都道府県を選択して検索してください。';
    $('coverage').innerHTML='<strong>自動照合状況</strong><p>公式指定名簿：'+designationIndex.length.toLocaleString()+'件を読み込みました。</p><p>施設データは検索時に選択した都道府県のみ取得します。</p>';
  } catch(e) {
    $('status').textContent='指定名簿を取得できません。データ公開後に再度お試しください。';
  }
}
$('searchForm').addEventListener('submit',search);
init();
