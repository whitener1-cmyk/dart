import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

st.set_page_config(page_title="KCA M&A 스크리너", layout="wide")

if "selected_corps"   not in st.session_state: st.session_state.selected_corps   = {}
if "industry_cache"   not in st.session_state: st.session_state.industry_cache   = {}
# industry_cache: {업종코드prefix: [{"corp_code","corp_name","업종명","대표자","설립일","주소"}, ...]}

# ══════════════════════════════════════════
# 업종 전체 목록 (한국표준산업분류 앞 2자리)
# ══════════════════════════════════════════
INDUSTRY_MAP = {
    "01":"농업", "02":"임업", "03":"어업",
    "05":"석탄/원유/천연가스 광업", "06":"금속광업", "07":"비금속광물 광업",
    "08":"기타광업", "09":"광업지원 서비스업",
    "10":"식료품 제조업", "11":"음료 제조업", "12":"담배 제조업",
    "13":"섬유제품 제조업", "14":"의복/액세서리 제조업",
    "15":"가죽/가방/신발 제조업", "16":"목재/나무제품 제조업",
    "17":"펄프/종이 제조업", "18":"인쇄/기록매체 복제업",
    "19":"코크스/석유정제품 제조업", "20":"화학물질/제품 제조업",
    "21":"의료용 물질/의약품 제조업", "22":"고무/플라스틱 제조업",
    "23":"비금속 광물제품 제조업", "24":"1차 금속 제조업",
    "25":"금속가공제품 제조업", "26":"전자부품/컴퓨터/영상/통신장비 제조업",
    "27":"의료/정밀/광학기기 제조업", "28":"전기장비 제조업",
    "29":"기타 기계 및 장비 제조업", "30":"자동차 및 트레일러 제조업",
    "31":"기타 운송장비 제조업", "32":"가구 제조업", "33":"기타 제조업",
    "35":"전기/가스/증기/공기조절 공급업", "36":"수도사업",
    "37":"하수/폐수 처리업", "38":"폐기물 수집/운반/처리업", "39":"환경정화/복원업",
    "41":"종합 건설업", "42":"전문직별 공사업", "43":"건물 건설업",
    "45":"자동차 및 부품 판매업", "46":"도매 및 상품중개업", "47":"소매업",
    "49":"육상 운송 및 파이프라인 운송업", "50":"수상 운송업", "51":"항공 운송업",
    "52":"창고 및 운송관련 서비스업",
    "55":"숙박업", "56":"음식점 및 주점업",
    "58":"출판업 (게임/서적/잡지 등)",
    "59":"영상/방송/음악 제작 및 배급업",
    "60":"방송업",
    "61":"통신업",
    "62":"소프트웨어 개발 및 공급업",
    "63":"정보서비스업 (포털/데이터 등)",
    "64":"금융업", "65":"보험 및 연금업", "66":"금융 및 보험관련 서비스업",
    "68":"부동산업", "70":"연구개발업",
    "71":"전문 서비스업 (법률/회계 등)",
    "72":"건축/엔지니어링 서비스업",
    "73":"광고/시장조사 서비스업",
    "74":"기타 전문/과학/기술 서비스업",
    "75":"수의업", "76":"사업지원 서비스업", "77":"임대업",
    "78":"고용 서비스업", "79":"여행/스포츠/오락관련 서비스업",
    "80":"경비/탐정 서비스업", "81":"건물/산업설비 청소 서비스업",
    "82":"기타 사업지원 서비스업",
    "84":"공공행정/국방/사회보장", "85":"교육 서비스업",
    "86":"보건업", "87":"사회복지 서비스업", "88":"기타 사회복지 서비스업",
    "90":"창작/예술/여가 서비스업",
    "91":"스포츠 및 여가관련 서비스업",
    "92":"도박/복권업", "93":"스포츠/오락/레저 서비스업",
    "94":"협회 및 단체", "95":"수리업", "96":"기타 개인 서비스업",
}
INDUSTRY_OPTIONS = [f"{v}  [{k}]" for k, v in sorted(INDUSTRY_MAP.items(), key=lambda x: x[0])]
def parse_prefix(option: str) -> str:
    return option.split("[")[-1].rstrip("]").strip()
def get_industry_label(code: str) -> str:
    if not code: return "업종 미분류"
    p = str(code).strip().zfill(5)[:2]
    return INDUSTRY_MAP.get(p, f"기타 ({code})")

# ══════════════════════════════════════════
# 계정명 분류 (IFRS 변형 전부 커버)
# ══════════════════════════════════════════
REVENUE_NAMES = {
    "매출액","매출","수익","수익(매출액)","매출액(수익)","매출액 등",
    "영업수익","사업수익","순매출액","I. 매출액","Ⅰ. 매출액",
    "이자수익","보험료수익","수수료수익",
}
OPERATING_NAMES = {
    "영업이익","영업손익","영업이익(손실)","영업이익(영업손실)",
    "영업이익(손실)합계","영업손실","III. 영업이익","Ⅲ. 영업이익","영업이익(손익)",
}
NET_INCOME_NAMES = {
    "당기순이익","당기순손익","당기순이익(손실)","당기순이익(당기순손실)",
    "당기순손실","연결당기순이익","분기순이익","반기순이익",
    "반기순이익(손실)","분기순이익(손실)",
    "지배기업 소유주 귀속 당기순이익","지배주주지분 순이익",
}
def classify_account(nm: str):
    nm = nm.strip()
    if nm in REVENUE_NAMES:    return "매출액"
    if nm in OPERATING_NAMES:  return "영업이익"
    if nm in NET_INCOME_NAMES: return "당기순이익"
    return None

# ══════════════════════════════════════════
# DART API 함수
# ══════════════════════════════════════════

@st.cache_data(show_spinner=False)
def fetch_corp_codes(key):
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"
    res = requests.get(url, timeout=15)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        xml_data = z.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)
    rows = []
    for c in root.findall("list"):
        sc = (c.findtext("stock_code") or "").strip()
        if sc: continue   # 상장사 제외
        rows.append({"corp_code": c.findtext("corp_code"),
                     "corp_name": c.findtext("corp_name")})
    return pd.DataFrame(rows)


def _fetch_fin_single(key, corp_code, year):
    """단일 기업·단일 연도 재무 조회 (병렬 처리용 헬퍼)"""
    base = {"crtfc_key": key, "corp_code": corp_code,
            "bsns_year": year, "reprt_code": "11011"}
    for endpoint, fs_div in [("fnlttSinglAcnt", None),
                              ("fnlttSinglAcntAll", "OFS"),
                              ("fnlttSinglAcntAll", "CFS")]:
        params = dict(base)
        if fs_div: params["fs_div"] = fs_div
        try:
            r = requests.get(f"https://opendart.fss.or.kr/api/{endpoint}.json",
                             params=params, timeout=8)
            d = r.json()
            if d.get("status") == "000" and d.get("list"):
                return d
        except Exception:
            pass
    return {}


def _extract_figures_raw(fin_data):
    """재무 데이터 → {매출액, 영업이익, 당기순이익} (억원 float)"""
    result = {"매출액": None, "영업이익": None, "당기순이익": None}
    for item in fin_data.get("list", []):
        key = classify_account(item.get("account_nm", ""))
        if key and result[key] is None:
            raw = item.get("thstrm_amount", "").replace(",", "").strip()
            try:
                result[key] = round(int(raw) / 1e8, 1)
            except ValueError:
                pass
    return result


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_recent_filers(key, industry_prefix: str,
                        min_revenue: float = 30.0,
                        min_operating: float = 3.0) -> list:
    """
    최근 2년 사업보고서 제출 비상장 외감 기업 목록 조회 (최대 500개).
    ① company.json 병렬 조회 → 업종코드 필터
    ② 최신연도 재무 병렬 조회 → 매출·영업이익 기준 필터
    반환: [{"corp_code","corp_name","업종명","대표자","설립일","주소",
            "매출액","영업이익","EBITDA","당기순이익"}, ...]
    """
    end_date   = datetime.today().strftime("%Y%m%d")
    start_date = (datetime.today() - timedelta(days=730)).strftime("%Y%m%d")

    # ── 1) 최근 사업보고서 제출 법인 목록 ──
    corp_codes = set()
    corp_names = {}
    for page in range(1, 6):
        try:
            r = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key": key, "bgn_de": start_date, "end_de": end_date,
                        "pblntf_detail_ty": "A001", "corp_cls": "E",
                        "page_no": str(page), "page_count": "100"},
                timeout=10,
            )
            d = r.json()
            if d.get("status") != "000": break
            for item in d.get("list", []):
                cc = item.get("corp_code", "")
                if cc:
                    corp_codes.add(cc)
                    corp_names[cc] = item.get("corp_name", "")
            if page >= d.get("total_page", 1): break
        except Exception:
            break

    if not corp_codes:
        return []

    # ── 2) 병렬 company.json → 업종 필터 ──
    industry_matched = []

    def _fetch_info(cc):
        try:
            r = requests.get("https://opendart.fss.or.kr/api/company.json",
                             params={"crtfc_key": key, "corp_code": cc}, timeout=6)
            d = r.json()
            if d.get("status") == "000":
                code = str(d.get("induty_code", "")).strip().zfill(5)
                if code[:2] == industry_prefix:
                    return {
                        "corp_code": cc,
                        "corp_name": d.get("corp_name", corp_names.get(cc, "")),
                        "업종명":    get_industry_label(code),
                        "업종코드":  code,
                        "대표자":    d.get("ceo_nm", "-"),
                        "설립일":    d.get("est_dt", "-"),
                        "주소":      d.get("adres", "-"),
                    }
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=12) as ex:
        for res in as_completed([ex.submit(_fetch_info, cc) for cc in corp_codes]):
            r = res.result()
            if r: industry_matched.append(r)

    if not industry_matched:
        return []

    # ── 3) 병렬 재무 조회 → 매출·영업이익 필터 ──
    # 최신 연도(2024) 우선, 없으면 2023
    LATEST_YEARS = ["2024", "2023"]

    def _fetch_fin(corp):
        cc = corp["corp_code"]
        for yr in LATEST_YEARS:
            fd = _fetch_fin_single(key, cc, yr)
            if fd.get("list"):
                figs = _extract_figures_raw(fd)
                rev  = figs.get("매출액")
                ebit = figs.get("영업이익")
                net  = figs.get("당기순이익")
                # 매출 30억 이상, 영업이익 3억 이상 필터
                if (rev  is not None and rev  >= min_revenue and
                    ebit is not None and ebit >= min_operating):
                    ebitda = round(ebit * 1.15, 1) if ebit is not None else None
                    return {**corp,
                            "기준연도":   yr,
                            "매출액":     rev,
                            "영업이익":   ebit,
                            "EBITDA":     ebitda,
                            "당기순이익": net}
        return None   # 필터 미통과

    final = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for res in as_completed([ex.submit(_fetch_fin, c) for c in industry_matched]):
            r = res.result()
            if r: final.append(r)

    # 매출액 오름차순 정렬
    return sorted(final, key=lambda x: (x.get("매출액") or 0))


def fetch_financial_robust(key, corp_code, year):
    base = {"crtfc_key": key, "corp_code": corp_code,
            "bsns_year": year, "reprt_code": "11011"}
    attempts = [
        ("fnlttSinglAcnt",    None),
        ("fnlttSinglAcntAll", "OFS"),
        ("fnlttSinglAcntAll", "CFS"),
    ]
    logs = []
    for endpoint, fs_div in attempts:
        params = dict(base)
        if fs_div: params["fs_div"] = fs_div
        label = f"{endpoint}/{fs_div or 'auto'}"
        try:
            r = requests.get(f"https://opendart.fss.or.kr/api/{endpoint}.json",
                             params=params, timeout=10)
            d = r.json()
            cnt = len(d.get("list", []))
            logs.append(f"  {label}: status={d.get('status')}, {cnt}건")
            if d.get("status") == "000" and cnt > 0:
                d["_fs_div"] = fs_div or "auto"
                d["_logs"]   = logs
                return d
        except Exception as e:
            logs.append(f"  {label}: 예외 {e}")
    return {"_logs": logs}


def extract_figures(fin_data):
    return _extract_figures_raw(fin_data)


# ══════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════
st.sidebar.title("KCA M&A 스크리너")
api_key    = st.sidebar.text_input("🔑 DART API 인증키", type="password")
debug_mode = st.sidebar.checkbox("🐛 디버그 모드", value=False)
menu_tab   = st.sidebar.radio("페이지 이동",
    ["1. 타깃 스크리닝 & 정밀분석", "2. 장바구니 통합 Valuation"])
st.sidebar.divider()
st.sidebar.metric("🛒 장바구니", f"{len(st.session_state.selected_corps)}개")
for name in st.session_state.selected_corps:
    st.sidebar.write(f"• {name}")


# ══════════════════════════════════════════
# 1페이지
# ══════════════════════════════════════════
if menu_tab == "1. 타깃 스크리닝 & 정밀분석":
    st.title("🎯 비상장 M&A 타깃 스크리닝 & DART 정밀 분석")

    if not api_key:
        st.info("왼쪽 사이드바에 DART API 인증키를 입력하면 활성화됩니다.")
        st.stop()

    # ── STEP 1: 업종 선택 ──
    st.subheader("① 업종 선택")
    selected_option = st.selectbox(
        "분석할 업종을 선택하세요",
        options=["— 업종을 선택하세요 —"] + INDUSTRY_OPTIONS,
    )

    if selected_option == "— 업종을 선택하세요 —":
        st.info("업종을 선택하면 해당 업종의 비상장 외감 법인 목록이 나타납니다.")
        st.stop()

    industry_prefix = parse_prefix(selected_option)
    industry_name   = INDUSTRY_MAP.get(industry_prefix, selected_option)

    # ── STEP 2: 해당 업종 기업 목록 조회 ──
    cache_key = industry_prefix
    if cache_key not in st.session_state.industry_cache:
        with st.spinner(f"'{industry_name}' 업종 비상장 기업 조회 + 재무 필터링 중... (최초 1회, 약 30~60초 소요)"):
            corps = fetch_recent_filers(api_key, industry_prefix, min_revenue=30.0, min_operating=3.0)
        st.session_state.industry_cache[cache_key] = corps
    else:
        corps = st.session_state.industry_cache[cache_key]

    if not corps:
        st.warning(
            f"'{industry_name}' 업종에서 매출 30억·영업이익 3억 이상 비상장 외감 법인을 찾지 못했습니다.\n\n"
            "다른 업종을 선택하거나, 기업명 직접 검색을 이용해주세요."
        )
    else:
        st.success(f"✅ '{industry_name}' 업종 · 매출 30억↑ · 영업이익 3억↑ 기업 **{len(corps)}개** 조회 완료")

        # 기업 목록 표 — 재무 컬럼 포함
        df_corps = pd.DataFrame(corps)[[
            "corp_name","업종명","기준연도","매출액","영업이익","EBITDA","당기순이익","대표자","설립일"
        ]].copy()
        df_corps.columns = ["기업명","업종","기준연도","매출액(억)","영업이익(억)","EBITDA(억)","당기순이익(억)","대표자","설립일"]
        # 매출액 오름차순 (이미 정렬됐지만 표에서도 명시)
        df_corps = df_corps.sort_values("매출액(억)", ascending=True).reset_index(drop=True)

        # 기업명 키워드 추가 필터 (선택)
        kw = st.text_input("🔎 목록 내 기업명 필터 (선택사항)", placeholder="예: 스튜디오, 엔터")
        if kw:
            df_corps = df_corps[df_corps["기업명"].str.contains(kw, na=False)]

        st.subheader(f"② 기업 목록 ({len(df_corps)}개)  ·  필터: 매출 30억↑ · 영업이익 3억↑ · 매출액 오름차순")
        st.dataframe(df_corps, use_container_width=True, hide_index=True)

        if df_corps.empty:
            st.warning("필터 조건에 맞는 기업이 없습니다.")
            st.stop()

        # ── STEP 3: 기업 선택 → 상세 분석 ──
        st.subheader("③ 기업 선택 후 정밀 분석")
        selected_name = st.selectbox("분석할 기업 선택", df_corps["기업명"].tolist())

        corp_data = next((c for c in corps if c["corp_name"] == selected_name), None)
        if not corp_data:
            st.stop()

        corp_code = corp_data["corp_code"]

        # 기업 기본 정보
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("대표자",   corp_data.get("대표자","-"))
        c2.metric("업종",     corp_data.get("업종명","-"))
        c3.metric("설립일",   corp_data.get("설립일","-"))
        c4.metric("법인구분", "비상장 외감")
        st.caption(f"주소: {corp_data.get('주소','-')}")

        # 재무 3개년
        st.subheader(f"📈 {selected_name} — 3개년 재무 추이")

        years    = ["2024","2023","2022"]
        fin_rows = []
        fs_div_used = None
        all_logs    = []

        progress = st.progress(0, text="재무 데이터 조회 중...")
        for i, yr in enumerate(years):
            progress.progress((i+1)/len(years), text=f"{yr}년 조회 중...")
            fin_data = fetch_financial_robust(api_key, corp_code, yr)
            all_logs.extend([f"\n[{yr}년]"] + fin_data.get("_logs",[]))
            if not fin_data.get("list"): continue
            if fs_div_used is None:
                fs_div_used = fin_data.get("_fs_div","?")
            figures = extract_figures(fin_data)
            if any(v is not None for v in figures.values()):
                r = {"연도": yr}
                r.update(figures)
                if figures["영업이익"] is not None:
                    r["EBITDA(추정)"] = round(figures["영업이익"]*1.15, 1)
                fin_rows.append(r)
        progress.empty()

        if debug_mode:
            with st.expander("🐛 DART API 로그"):
                st.code("\n".join(all_logs))

        if fin_rows:
            fs_label = {"CFS":"연결(CFS)","OFS":"별도(OFS)","auto":"자동"}.get(fs_div_used, fs_div_used)
            st.caption(f"📌 재무제표 기준: {fs_label}")
            df_fin = pd.DataFrame(fin_rows).set_index("연도")
            st.table(df_fin)
            chart_col = st.selectbox("차트 항목", [c for c in df_fin.columns if df_fin[c].notna().any()])
            st.bar_chart(df_fin[[chart_col]])
        else:
            st.warning(
                f"📭 {selected_name}의 재무 데이터가 DART에 없습니다.\n\n"
                "외감 대상이어도 제출 지연 또는 면제 기업은 데이터가 없을 수 있습니다."
            )

        # 수동 입력
        with st.expander("✏️ 재무 데이터 수동 입력 (DART 미등록 기업용)", expanded=not fin_rows):
            st.caption("단위: 억원")
            mc1, mc2, mc3 = st.columns(3)
            with mc1: m_rev  = st.number_input("매출액",    0.0, step=1.0, key="m_rev")
            with mc2: m_ebit = st.number_input("영업이익",  0.0, step=1.0, key="m_ebit")
            with mc3: m_net  = st.number_input("당기순이익",0.0, step=1.0, key="m_net")
            if m_rev or m_ebit or m_net:
                fin_rows = [{"연도":"수동입력","매출액": m_rev or None,
                             "영업이익": m_ebit or None, "당기순이익": m_net or None,
                             "EBITDA(추정)": round(m_ebit*1.15,1) if m_ebit else None}]
                st.success("수동 입력 데이터가 장바구니에 반영됩니다.")

        # 장바구니
        st.divider()
        st.subheader("🛒 인수 장바구니")
        in_cart = selected_name in st.session_state.selected_corps
        toggle  = st.checkbox(
            f"⭐ {selected_name}을 인수 후보로 장바구니에 담기",
            value=in_cart, key=f"cart_{corp_code}",
        )
        latest_fin = fin_rows[0] if fin_rows else {}
        if toggle and not in_cart:
            st.session_state.selected_corps[selected_name] = {
                "corp_code": corp_code,
                "업종명": corp_data.get("업종명","-"),
                **latest_fin,
            }
            st.toast(f"✅ {selected_name} 추가!")
        elif not toggle and in_cart:
            del st.session_state.selected_corps[selected_name]
            st.toast(f"🗑️ {selected_name} 제거")


# ══════════════════════════════════════════
# 2페이지
# ══════════════════════════════════════════
elif menu_tab == "2. 장바구니 통합 Valuation":
    st.title("📊 장바구니 통합 Valuation & 인수비용 시뮬레이터")
    cart = st.session_state.selected_corps
    n    = len(cart)
    if n == 0:
        st.warning("1페이지에서 기업을 장바구니에 담아주세요.")
        st.stop()
    if n < 2:
        st.info(f"현재 {n}개 — 2개 이상이면 비교 분석이 풍부해집니다.")
    st.write(f"**선택 기업 {n}개**: {', '.join(cart.keys())}")

    st.header("1. Valuation 배수 설정")
    c1,c2,c3,c4 = st.columns(4)
    with c1: m_ebit   = st.slider("EV/영업이익", 3.0,40.0,10.0,0.5)
    with c2: m_ebitda = st.slider("EV/EBITDA",   3.0,40.0, 8.0,0.5)
    with c3: m_per    = st.slider("PER",          5.0,50.0,15.0,1.0)
    with c4: m_psr    = st.slider("PSR",          0.5,20.0, 1.5,0.1)
    method = st.radio("메인 Valuation 지표",
        ["EV/영업이익","EV/EBITDA","PER","PSR"], horizontal=True)

    val_rows = []
    for name, data in cart.items():
        rev    = data.get("매출액")
        ebit   = data.get("영업이익")
        ebitda = data.get("EBITDA(추정)")
        net    = data.get("당기순이익")
        if   method=="EV/영업이익" and ebit:   ev=round(ebit*m_ebit,1)
        elif method=="EV/EBITDA"  and ebitda:  ev=round(ebitda*m_ebitda,1)
        elif method=="PER"        and net:      ev=round(net*m_per,1)
        elif method=="PSR"        and rev:      ev=round(rev*m_psr,1)
        else:                                   ev=None
        val_rows.append({"기업명":name,"업종":data.get("업종명","-"),
            "매출액(억)":rev,"영업이익(억)":ebit,"당기순이익(억)":net,
            "산출 EV(억)":ev,"Market Cap(억)":round(ev-10,1) if ev else None})

    df_val = pd.DataFrame(val_rows)
    no_data = df_val[df_val["산출 EV(억)"].isna()]["기업명"].tolist()
    if no_data:
        st.warning(f"⚠️ EV 계산 불가 ({method} 데이터 없음): {', '.join(no_data)}")
    st.subheader("2. 기업별 산출 EV")
    st.table(df_val)

    df_valid = df_val.dropna(subset=["산출 EV(억)"])
    if not df_valid.empty:
        cc1,cc2 = st.columns(2)
        cc1.metric("총 EV 합계",        f"{df_valid['산출 EV(억)'].sum():,.1f} 억원")
        cc2.metric("총 Market Cap 합계",f"{df_valid['Market Cap(억)'].sum():,.1f} 억원")
        st.divider()
        st.header("3. 인수비용 계산기")
        a1,a2,a3 = st.columns(3)
        with a1: acq_ratio  = st.number_input("인수 지분 비율 (%)",51,100,100)
        with a2: cash_ratio = st.number_input("현금 지급 비율 (%)", 0,100, 10)
        with a3: discount   = st.number_input("현금 할인율 (%)",     0, 50,  0)
        stock_ratio = 100 - cash_ratio
        acq_rows = []
        for _, r in df_valid.iterrows():
            base=round(r["산출 EV(억)"]*acq_ratio/100,1)
            cash=round(base*cash_ratio/100*(1-discount/100),1)
            swap=round(base*stock_ratio/100,1)
            acq_rows.append({"기업명":r["기업명"],"인수기준금액(억)":base,
                              "현금지급액(억)":cash,"지분스왑액(억)":swap})
        df_acq = pd.DataFrame(acq_rows)
        st.table(df_acq)
        s_cash=df_acq["현금지급액(억)"].sum()
        s_swap=df_acq["지분스왑액(억)"].sum()
        f1,f2,f3=st.columns(3)
        f1.metric("총 현금 소요액",  f"{s_cash:,.1f} 억원")
        f2.metric("총 지분스왑 규모",f"{s_swap:,.1f} 억원")
        f3.metric("딜 총 규모",      f"{s_cash+s_swap:,.1f} 억원")
