import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="KCA M&A 스크리너", layout="wide")

if "selected_corps" not in st.session_state:
    st.session_state.selected_corps = {}

# ══════════════════════════════════════════════════════
# 한국표준산업분류 전체 목록 (코드 앞 2자리 → 업종명)
# ══════════════════════════════════════════════════════
INDUSTRY_MAP = {
    "01": "농업",
    "02": "임업",
    "03": "어업",
    "05": "석탄/원유/천연가스 광업",
    "06": "금속광업",
    "07": "비금속광물 광업",
    "08": "기타광업",
    "09": "광업지원 서비스업",
    "10": "식료품 제조업",
    "11": "음료 제조업",
    "12": "담배 제조업",
    "13": "섬유제품 제조업",
    "14": "의복/액세서리 제조업",
    "15": "가죽/가방/신발 제조업",
    "16": "목재/나무제품 제조업",
    "17": "펄프/종이 제조업",
    "18": "인쇄/기록매체 복제업",
    "19": "코크스/석유정제품 제조업",
    "20": "화학물질/제품 제조업",
    "21": "의료용 물질/의약품 제조업",
    "22": "고무/플라스틱 제조업",
    "23": "비금속 광물제품 제조업",
    "24": "1차 금속 제조업",
    "25": "금속가공제품 제조업",
    "26": "전자부품/컴퓨터/영상/통신장비 제조업",
    "27": "의료/정밀/광학기기 제조업",
    "28": "전기장비 제조업",
    "29": "기타 기계 및 장비 제조업",
    "30": "자동차 및 트레일러 제조업",
    "31": "기타 운송장비 제조업",
    "32": "가구 제조업",
    "33": "기타 제조업",
    "35": "전기/가스/증기/공기조절 공급업",
    "36": "수도사업",
    "37": "하수/폐수 처리업",
    "38": "폐기물 수집/운반/처리업",
    "39": "환경정화/복원업",
    "41": "종합 건설업",
    "42": "전문직별 공사업",
    "43": "건물 건설업",
    "45": "자동차 및 부품 판매업",
    "46": "도매 및 상품중개업",
    "47": "소매업",
    "49": "육상 운송 및 파이프라인 운송업",
    "50": "수상 운송업",
    "51": "항공 운송업",
    "52": "창고 및 운송관련 서비스업",
    "55": "숙박업",
    "56": "음식점 및 주점업",
    "58": "출판업 (게임/서적/잡지 등)",
    "59": "영상/방송/음악 제작 및 배급업",
    "60": "방송업",
    "61": "통신업",
    "62": "소프트웨어 개발 및 공급업",
    "63": "정보서비스업 (포털/데이터 등)",
    "64": "금융업",
    "65": "보험 및 연금업",
    "66": "금융 및 보험관련 서비스업",
    "68": "부동산업",
    "70": "연구개발업",
    "71": "전문 서비스업 (법률/회계 등)",
    "72": "건축/엔지니어링 서비스업",
    "73": "광고/시장조사 서비스업",
    "74": "기타 전문/과학/기술 서비스업",
    "75": "수의업",
    "76": "사업지원 서비스업",
    "77": "임대업",
    "78": "고용 서비스업",
    "79": "여행/스포츠/오락관련 서비스업",
    "80": "경비/탐정 서비스업",
    "81": "건물/산업설비 청소 서비스업",
    "82": "기타 사업지원 서비스업",
    "84": "공공행정/국방/사회보장",
    "85": "교육 서비스업",
    "86": "보건업",
    "87": "사회복지 서비스업",
    "88": "기타 사회복지 서비스업",
    "90": "창작/예술/여가 서비스업",
    "91": "스포츠 및 여가관련 서비스업",
    "92": "도박/복권업",
    "93": "스포츠/오락/레저 서비스업",
    "94": "협회 및 단체",
    "95": "수리업",
    "96": "기타 개인 서비스업",
}

# ══════════════════════════════════════════════════════
# 계정명 분류 (IFRS 연결/별도 모든 변형 커버)
# ══════════════════════════════════════════════════════
REVENUE_NAMES = {
    # 표준형
    "매출액", "매출", "수익",
    # 괄호형
    "수익(매출액)", "매출액(수익)", "매출액 등",
    # 업종별 변형
    "영업수익", "사업수익", "순매출액",
    "I. 매출액", "Ⅰ. 매출액",
    # 금융업
    "이자수익", "보험료수익", "수수료수익",
}
OPERATING_NAMES = {
    "영업이익", "영업손익",
    "영업이익(손실)", "영업이익(영업손실)",
    "영업이익(손실)합계", "영업손실",
    "III. 영업이익", "Ⅲ. 영업이익",
    "영업이익(손익)",
}
NET_INCOME_NAMES = {
    "당기순이익", "당기순손익", "당기순이익(손실)",
    "당기순이익(당기순손실)", "당기순손실",
    "연결당기순이익", "분기순이익", "반기순이익",
    "반기순이익(손실)", "분기순이익(손실)",
    "지배기업 소유주 귀속 당기순이익",
    "지배주주지분 순이익",
}

def classify_account(nm: str):
    nm = nm.strip()
    if nm in REVENUE_NAMES:    return "매출액"
    if nm in OPERATING_NAMES:  return "영업이익"
    if nm in NET_INCOME_NAMES: return "당기순이익"
    return None

def get_industry_label(code: str) -> str:
    """업종코드 앞 2자리로 업종명 반환"""
    if not code:
        return "업종 미분류"
    prefix = str(code).strip().zfill(5)[:2]
    return INDUSTRY_MAP.get(prefix, f"기타 ({code})")

# ══════════════════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════════════════
st.sidebar.title("KCA M&A 스크리너")
api_key    = st.sidebar.text_input("🔑 DART API 인증키", type="password")
debug_mode = st.sidebar.checkbox("🐛 디버그 모드", value=False)
menu_tab   = st.sidebar.radio("페이지 이동", ["1. 타깃 스크리닝 & 정밀분석", "2. 장바구니 통합 Valuation"])
st.sidebar.divider()
st.sidebar.metric("🛒 장바구니", f"{len(st.session_state.selected_corps)}개")
for name in st.session_state.selected_corps:
    st.sidebar.write(f"• {name}")

# ══════════════════════════════════════════════════════
# DART API 함수
# ══════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def fetch_corp_codes(key):
    """비상장사만 로드 (stock_code 없는 것)"""
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"
    res = requests.get(url, timeout=15)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        xml_data = z.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)
    rows = []
    for c in root.findall("list"):
        sc = (c.findtext("stock_code") or "").strip()
        if sc:           # 상장사 제외
            continue
        rows.append({
            "corp_code": c.findtext("corp_code"),
            "corp_name": c.findtext("corp_name"),
        })
    return pd.DataFrame(rows)


def fetch_company_info(key, corp_code):
    try:
        r = requests.get(
            "https://opendart.fss.or.kr/api/company.json",
            params={"crtfc_key": key, "corp_code": corp_code},
            timeout=8,
        )
        d = r.json()
        return d if d.get("status") == "000" else {}
    except Exception:
        return {}


def fetch_company_info_bulk(key, corp_codes: list) -> dict:
    """
    여러 기업의 company.json을 병렬 조회.
    반환: {corp_code: info_dict}
    """
    results = {}
    def _fetch(cc):
        return cc, fetch_company_info(key, cc)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch, cc): cc for cc in corp_codes}
        for fut in as_completed(futures):
            cc, info = fut.result()
            results[cc] = info
    return results


def fetch_financial_robust(key, corp_code, year):
    """
    비상장사 재무: fnlttSinglAcnt (fs_div 없는 구버전) 우선
    → fnlttSinglAcntAll OFS → CFS 순으로 시도
    """
    base = {"crtfc_key": key, "corp_code": corp_code,
            "bsns_year": year, "reprt_code": "11011"}
    attempts = [
        ("fnlttSinglAcnt",    None),       # 비상장사에 가장 잘 맞음
        ("fnlttSinglAcntAll", "OFS"),
        ("fnlttSinglAcntAll", "CFS"),
    ]
    logs = []
    for endpoint, fs_div in attempts:
        params = dict(base)
        if fs_div:
            params["fs_div"] = fs_div
        label = f"{endpoint}/{fs_div or 'auto'}"
        try:
            r = requests.get(
                f"https://opendart.fss.or.kr/api/{endpoint}.json",
                params=params, timeout=10)
            d = r.json()
            cnt = len(d.get("list", []))
            logs.append(f"  {label}: status={d.get('status')}, list={cnt}건")
            if d.get("status") == "000" and cnt > 0:
                d["_fs_div"] = fs_div or "auto"
                d["_logs"]   = logs
                return d
        except Exception as e:
            logs.append(f"  {label}: 예외 {e}")
    return {"_logs": logs}


def extract_figures(fin_data):
    result = {"매출액": None, "영업이익": None, "당기순이익": None}
    for item in fin_data.get("list", []):
        nm  = item.get("account_nm", "").strip()
        key = classify_account(nm)
        if key and result[key] is None:
            raw = item.get("thstrm_amount", "").replace(",", "").strip()
            try:
                result[key] = round(int(raw) / 1e8, 1)
            except ValueError:
                pass
    return result


# ══════════════════════════════════════════════════════
# 1페이지
# ══════════════════════════════════════════════════════
if menu_tab == "1. 타깃 스크리닝 & 정밀분석":
    st.title("🎯 비상장 M&A 타깃 스크리닝 & DART 정밀 분석")

    if not api_key:
        st.info("왼쪽 사이드바에 DART API 인증키를 입력하면 활성화됩니다.")
        st.stop()

    with st.spinner("DART 비상장 법인 DB 로드 중..."):
        try:
            df_all = fetch_corp_codes(api_key)
        except Exception as e:
            st.error(f"DART 인증 실패: {e}")
            st.stop()
    st.success(f"✅ 비상장 법인 {len(df_all):,}개 로드 완료")

    # ── 검색 UI ──
    st.subheader("🔍 1단계: 기업명 검색")
    keyword = st.text_input("기업명 (일부 입력 가능)", placeholder="예: 스튜디오, 엔터테인먼트, 게임")

    if not keyword:
        st.info("기업명을 입력하면 검색됩니다.")
        st.stop()

    df_keyword = df_all[df_all["corp_name"].str.contains(keyword, na=False)].head(50)

    if df_keyword.empty:
        st.warning("검색 결과 없음 — 다른 키워드를 시도해보세요.")
        st.stop()

    st.info(f"'{keyword}' 키워드로 {len(df_keyword)}개 검색됨. 업종 정보 조회 중...")

    # ── 업종 병렬 조회 ──
    with st.spinner(f"{len(df_keyword)}개 기업 업종 정보 조회 중 (최대 10초)..."):
        corp_info_map = fetch_company_info_bulk(api_key, df_keyword["corp_code"].tolist())

    # 업종 컬럼 추가
    df_keyword = df_keyword.copy()
    df_keyword["업종코드"] = df_keyword["corp_code"].map(
        lambda cc: corp_info_map.get(cc, {}).get("induty_code", "")
    )
    df_keyword["업종명"] = df_keyword["업종코드"].map(get_industry_label)
    df_keyword["대표자"] = df_keyword["corp_code"].map(
        lambda cc: corp_info_map.get(cc, {}).get("ceo_nm", "-")
    )

    # ── 업종 필터 ──
    st.subheader("🏭 2단계: 업종 필터")
    available_industries = sorted(df_keyword["업종명"].dropna().unique().tolist())

    if available_industries:
        selected_industries = st.multiselect(
            "표시할 업종 선택 (미선택 시 전체 표시)",
            options=available_industries,
            default=[],
            placeholder="업종을 선택하세요 (복수 선택 가능)",
        )
    else:
        selected_industries = []

    if selected_industries:
        df_filtered = df_keyword[df_keyword["업종명"].isin(selected_industries)]
    else:
        df_filtered = df_keyword

    # ── 검색 결과 표 ──
    st.subheader(f"📋 3단계: 분석 기업 선택 ({len(df_filtered)}개)")
    st.dataframe(
        df_filtered[["corp_name", "업종명", "대표자"]].rename(
            columns={"corp_name": "기업명"}
        ),
        use_container_width=True, hide_index=True,
    )

    if df_filtered.empty:
        st.warning("선택한 업종에 해당하는 기업이 없습니다.")
        st.stop()

    selected_name = st.selectbox("정밀 분석할 기업 선택", df_filtered["corp_name"].tolist())
    row_s     = df_filtered[df_filtered["corp_name"] == selected_name].iloc[0]
    corp_code = row_s["corp_code"]

    # ── 기업 기본 정보 ──
    info = corp_info_map.get(corp_code, {})
    if info:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("대표자",   info.get("ceo_nm",     "-"))
        c2.metric("업종",     get_industry_label(info.get("induty_code", "")))
        c3.metric("설립일",   info.get("est_dt",      "-"))
        c4.metric("법인구분", info.get("corp_cls",    "-"))
        st.caption(f"주소: {info.get('adres', '-')}")

    # ── 재무 3개년 ──
    st.subheader(f"📈 {selected_name} — 3개년 재무 추이")
    st.caption("⚠️ 비상장사는 DART 감사보고서 제출 여부에 따라 데이터가 없을 수 있습니다.")

    years       = ["2024", "2023", "2022"]
    fin_rows    = []
    fs_div_used = None
    all_logs    = []

    progress = st.progress(0, text="재무 데이터 조회 중...")
    for i, yr in enumerate(years):
        progress.progress((i + 1) / len(years), text=f"{yr}년 조회 중...")
        fin_data = fetch_financial_robust(api_key, corp_code, yr)
        all_logs.extend([f"\n[{yr}년]"] + fin_data.get("_logs", []))

        if not fin_data.get("list"):
            continue
        if fs_div_used is None:
            fs_div_used = fin_data.get("_fs_div", "?")

        figures = extract_figures(fin_data)
        if any(v is not None for v in figures.values()):
            r = {"연도": yr}
            r.update(figures)
            if figures["영업이익"] is not None:
                r["EBITDA(추정)"] = round(figures["영업이익"] * 1.15, 1)
            fin_rows.append(r)
    progress.empty()

    if debug_mode:
        with st.expander("🐛 DART API 로그", expanded=False):
            st.code("\n".join(all_logs))

    if fin_rows:
        fs_label = {"CFS": "연결(CFS)", "OFS": "별도(OFS)", "auto": "자동"}.get(fs_div_used, fs_div_used)
        st.caption(f"📌 재무제표 기준: {fs_label}")
        df_fin = pd.DataFrame(fin_rows).set_index("연도")
        st.table(df_fin)
        chart_col = st.selectbox("차트 항목", [c for c in df_fin.columns if df_fin[c].notna().any()])
        st.bar_chart(df_fin[[chart_col]])
    else:
        st.warning(
            f"📭 {selected_name}의 재무 데이터가 DART에 없습니다.\n\n"
            "비상장사는 외부감사 대상(자산 120억 이상 등)이어야 DART에 재무제표가 등록됩니다. "
            "외감 비대상 기업은 수동 입력이 필요합니다."
        )

    # ── 수동 재무 입력 (DART 데이터 없을 때) ──
    with st.expander("✏️ 재무 데이터 수동 입력 (DART 미등록 기업용)", expanded=not fin_rows):
        st.caption("직접 수치를 입력하면 장바구니에 담아 Valuation에 활용할 수 있습니다. (단위: 억원)")
        mc1, mc2, mc3 = st.columns(3)
        with mc1: manual_rev  = st.number_input("매출액 (억원)",    0.0, step=1.0, key="m_rev")
        with mc2: manual_ebit = st.number_input("영업이익 (억원)",  0.0, step=1.0, key="m_ebit")
        with mc3: manual_net  = st.number_input("당기순이익 (억원)",0.0, step=1.0, key="m_net")
        if manual_rev or manual_ebit or manual_net:
            fin_rows = [{"연도": "수동입력", "매출액": manual_rev or None,
                         "영업이익": manual_ebit or None, "당기순이익": manual_net or None,
                         "EBITDA(추정)": round(manual_ebit * 1.15, 1) if manual_ebit else None}]
            st.success("수동 입력 데이터가 장바구니에 반영됩니다.")

    # ── 장바구니 ──
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
            "업종명": get_industry_label(info.get("induty_code", "")),
            **latest_fin,
        }
        st.toast(f"✅ {selected_name} 추가!")
    elif not toggle and in_cart:
        del st.session_state.selected_corps[selected_name]
        st.toast(f"🗑️ {selected_name} 제거")


# ══════════════════════════════════════════════════════
# 2페이지
# ══════════════════════════════════════════════════════
elif menu_tab == "2. 장바구니 통합 Valuation":
    st.title("📊 장바구니 통합 Valuation & 인수비용 시뮬레이터")

    cart = st.session_state.selected_corps
    n    = len(cart)

    if n == 0:
        st.warning("1페이지에서 기업을 장바구니에 담아주세요.")
        st.stop()
    if n < 2:
        st.info(f"현재 {n}개 선택됨 — 2개 이상이면 비교 분석이 풍부해집니다.")

    st.write(f"**선택 기업 {n}개**: {', '.join(cart.keys())}")

    st.header("1. Valuation 배수 설정")
    c1, c2, c3, c4 = st.columns(4)
    with c1: m_ebit   = st.slider("EV/영업이익", 3.0, 40.0, 10.0, 0.5)
    with c2: m_ebitda = st.slider("EV/EBITDA",   3.0, 40.0,  8.0, 0.5)
    with c3: m_per    = st.slider("PER",          5.0, 50.0, 15.0, 1.0)
    with c4: m_psr    = st.slider("PSR",          0.5, 20.0,  1.5, 0.1)
    method = st.radio("메인 Valuation 지표", ["EV/영업이익", "EV/EBITDA", "PER", "PSR"], horizontal=True)

    val_rows = []
    for name, data in cart.items():
        rev    = data.get("매출액")
        ebit   = data.get("영업이익")
        ebitda = data.get("EBITDA(추정)")
        net    = data.get("당기순이익")

        if   method == "EV/영업이익" and ebit:   ev = round(ebit   * m_ebit,   1)
        elif method == "EV/EBITDA"  and ebitda:  ev = round(ebitda * m_ebitda, 1)
        elif method == "PER"        and net:      ev = round(net    * m_per,    1)
        elif method == "PSR"        and rev:      ev = round(rev    * m_psr,    1)
        else:                                     ev = None

        val_rows.append({
            "기업명":         name,
            "업종":           data.get("업종명", "-"),
            "매출액(억)":     rev,
            "영업이익(억)":   ebit,
            "당기순이익(억)": net,
            "산출 EV(억)":    ev,
            "Market Cap(억)": round(ev - 10, 1) if ev else None,
        })

    df_val  = pd.DataFrame(val_rows)
    no_data = df_val[df_val["산출 EV(억)"].isna()]["기업명"].tolist()
    if no_data:
        st.warning(f"⚠️ EV 계산 불가 ({method} 데이터 없음): {', '.join(no_data)}")

    st.subheader("2. 기업별 산출 EV")
    st.table(df_val)

    df_valid = df_val.dropna(subset=["산출 EV(억)"])
    if not df_valid.empty:
        cc1, cc2 = st.columns(2)
        cc1.metric("총 EV 합계",         f"{df_valid['산출 EV(억)'].sum():,.1f} 억원")
        cc2.metric("총 Market Cap 합계", f"{df_valid['Market Cap(억)'].sum():,.1f} 억원")

        st.divider()
        st.header("3. 인수비용 계산기")
        a1, a2, a3 = st.columns(3)
        with a1: acq_ratio  = st.number_input("인수 지분 비율 (%)", 51, 100, 100)
        with a2: cash_ratio = st.number_input("현금 지급 비율 (%)",  0, 100,  10)
        with a3: discount   = st.number_input("현금 할인율 (%)",      0,  50,   0)
        stock_ratio = 100 - cash_ratio

        acq_rows = []
        for _, r in df_valid.iterrows():
            base = round(r["산출 EV(억)"] * acq_ratio  / 100, 1)
            cash = round(base * cash_ratio / 100 * (1 - discount / 100), 1)
            swap = round(base * stock_ratio / 100, 1)
            acq_rows.append({"기업명": r["기업명"], "인수기준금액(억)": base,
                              "현금지급액(억)": cash, "지분스왑액(억)": swap})

        df_acq = pd.DataFrame(acq_rows)
        st.table(df_acq)
        s_cash = df_acq["현금지급액(억)"].sum()
        s_swap = df_acq["지분스왑액(억)"].sum()
        f1, f2, f3 = st.columns(3)
        f1.metric("총 현금 소요액",   f"{s_cash:,.1f} 억원")
        f2.metric("총 지분스왑 규모", f"{s_swap:,.1f} 억원")
        f3.metric("딜 총 규모",       f"{s_cash + s_swap:,.1f} 억원")
