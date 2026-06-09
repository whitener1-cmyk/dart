import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

st.set_page_config(page_title="KCA M&A 스크리너", layout="wide")

if "selected_corps" not in st.session_state: st.session_state.selected_corps = {}
if "screened_df"    not in st.session_state: st.session_state.screened_df    = None
if "screen_done"    not in st.session_state: st.session_state.screen_done    = False

# ══════════════════════════════════════════
# 업종 전체 목록
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
    "60":"방송업", "61":"통신업",
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

def get_industry_label(code: str) -> str:
    if not code: return "업종 미분류"
    p = str(code).strip().zfill(5)[:2]
    return INDUSTRY_MAP.get(p, f"기타 ({code})")

# ══════════════════════════════════════════
# 계정명 분류
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
def fetch_corp_codes(key: str) -> pd.DataFrame:
    """비상장사만 (stock_code 없는 것)"""
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"
    res = requests.get(url, timeout=15)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        xml_data = z.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)
    rows = []
    for c in root.findall("list"):
        sc = (c.findtext("stock_code") or "").strip()
        if sc: continue
        rows.append({"corp_code": c.findtext("corp_code"),
                     "corp_name": c.findtext("corp_name")})
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, ttl=86400)
def fetch_recent_filer_codes(key: str) -> set:
    """
    최근 2년 사업보고서 제출 기업 corp_code 집합.
    corp_cls 없이 전체 조회 → corpCode.xml 비상장 목록과 교차 필터는 호출부에서.
    최대 10페이지(1,000개) 수집.
    """
    end_date   = datetime.today().strftime("%Y%m%d")
    start_date = (datetime.today() - timedelta(days=730)).strftime("%Y%m%d")
    codes = set()
    for page in range(1, 11):
        try:
            r = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key": key,
                        "bgn_de": start_date, "end_de": end_date,
                        "pblntf_detail_ty": "A001",   # 사업보고서
                        "page_no": str(page), "page_count": "100"},
                timeout=12,
            )
            d = r.json()
            if d.get("status") != "000": break
            for item in d.get("list", []):
                cc = item.get("corp_code", "")
                if cc: codes.add(cc)
            if page >= int(d.get("total_page", 1)): break
        except Exception:
            break
    return codes


def fetch_company_info_single(key: str, corp_code: str) -> dict:
    try:
        r = requests.get("https://opendart.fss.or.kr/api/company.json",
                         params={"crtfc_key": key, "corp_code": corp_code},
                         timeout=7)
        d = r.json()
        return d if d.get("status") == "000" else {}
    except Exception:
        return {}


def fetch_fin_single(key: str, corp_code: str) -> dict:
    """최신 연도(2024→2023→2022) 재무. 성공하면 figures + 연도 반환."""
    base = {"crtfc_key": key, "corp_code": corp_code, "reprt_code": "11011"}
    for yr in ["2024", "2023", "2022"]:
        for endpoint, fs_div in [("fnlttSinglAcnt", None),
                                  ("fnlttSinglAcntAll", "OFS"),
                                  ("fnlttSinglAcntAll", "CFS")]:
            params = {**base, "bsns_year": yr}
            if fs_div: params["fs_div"] = fs_div
            try:
                r = requests.get(f"https://opendart.fss.or.kr/api/{endpoint}.json",
                                  params=params, timeout=8)
                d = r.json()
                if d.get("status") == "000" and d.get("list"):
                    figs = _extract_figures(d)
                    if any(v is not None for v in figs.values()):
                        return {"연도": yr, **figs}
            except Exception:
                pass
    return {}


def _extract_figures(fin_data: dict) -> dict:
    result = {"매출액": None, "영업이익": None, "당기순이익": None}
    for item in fin_data.get("list", []):
        k = classify_account(item.get("account_nm", ""))
        if k and result[k] is None:
            raw = item.get("thstrm_amount", "").replace(",", "").strip()
            try: result[k] = round(int(raw) / 1e8, 1)
            except ValueError: pass
    return result


# ══════════════════════════════════════════════════════════════
# 전체 스크리닝 실행 (세션에 캐시)
# ══════════════════════════════════════════════════════════════
def run_screening(key: str, df_unlisted: pd.DataFrame,
                  min_rev: float, min_op: float) -> pd.DataFrame:
    """
    비상장 외감사 기업 중 매출·영업이익 기준 이상인 기업만 반환.
    각 기업당: company.json(업종·대표자) + 재무 병렬 조회.
    """
    # 최근 사업보고서 제출 corp_code와 교집합
    with st.spinner("📡 DART 최근 사업보고서 제출 목록 수집 중..."):
        filer_codes = fetch_recent_filer_codes(key)

    unlisted_set = set(df_unlisted["corp_code"].tolist())
    candidates   = list(unlisted_set & filer_codes)   # 비상장 + 사보 제출

    st.info(f"비상장 외감 후보 {len(candidates):,}개 — 업종·재무 병렬 조회 시작")

    results = []
    total   = len(candidates)
    progress = st.progress(0, text="조회 중...")
    done_count = [0]

    def _process(cc):
        # ① company.json
        info = fetch_company_info_single(key, cc)
        if not info: return None
        # ② 재무
        fin = fetch_fin_single(key, cc)
        if not fin: return None
        rev  = fin.get("매출액")
        ebit = fin.get("영업이익")
        if rev is None or ebit is None: return None
        if rev < min_rev or ebit < min_op: return None
        net    = fin.get("당기순이익")
        ebitda = round(ebit * 1.15, 1) if ebit is not None else None
        ind_code = str(info.get("induty_code","")).strip().zfill(5)
        return {
            "corp_code":   cc,
            "기업명":      info.get("corp_name", ""),
            "업종코드앞2": ind_code[:2],
            "업종명":      get_industry_label(ind_code),
            "대표자":      info.get("ceo_nm", "-"),
            "설립일":      info.get("est_dt", "-"),
            "기준연도":    fin.get("연도",""),
            "매출액(억)":  rev,
            "영업이익(억)":ebit,
            "EBITDA(억)":  ebitda,
            "당기순이익(억)": net,
        }

    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(_process, cc): cc for cc in candidates}
        for fut in as_completed(futures):
            done_count[0] += 1
            progress.progress(min(done_count[0] / max(total,1), 1.0),
                              text=f"{done_count[0]:,}/{total:,} 조회 중...")
            res = fut.result()
            if res: results.append(res)

    progress.empty()

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values("매출액(억)", ascending=True).reset_index(drop=True)
    return df


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

    # 비상장 법인 DB 로드
    with st.spinner("DART 비상장 법인 DB 로드 중..."):
        try:
            df_all = fetch_corp_codes(api_key)
        except Exception as e:
            st.error(f"DART 인증 실패: {e}")
            st.stop()

    # ── 스크리닝 실행 버튼 ──
    col_btn, col_info = st.columns([2, 5])
    with col_btn:
        run_btn = st.button("🚀 전체 스크리닝 실행",
                            help="매출 30억↑ · 영업이익 3억↑ 비상장 기업 전체 조회 (수 분 소요)",
                            type="primary")
    with col_info:
        st.caption("최초 1회 실행 후 결과가 세션에 저장됩니다. 필터는 아래에서 실시간으로 조정하세요.")

    if run_btn:
        st.session_state.screen_done = False
        st.session_state.screened_df = None

    if run_btn or not st.session_state.screen_done:
        if run_btn:
            df_screened = run_screening(api_key, df_all, min_rev=30.0, min_op=3.0)
            st.session_state.screened_df  = df_screened
            st.session_state.screen_done  = True
        elif not st.session_state.screen_done:
            st.info("위 '🚀 전체 스크리닝 실행' 버튼을 눌러 시작하세요.")
            st.stop()

    if st.session_state.screen_done and st.session_state.screened_df is not None:
        df_base = st.session_state.screened_df.copy()

        if df_base.empty:
            st.warning("조건에 맞는 기업을 찾지 못했습니다.")
            st.stop()

        st.success(f"✅ 매출 30억↑ · 영업이익 3억↑ 비상장 기업 총 **{len(df_base)}개** 조회 완료")

        # ── 필터 영역 ──
        st.subheader("🔽 필터 & 검색")
        f1, f2 = st.columns([3, 2])

        with f1:
            # 업종 멀티셀렉트 (실제 조회된 업종만 표시)
            available_industries = sorted(df_base["업종명"].dropna().unique().tolist())
            sel_industries = st.multiselect(
                "업종 필터 (미선택 = 전체)",
                options=available_industries,
                default=[],
                placeholder="업종을 선택하세요 (복수 선택 가능)",
            )

        with f2:
            # 기업명 검색
            kw = st.text_input("🔎 기업명 검색", placeholder="예: 스튜디오, 엔터, 게임")

        # 필터 적용
        df_view = df_base.copy()
        if sel_industries:
            df_view = df_view[df_view["업종명"].isin(sel_industries)]
        if kw:
            df_view = df_view[df_view["기업명"].str.contains(kw, na=False)]

        # ── 목록 표시 ──
        st.subheader(f"📋 기업 목록 ({len(df_view)}개) — 매출액 오름차순")
        display_cols = ["기업명","업종명","기준연도","매출액(억)","영업이익(억)","EBITDA(억)","당기순이익(억)","대표자","설립일"]
        st.dataframe(
            df_view[display_cols].reset_index(drop=True),
            use_container_width=True, hide_index=True,
        )

        if df_view.empty:
            st.warning("필터 조건에 맞는 기업이 없습니다.")
            st.stop()

        # ── 기업 선택 → 정밀 분석 ──
        st.divider()
        st.subheader("🔍 정밀 분석")
        selected_name = st.selectbox("분석할 기업 선택", df_view["기업명"].tolist())

        row_s = df_view[df_view["기업명"] == selected_name].iloc[0]
        corp_code = row_s["corp_code"]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("대표자",   row_s.get("대표자", "-"))
        c2.metric("업종",     row_s.get("업종명", "-"))
        c3.metric("설립일",   row_s.get("설립일", "-"))
        c4.metric("법인구분", "비상장 외감")

        # 3개년 재무
        st.subheader(f"📈 {selected_name} — 3개년 재무 추이")
        years    = ["2024", "2023", "2022"]
        fin_rows = []
        all_logs = []
        fs_div_used = None

        prog = st.progress(0, text="재무 데이터 조회 중...")
        for i, yr in enumerate(years):
            prog.progress((i+1)/len(years), text=f"{yr}년 조회 중...")
            base_p = {"crtfc_key": api_key, "corp_code": corp_code,
                      "bsns_year": yr, "reprt_code": "11011"}
            fin_data = {}
            for endpoint, fs_div in [("fnlttSinglAcnt", None),
                                      ("fnlttSinglAcntAll", "OFS"),
                                      ("fnlttSinglAcntAll", "CFS")]:
                params = dict(base_p)
                if fs_div: params["fs_div"] = fs_div
                label = f"{endpoint}/{fs_div or 'auto'}"
                try:
                    r = requests.get(f"https://opendart.fss.or.kr/api/{endpoint}.json",
                                     params=params, timeout=10)
                    d = r.json()
                    cnt = len(d.get("list", []))
                    all_logs.append(f"[{yr}] {label}: status={d.get('status')}, {cnt}건")
                    if d.get("status") == "000" and cnt > 0:
                        fin_data = d
                        if fs_div_used is None:
                            fs_div_used = fs_div or "auto"
                        break
                except Exception as e:
                    all_logs.append(f"[{yr}] {label}: 예외 {e}")
            figs = _extract_figures(fin_data)
            if any(v is not None for v in figs.values()):
                r_row = {"연도": yr}
                r_row.update(figs)
                if figs["영업이익"] is not None:
                    r_row["EBITDA(추정)"] = round(figs["영업이익"] * 1.15, 1)
                fin_rows.append(r_row)
        prog.empty()

        if debug_mode:
            with st.expander("🐛 DART API 로그"):
                st.code("\n".join(all_logs))

        if fin_rows:
            fs_label = {"CFS":"연결(CFS)","OFS":"별도(OFS)","auto":"자동"}.get(fs_div_used, fs_div_used)
            st.caption(f"📌 재무제표 기준: {fs_label}")
            df_fin = pd.DataFrame(fin_rows).set_index("연도")
            st.table(df_fin)
            chart_col = st.selectbox("차트 항목",
                [c for c in df_fin.columns if df_fin[c].notna().any()])
            st.bar_chart(df_fin[[chart_col]])
        else:
            st.warning("재무 데이터 없음 — 수동 입력을 이용해주세요.")

        # 수동 입력
        with st.expander("✏️ 재무 수동 입력 (DART 미등록 기업용)", expanded=not fin_rows):
            st.caption("단위: 억원")
            mc1, mc2, mc3 = st.columns(3)
            with mc1: m_rev  = st.number_input("매출액",    0.0, step=1.0, key="m_rev")
            with mc2: m_ebit = st.number_input("영업이익",  0.0, step=1.0, key="m_ebit")
            with mc3: m_net  = st.number_input("당기순이익",0.0, step=1.0, key="m_net")
            if m_rev or m_ebit or m_net:
                fin_rows = [{"연도":"수동입력","매출액": m_rev or None,
                             "영업이익": m_ebit or None, "당기순이익": m_net or None,
                             "EBITDA(추정)": round(m_ebit*1.15,1) if m_ebit else None}]

        # 장바구니
        st.divider()
        st.subheader("🛒 인수 장바구니")
        in_cart = selected_name in st.session_state.selected_corps
        toggle  = st.checkbox(f"⭐ {selected_name}을 인수 후보로 장바구니에 담기",
                              value=in_cart, key=f"cart_{corp_code}")
        latest_fin = fin_rows[0] if fin_rows else {}
        if toggle and not in_cart:
            st.session_state.selected_corps[selected_name] = {
                "corp_code": corp_code,
                "업종명": row_s.get("업종명", "-"),
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
