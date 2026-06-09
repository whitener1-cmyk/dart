import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import zipfile
import io

# ─────────────────────────────────────────────
# 페이지 설정 및 세션 초기화
# ─────────────────────────────────────────────
st.set_page_config(page_title="KCA M&A 스크리너", layout="wide")

if "selected_corps" not in st.session_state:
    st.session_state.selected_corps = {}   # { corp_name: {corp_code, 재무데이터} }

# ─────────────────────────────────────────────
# 사이드바
# ─────────────────────────────────────────────
st.sidebar.title("KCA M&A 스크리너")
api_key = st.sidebar.text_input("🔑 DART API 인증키", type="password")
menu_tab = st.sidebar.radio("페이지 이동", ["1. 타깃 스크리닝 & 정밀분석", "2. 장바구니 통합 Valuation"])

st.sidebar.divider()
cart_count = len(st.session_state.selected_corps)
st.sidebar.metric("🛒 장바구니 기업 수", f"{cart_count}개")
if st.session_state.selected_corps:
    for name in st.session_state.selected_corps:
        st.sidebar.write(f"• {name}")

# ─────────────────────────────────────────────
# DART API 공통 함수
# ─────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def fetch_corp_codes(crtfc_key: str) -> pd.DataFrame:
    """DART 전체 법인 고유번호 로드 (ZIP → XML 파싱)"""
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={crtfc_key}"
    res = requests.get(url, timeout=15)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        xml_data = z.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)
    rows = []
    for c in root.findall("list"):
        stock_code = (c.findtext("stock_code") or "").strip()
        rows.append({
            "corp_code": c.findtext("corp_code"),
            "corp_name": c.findtext("corp_name"),
            "stock_code": stock_code,
            "is_listed": bool(stock_code),   # 상장 여부
        })
    return pd.DataFrame(rows)


def fetch_company_info(crtfc_key: str, corp_code: str) -> dict:
    """기업 기본 정보 (업종명, 설립일, 대표자 등)"""
    url = "https://opendart.fss.or.kr/api/company.json"
    params = {"crtfc_key": crtfc_key, "corp_code": corp_code}
    res = requests.get(url, params=params, timeout=10)
    data = res.json()
    if data.get("status") == "000":
        return data
    return {}


def fetch_financial(crtfc_key: str, corp_code: str, year: str) -> dict:
    """
    단일회사 주요재무 (fnlttSinglAcntAll) — 상장사 전용
    reprt_code: 11011 = 사업보고서(연간)
    fs_div: OFS = 별도재무제표
    """
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key": crtfc_key,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": "11011",
        "fs_div": "OFS",
    }
    res = requests.get(url, params=params, timeout=10)
    return res.json()


def extract_key_figures(fin_data: dict) -> dict:
    """재무 JSON에서 매출액·영업이익·당기순이익 추출 → 억원 단위"""
    targets = {"매출액": None, "영업이익": None, "당기순이익": None}
    if fin_data.get("status") != "000":
        return targets
    for item in fin_data.get("list", []):
        nm = item.get("account_nm", "")
        if nm in targets:
            raw = item.get("thstrm_amount", "").replace(",", "").replace("-", "")
            try:
                targets[nm] = round(int(raw) / 1e8, 1)
            except ValueError:
                pass
    return targets


def search_corps_by_name(df_all: pd.DataFrame, keyword: str, listed_only: bool) -> pd.DataFrame:
    """기업명 키워드 검색 + 상장 여부 필터"""
    result = df_all[df_all["corp_name"].str.contains(keyword, na=False)]
    if listed_only:
        result = result[result["is_listed"]]
    return result.head(30)


# ─────────────────────────────────────────────
# 1페이지: 타깃 스크리닝 & 정밀 분석
# ─────────────────────────────────────────────
if menu_tab == "1. 타깃 스크리닝 & 정밀분석":
    st.title("🎯 M&A 타깃 스크리닝 & DART 정밀 분석")

    if not api_key:
        st.info("왼쪽 사이드바에 **DART API 인증키**를 입력하면 시스템이 활성화됩니다.")
        st.stop()

    # ── 법인 DB 로드 ──
    with st.spinner("DART 법인 DB 동기화 중..."):
        try:
            df_all = fetch_corp_codes(api_key)
        except Exception as e:
            st.error(f"DART 인증 실패 또는 네트워크 오류: {e}")
            st.stop()

    st.success(f"✅ DART DB 로드 완료 — 총 {len(df_all):,}개 법인")

    # ── 검색 UI ──
    st.subheader("🔍 기업명 검색")
    col_kw, col_opt = st.columns([3, 1])
    with col_kw:
        keyword = st.text_input("기업명 (일부 입력 가능)", placeholder="예: 스튜디오드래곤, 하이브, 카카오")
    with col_opt:
        listed_only = st.checkbox("상장사만", value=True)

    if not keyword:
        st.info("기업명을 입력하면 검색 결과가 나타납니다.")
        st.stop()

    df_search = search_corps_by_name(df_all, keyword, listed_only)

    if df_search.empty:
        st.warning("검색 결과가 없습니다. 다른 키워드나 '상장사만' 해제 후 시도해보세요.")
        st.stop()

    # ── 검색 결과 선택 ──
    st.subheader(f"📋 검색 결과 ({len(df_search)}개)")
    st.dataframe(
        df_search[["corp_name", "stock_code", "is_listed"]].rename(
            columns={"corp_name": "기업명", "stock_code": "종목코드", "is_listed": "상장여부"}
        ),
        use_container_width=True,
        hide_index=True,
    )

    selected_name = st.selectbox(
        "정밀 분석할 기업 선택",
        df_search["corp_name"].tolist(),
    )

    if not selected_name:
        st.stop()

    row = df_search[df_search["corp_name"] == selected_name].iloc[0]
    corp_code = row["corp_code"]
    is_listed = row["is_listed"]

    # ── 기업 기본 정보 ──
    with st.spinner("기업 기본 정보 로딩..."):
        info = fetch_company_info(api_key, corp_code)

    if info:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("대표자", info.get("ceo_nm", "-"))
        c2.metric("업종", info.get("induty_code", "-"))
        c3.metric("설립일", info.get("est_dt", "-"))
        c4.metric("법인구분", info.get("corp_cls", "-"))
        st.caption(f"주소: {info.get('adres', '-')}")

    # ── 재무 3개년 시계열 (상장사만) ──
    st.subheader(f"📈 {selected_name} — 3개년 재무 추이 (별도, 연간)")

    if not is_listed:
        st.warning(
            "⚠️ 비상장사는 DART 주요재무 API를 지원하지 않습니다.\n\n"
            "비상장사 재무 데이터가 필요하면 해당 기업의 감사보고서 PDF를 DART에서 직접 조회하거나, "
            "엑셀 파일을 직접 입력하는 방식을 추천드립니다."
        )
    else:
        years = ["2024", "2023", "2022"]
        fin_rows = []
        error_years = []

        progress = st.progress(0)
        for i, yr in enumerate(years):
            progress.progress((i + 1) / len(years), text=f"{yr}년 데이터 조회 중...")
            fin_data = fetch_financial(api_key, corp_code, yr)
            figures = extract_key_figures(fin_data)
            if all(v is None for v in figures.values()):
                error_years.append(yr)
            else:
                row_data = {"연도": yr}
                row_data.update(figures)
                # EBITDA 추정 (영업이익 × 1.15 — D&A 없을 경우 근사)
                if figures["영업이익"] is not None:
                    row_data["EBITDA (추정)"] = round(figures["영업이익"] * 1.15, 1)
                fin_rows.append(row_data)
        progress.empty()

        if error_years:
            st.caption(f"ℹ️ {', '.join(error_years)}년 데이터 없음 (미제출 또는 기간 미해당)")

        if fin_rows:
            df_fin = pd.DataFrame(fin_rows).set_index("연도")
            st.table(df_fin)

            # 간단 차트
            chart_col = st.selectbox("차트로 볼 항목", [c for c in df_fin.columns if df_fin[c].notna().any()])
            st.bar_chart(df_fin[[chart_col]])
        else:
            st.error("재무 데이터를 가져오지 못했습니다. 종목코드 또는 API 키를 확인해주세요.")

    # ── 장바구니 담기 ──
    st.divider()
    st.subheader("🛒 KCA 인수 장바구니")

    in_cart = selected_name in st.session_state.selected_corps
    toggle = st.checkbox(
        f"⭐ {selected_name}을 인수 후보로 장바구니에 담기",
        value=in_cart,
        key=f"cart_{corp_code}",
    )

    if toggle and not in_cart:
        # 최신 재무 데이터 저장
        latest_fin = fin_rows[0] if (is_listed and fin_rows) else {}
        st.session_state.selected_corps[selected_name] = {
            "corp_code": corp_code,
            "is_listed": is_listed,
            **latest_fin,
        }
        st.toast(f"✅ {selected_name} 장바구니에 추가!")
    elif not toggle and in_cart:
        del st.session_state.selected_corps[selected_name]
        st.toast(f"🗑️ {selected_name} 장바구니에서 제거")


# ─────────────────────────────────────────────
# 2페이지: 장바구니 통합 Valuation
# ─────────────────────────────────────────────
elif menu_tab == "2. 장바구니 통합 Valuation":
    st.title("📊 KCA 장바구니 통합 Valuation & 인수비용 시뮬레이터")

    cart = st.session_state.selected_corps
    n = len(cart)

    if n == 0:
        st.warning("1페이지에서 기업을 장바구니에 담아주세요.")
        st.stop()

    if n < 2:
        st.warning(f"현재 {n}개 선택됨 — 최소 2개 이상이어야 시뮬레이터가 활성화됩니다.")

    st.write(f"**선택 기업 {n}개**: {', '.join(cart.keys())}")

    # ── Valuation 파라미터 ──
    st.header("1. Valuation 배수 설정")
    col1, col2, col3, col4 = st.columns(4)
    with col1: m_ebit = st.slider("EV/영업이익", 3.0, 40.0, 10.0, 0.5)
    with col2: m_ebitda = st.slider("EV/EBITDA", 3.0, 40.0, 8.0, 0.5)
    with col3: m_per = st.slider("PER", 5.0, 50.0, 15.0, 1.0)
    with col4: m_psr = st.slider("PSR", 0.5, 20.0, 1.5, 0.1)

    method = st.radio(
        "메인 Valuation 지표",
        ["EV/영업이익", "EV/EBITDA", "PER", "PSR"],
        horizontal=True,
    )

    # ── EV 계산 ──
    val_rows = []
    for name, data in cart.items():
        rev = data.get("매출액")
        ebit = data.get("영업이익")
        ebitda = data.get("EBITDA (추정)")
        net = data.get("당기순이익")

        if method == "EV/영업이익" and ebit:
            ev = round(ebit * m_ebit, 1)
        elif method == "EV/EBITDA" and ebitda:
            ev = round(ebitda * m_ebitda, 1)
        elif method == "PER" and net:
            ev = round(net * m_per, 1)
        elif method == "PSR" and rev:
            ev = round(rev * m_psr, 1)
        else:
            ev = None

        val_rows.append({
            "기업명": name,
            "매출액(억)": rev,
            "영업이익(억)": ebit,
            "당기순이익(억)": net,
            "산출 EV(억)": ev,
            "Market Cap(억)": round(ev - 10, 1) if ev else None,
        })

    df_val = pd.DataFrame(val_rows)
    no_data = df_val[df_val["산출 EV(억)"].isna()]["기업명"].tolist()
    if no_data:
        st.warning(
            f"⚠️ 다음 기업은 선택 지표({method})에 필요한 재무 데이터가 없어 EV 계산 불가: {', '.join(no_data)}\n\n"
            "1페이지에서 다시 담거나 다른 Valuation 지표를 선택해보세요."
        )

    st.subheader("2. 기업별 산출 EV")
    st.table(df_val)

    df_valid = df_val.dropna(subset=["산출 EV(억)"])
    total_ev = df_valid["산출 EV(억)"].sum()
    total_cap = df_valid["Market Cap(억)"].sum()

    c1, c2 = st.columns(2)
    c1.metric("총 EV 합계", f"{total_ev:,.1f} 억원")
    c2.metric("총 Market Cap 합계", f"{total_cap:,.1f} 억원")

    # ── 인수비용 계산기 ──
    st.divider()
    st.header("3. 인수비용 계산기")

    cc1, cc2, cc3 = st.columns(3)
    with cc1: acq_ratio = st.number_input("인수 지분 비율 (%)", 51, 100, 100)
    with cc2: cash_ratio = st.number_input("현금 지급 비율 (%)", 0, 100, 10)
    with cc3: discount = st.number_input("현금 할인율 (%)", 0, 50, 0)

    stock_ratio = 100 - cash_ratio

    acq_rows = []
    for _, r in df_valid.iterrows():
        base = round(r["산출 EV(억)"] * acq_ratio / 100, 1)
        cash_amt = round(base * cash_ratio / 100 * (1 - discount / 100), 1)
        swap_amt = round(base * stock_ratio / 100, 1)
        acq_rows.append({
            "기업명": r["기업명"],
            "인수 기준금액(억)": base,
            "현금 지급액(억)": cash_amt,
            "지분스왑액(억)": swap_amt,
        })

    df_acq = pd.DataFrame(acq_rows)
    st.table(df_acq)

    sum_cash = df_acq["현금 지급액(억)"].sum()
    sum_swap = df_acq["지분스왑액(억)"].sum()

    f1, f2, f3 = st.columns(3)
    f1.metric("총 현금 소요액", f"{sum_cash:,.1f} 억원")
    f2.metric("총 지분스왑 규모", f"{sum_swap:,.1f} 억원")
    f3.metric("딜 총 규모", f"{sum_cash + sum_swap:,.1f} 억원")
