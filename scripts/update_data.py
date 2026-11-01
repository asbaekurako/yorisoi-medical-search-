#!/usr/bin/env python3
"""Download MHLW open data, normalize facility records, and match medical-aid online eligibility.

The MHLW open-data release is periodic (not daily). This job runs daily, checks the
source page, and refreshes data when a new release is published.
"""
import csv, io, json, re, zipfile, hashlib, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'facilities.json'
MED_PAGE='https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/kenkou_iryou/iryou/newpage_43373.html'
AID_PAGE='https://www.mhlw.go.jp/stf/newpage_25108.html'
HEADERS={'User-Agent':'YorisoiMedicalSearch/1.0 (public-data research; contact via repository issues)'}

def get(url):
    r=requests.get(url,headers=HEADERS,timeout=90); r.raise_for_status(); return r

def clean(v): return re.sub(r'\s+',' ',str(v or '')).strip()
def pick(row, patterns):
    for k,v in row.items():
        key=clean(k)
        if any(p in key for p in patterns) and clean(v): return clean(v)
    return ''
def read_csv_bytes(raw):
    # Accept UTF-8 BOM, UTF-8, CP932 and common MHLW ZIP/CSV variants.
    for enc in ('utf-8-sig','cp932','utf-8'):
        try:
            txt=raw.decode(enc)
            sample=txt[:8192]
            try: dialect=csv.Sniffer().sniff(sample, delimiters=',\t;')
            except csv.Error: dialect=csv.excel
            return list(csv.DictReader(io.StringIO(txt),dialect=dialect))
        except UnicodeDecodeError: continue
    return []
def files_from_zip(url):
    raw=get(url).content
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if name.lower().endswith('.csv'):
                yield name,read_csv_bytes(z.read(name))
def find_links(page_url, prefix):
    soup=BeautifulSoup(get(page_url).text,'html.parser')
    out=[]
    for a in soup.find_all('a',href=True):
        href=urljoin(page_url,a['href']); label=clean(a.get_text(' ',strip=True))
        if prefix in href and href.lower().endswith(('.zip','.csv')): out.append((label,href))
    return out



def norm(s): return re.sub(r'[^0-9a-zぁ-んァ-ヶ一-龠]', '', unicodedata.normalize('NFKC', clean(s))).lower()

def header_score(values):
    joined=' '.join(clean(v) for v in values if v is not None)
    return sum(token in joined for token in ('医療機関名','施設名称','病院名','診療所名','歯科','薬局','名称','所在地','住所','指定番号'))

def dict_rows(values, header_index=0):
    values=list(values)
    if not values or header_index >= len(values): return
    headers=[clean(x) for x in values[header_index]]
    if not any(headers): return
    for vals in values[header_index+1:]:
        d={headers[i]: ('' if i>=len(vals) or vals[i] is None else clean(vals[i])) for i in range(len(headers)) if headers[i]}
        if any(d.values()): yield d

def read_xlsx(raw):
    try:
        import openpyxl
        wb=openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        for ws in wb.worksheets:
            sample=list(ws.iter_rows(min_row=1,max_row=min(ws.max_row or 1,12),values_only=True))
            if not sample: continue
            idx=max(range(len(sample)),key=lambda i:header_score(sample[i]))
            if header_score(sample[idx]) < 2: continue
            for row in dict_rows(ws.iter_rows(values_only=True), idx):
                row['__sheet__']=ws.title
                yield row
    except Exception as e: print('XLSX parse error:',e)

def read_pdf(raw):
    # Extract structured tables only; scanned PDFs and free-form lines are never guessed.
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    if not table: continue
                    idx=max(range(len(table)),key=lambda i:header_score(table[i] or []))
                    if header_score(table[idx] or []) >= 2:
                        yield from dict_rows(table, idx)
    except Exception as e: print('PDF parse error:',e)

def designation_rows_from_source(source):
    page=source['page']; soup=BeautifulSoup(get(page).content,'html.parser')
    links=[]
    for a in soup.find_all('a',href=True):
        u=urljoin(page,a['href']); label=clean(a.get_text(' ',strip=True)); low=(u+' '+label).lower()
        if any(x in low for x in ('.xlsx','.xls','.csv','.pdf','.zip')):
            links.append((label,u))
    found=[]
    for label,u in links:
        ext=urlparse(u).path.lower()
        try:
            raw=get(u).content
            if ext.endswith(('.xlsx','.xlsm')): rows=list(read_xlsx(raw))
            elif ext.endswith('.csv'): rows=read_csv_bytes(raw)
            elif ext.endswith('.zip'):
                rows=[]
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for n in z.namelist():
                        if n.lower().endswith('.csv'): rows.extend(read_csv_bytes(z.read(n)))
                        elif n.lower().endswith(('.xlsx','.xlsm')): rows.extend(read_xlsx(z.read(n)))
            elif ext.endswith('.pdf'): rows=list(read_pdf(raw))
            else: continue
            for row in rows:
                # Avoid parsing PDF free text as a confirmed designation without robust tabular extraction.
                if '__pdf_line__' in row: continue
                name=pick(row,['医療機関名','施設名称','病院名','診療所名','歯科診療所名','薬局名','名称'])
                addr=pick(row,['所在地','住所','医療機関所在地'])
                code=pick(row,['医療機関コード','医療機関番号','薬局コード','機関コード'])
                kind=''
                joined=' '.join(row.values())
                if '薬局' in joined or '薬局' in label: kind='薬局'
                elif '歯科' in joined or '歯科' in label: kind='歯科診療所'
                elif '病院' in joined: kind='病院'
                else: kind='診療所・医科'
                if name and (addr or code):
                    found.append({'name':name,'address':addr,'code':code,'kind':kind,'authority':source['authority'],'source_page':page,'source_file':u,'source_label':label,'scope':source['scope']})
        except Exception as e: print('Designation source failed:',source['id'],u,e)
    return found

def main():
    med_links=find_links(MED_PAGE,'/content/')
    # Current MHLW page uses predictable English filenames. Keep facility sheets and specialty/hour sheets.
    records={}; specialty={}; asof='不明'; downloaded=[]
    for label,url in med_links:
        m=re.search(r'(20\d{6})',url)
        if m: asof=m.group(1)
        low=url.lower()
        kind=''
        if 'hospital_facility' in low: kind='病院'
        elif 'clinic_facility' in low: kind='診療所'
        elif 'dental_facility' in low: kind='歯科診療所'
        elif 'pharmacy' in low: kind='薬局'
        elif 'hospital_speciality' in low: kind='病院'
        elif 'clinic_speciality' in low: kind='診療所'
        elif 'dental_speciality' in low: kind='歯科診療所'
        else: continue
        for fname,rows in files_from_zip(url):
            downloaded.append(url)
            for row in rows:
                code=pick(row,['医療機関コード','薬局コード','施設コード','機関コード','医療機関番号'])
                name=pick(row,['施設名称','医療機関名','薬局名','名称'])
                addr=pick(row,['所在地','住所'])
                phone=pick(row,['電話番号','連絡先電話'])
                dept=pick(row,['診療科名','診療科目','標榜科'])
                hours=pick(row,['診療時間','受付時間','開店時間','営業時間'])
                pref=pick(row,['都道府県名','都道府県'])
                city=pick(row,['市区町村名','市区町村'])
                website=pick(row,['ホームページ','URL','ウェブサイト'])
                key=code or norm(name+'|'+addr)
                if not key: continue
                if any(x in low for x in ('speciality','hours')):
                    s=specialty.setdefault(key,{'departments':set(),'hours':set()})
                    if dept:s['departments'].add(dept)
                    if hours:s['hours'].add(hours)
                else:
                    records[key]={'id':key,'name':name,'kind':kind,'address':addr,'prefecture':pref,'city':city,'phone':phone,'departments':dept,'hours':hours,'website':website,'source_as_of':asof,'aid_status':'未確認','designation_status':'unknown','source':'厚生労働省 医療情報ネット オープンデータ'}
    for key,s in specialty.items():
        if key in records:
            if s['departments']: records[key]['departments']='、'.join(sorted(s['departments']))
            if s['hours']: records[key]['hours']='／'.join(sorted(s['hours']))
    # Online eligibility participation list is NOT the same as statutory designation.
    aid_links=find_links(AID_PAGE,'/content/')
    aid_url=next((u for _,u in aid_links if 'csv' in u.lower()),None)
    aid_asof='不明'; aid_rows=[]
    if aid_url:
        raw=get(aid_url).content
        m=re.search(r'(20\d{6})',get(AID_PAGE).text)
        if m: aid_asof=m.group(1)
        aid_rows=read_csv_bytes(raw)
    aid_codes=set(); aid_names=set()
    for row in aid_rows:
        code=pick(row,['医療機関コード','薬局コード','施設コード','機関コード'])
        name=pick(row,['医療機関名','薬局名','施設名称','名称'])
        if code: aid_codes.add(code)
        if name: aid_names.add(norm(name))
    for x in records.values():
        if x['id'] in aid_codes or norm(x['name']) in aid_names:
            x['aid_status']='参加確認済み'
    # Statutory designation sources: separate from the online eligibility participation list.
    registry=json.loads((ROOT/'data'/'designation_sources.json').read_text(encoding='utf-8'))
    designation_rows=[]; source_audits=[]
    for source in registry.get('sources',[]):
        try:
            rows=designation_rows_from_source(source)
            designation_rows.extend(rows)
            source_audits.append({'id':source['id'],'authority':source['authority'],'page':source['page'],'status':'取得成功' if rows else 'ページ取得済み・構造化名簿未抽出','records':len(rows),'scope':source['scope'],'note':source.get('note','')})
        except Exception as e:
            source_audits.append({'id':source['id'],'authority':source['authority'],'page':source['page'],'status':'取得失敗','records':0,'error':str(e),'scope':source.get('scope','')})
    # Build conservative exact/near-exact lookup keys. A name-only match is labelled as a match
    # candidate; exact code match is preferred. Conflicting duplicate names are not auto-confirmed.
    by_code={}; by_name={}
    for d in designation_rows:
        if d['code']: by_code.setdefault(norm(d['code']),[]).append(d)
        if d['name']: by_name.setdefault(norm(d['name']),[]).append(d)
    confirmed=0; candidates=0
    for x in records.values():
        code=norm(x.get('id',''))
        hits=by_code.get(code,[]) if code else []
        match_method='医療機関コード完全一致' if hits else ''
        if not hits:
            namehits=by_name.get(norm(x.get('name','')),[])
            # Require same prefecture/address token overlap for name-only matching.
            xaddr=norm(x.get('address',''))
            safe=[d for d in namehits if d.get('address') and (norm(d['address']) in xaddr or xaddr in norm(d['address']) or norm(d['address'])==xaddr)]
            if len(safe)==1:
                hits=safe; match_method='名称・住所一致'
            elif namehits:
                x['designation_status']='candidate'
                x['designation_note']='同名候補あり。住所・コードの追加確認が必要'
                candidates+=1
        if len(hits)==1:
            d=hits[0]
            x['designation_status']='confirmed'
            x['designation_authority']=d['authority']
            x['designation_source_url']=d['source_page']
            x['designation_source_file']=d['source_file']
            x['designation_match_method']=match_method
            x['designation_scope']=d['scope']
            confirmed+=1
        elif len(hits)>1:
            x['designation_status']='candidate'
            x['designation_note']='複数の指定名簿レコードに一致。自動確定せず'
            candidates+=1
    all_prefectures=['北海道','青森県','岩手県','宮城県','秋田県','山形県','福島県','茨城県','栃木県','群馬県','埼玉県','千葉県','東京都','神奈川県','新潟県','富山県','石川県','福井県','山梨県','長野県','岐阜県','静岡県','愛知県','三重県','滋賀県','京都府','大阪府','兵庫県','奈良県','和歌山県','鳥取県','島根県','岡山県','広島県','山口県','徳島県','香川県','愛媛県','高知県','福岡県','佐賀県','長崎県','熊本県','大分県','宮崎県','鹿児島県','沖縄県']
    covered_pref={s.get('prefecture') for s in registry.get('sources',[])}
    coverage={'coverage_status':'partial','registered_source_pages':len(registry.get('sources',[])),'prefectures_with_registered_sources':sorted(x for x in covered_pref if x),'prefecture_count_with_sources':len(covered_pref),'prefectures_without_any_registered_source':[p for p in all_prefectures if p not in covered_pref],'designation_records_downloaded':len(designation_rows),'confirmed_facilities':confirmed,'candidate_facilities':candidates,'jurisdiction_warning':'都道府県名簿が登録済みでも、政令指定都市・中核市等が独自に指定する区域を含むとは限りません。各自治体の管轄境界まで照合できた場合に限り、その区域を網羅済みとして扱います。','coverage_warning':'全国完全網羅ではありません。未登録自治体・独自管轄市・PDF画像名簿・公開されていない名簿は未確認のまま表示します。未確認を指定なしと判定しません。'}
    payload={'updated_at':datetime.now(timezone.utc).astimezone().isoformat(timespec='minutes'),'medical_data_as_of':asof,'medical_aid_as_of':aid_asof,'count':len(records),'facilities':list(records.values()),'designation_index':designation_rows,'designation_coverage':coverage,'designation_sources_audit':source_audits,'designation_records_count':len(designation_rows),'sources':{'medical_information_network':MED_PAGE,'medical_aid_online_eligibility':AID_PAGE,'designation_registry':'data/designation_sources.json'},'notice':'ブラウザ側でも指定名簿データを自動照合します。オンライン資格確認参加は生活保護法上の指定医療機関であることを意味しません。自治体の公式指定名簿と照合できない施設は未確認です。'}
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    print(f'Wrote {len(records)} facilities; aid list rows={len(aid_rows)}; medical release={asof}')
if __name__=='__main__': main()
