import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import zipfile
import io

st.set_page_config(page_title="KCA M&A 스크리너", layout="wide")

if "selected_corps" not in st.session_state:
    st.session_state.selected_corps = {}

# ── 사이드바 ──
st.sidebar.title("KCA M&A 스크리너")
api_key = st.sidebar.text_input("🔑 DART API 인증키", type="password")
menu_tab = st.sidebar.radio("페이지 이동", ["1. 타깃 스크리닝 & 정밀분석", "2. 장바구니 통합 Valuation"])
st.sidebar.divider()
cart_count = len(st.session_state.selected_corps)
st.sidebar.metric("🛒 장바구니", f"{cart_count}개")
for name in st.session_state.selected_corps:
    st.sidebar.write(f"• {name}")

# ── DART API 함수 ──

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
        rows.append({
            "corp_code": c.findtext("corp_code"),
            "corp_name": c.findtext("corp_name"),
            "stock_code": sc,
            "is_listed": bool(sc),
        })
    return pd.DataFrame(rows)


def fetch_company_info(key, corp_code):
    r = requests.get(
        "https://opendart.fss.or.kr/api/company.json",
        params={"crtfc_key": key, "corp_code": corp_code},
        timeout=10,
    )
    d = r.json()
    return d if d.get("status") == "000" else {}


def fetch_financial_robust(key, corp_code, year):
    """
    CFS(연결) 우선, 없으면 OFS(별도) 시도.
    fnlttSinglAcntAll 실패 시 fnlttSinglAcnt로 fallback.
    """
    base_params = {
        "crtfc_key": key,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": "11011",   # 사업보고서(연간)
    }

    # 시도 순서: (endpoint, fs_div)
    attempts = [
        ("fnlttSinglAcntAll", "CFS"),
        ("fnlttSinglAcntAll", "OFS"),
        ("fnlttSinglAcnt",    None),      # fs_div 없음
    ]

    for endpoint, fs_div in attempts:
        params = dict(base_params)
        if fs_div:
            params["fs_div"] = fs_div
        try:
            r = requests.get(
                f"https://opendart.fss.or.kr/api/{endpoint}.json",
                params=params, timeout=10,
            )
            d = r.json()
            if d.get("status") == "000" and d.get("list"):
                d["_fs_div_used"] = fs_div or "auto"
                return d
        except Exception:
            continue
    return {}


def extract_figures(fin_data):
    targets = {"매출액": None, "영업이익": None, "당기순이익": None}
    for item in fin_data.get("list", []):
        nm = item.get("account_nm", "")
        if nm in targets and targets[nm] is None:
            raw = item.get("thstrm_amount", "").replace(",", "").replace("-", "").strip()
            try:
                targets[nm] = round(int(raw) / 1e8, 1)
            except ValueError:
                pass
    return targets


# ════════════════════════════════════════
# 1페이지
# ════════════════════════════════════════
if menu_tab == "1. 타깃 스크리닝 & 정밀분석":
    st.title("🎯 M&A 타깃 스크리닝 & DART 정밀 분석")

    if not api_key:
        st.info("왼쪽 사이드바에 DART API 인증키를 입력하면 시스템이 활성화됩니다.")
        st.stop()

    with st.spinner("DART 법인 DB 동기화 중..."):
        try:
            df_all = fetch_corp_codes(api_key)
        except Exception as e:
            st.error(f"DART 인증 실패: {e}")
            st.stop()
    st.success(f"✅ DART DB 로드 완료 — 총 {len(df_all):,}개 법인")

    # 검색
    st.subheader("🔍 기업명 검색")
    col_kw, col_opt = st.columns([3, 1])
    with col_kw:
        keyword = st.text_input("기업명 (일부 입력 가능)", placeholder="예: 카카오, 하이브, 스튜디오드래곤")
    with col_opt:
        listed_only = st.checkbox("상장사만", value=True)

    if not keyword:
        st.info("기업명을 입력하면 검색됩니다.")
        st.stop()

    mask = df_all["corp_name"].str.contains(keyword, na=False)
    if listed_only:
        mask &= df_all["is_listed"]
    df_search = df_all[mask].head(30)

    if df_search.empty:
        st.warning("검색 결과 없음 — '상장사만' 체크 해제 후 시도해보세요.")
        st.stop()

    st.subheader(f"📋 검색 결과 ({len(df_search)}개)")
    st.dataframe(
        df_search[["corp_name", "stock_code", "is_listed"]].rename(
            columns={"corp_name": "기업명", "stock_code": "종목코드", "is_listed": "상장"}
        ),
        use_container_width=True, hide_index=True,
    )

    selected_name = st.selectbox("정밀 분석할 기업 선택", df_search["corp_name"].tolist())
    row = df_search[df_search["corp_name"] == selected_name].iloc[0]
    corp_code = row["corp_code"]
    is_listed = row["is_listed"]

    # 기업 기본 정보
    with st.spinner("기업 기본 정보 조회 중..."):
        info = fetch_company_info(api_key, corp_code)
    if info:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("대표자", info.get("ceo_nm", "-"))
        c2.metric("업종코드", info.get("induty_code", "-"))
        c3.metric("설립일", info.get("est_dt", "-"))
        c4.metric("법인구분", info.get("corp_cls", "-"))
        st.caption(f"주소: {info.get('adres', '-')}")

    # 재무 3개년
    st.subheader(f"📈 {selected_name} — 3개년 재무 추이")

    if not is_listed:
        st.warning(
            "비상장사는 DART 주요재무 API를 지원하지 않습니다.\n"
            "감사보고서 PDF를 DART에서 직접 조회하거나 수동 입력을 이용해주세요."
        )
    else:
        # ★ 핵심 수정: 2023, 2022, 2021만 조회 (2024 사업보고서는 2025년 3~4월 제출)
        years = ["2023", "2022", "2021"]
        fin_rows = []
        fs_div_used = None

        progress = st.progress(0, text="재무 데이터 조회 중...")
        for i, yr in enumerate(years):
            progress.progress((i + 1) / len(years), text=f"{yr}년 조회 중...")
            fin_data = fetch_financial_robust(api_key, corp_code, yr)
            if not fin_data:
                continue
            if fs_div_used is None:
                fs_div_used = fin_data.get("_fs_div_used", "?")
            figures = extract_figures(fin_data)
            if any(v is not None for v in figures.values()):
                r = {"연도": yr}
                r.update(figures)
                if figures["영업이익"] is not None:
                    r["EBITDA(추정)"] = round(figures["영업이익"] * 1.15, 1)
                fin_rows.append(r)
        progress.empty()

        if fin_rows:
            st.caption(f"📌 재무제표 기준: {'연결(CFS)' if fs_div_used == 'CFS' else '별도(OFS)' if fs_div_used == 'OFS' else '자동'}")
            df_fin = pd.DataFrame(fin_rows).set_index("연도")
            st.table(df_fin)
            chart_col = st.selectbox("차트 항목", [c for c in df_fin.columns if df_fin[c].notna().any()])
            st.bar_chart(df_fin[[chart_col]])
        else:
            st.error(
                "재무 데이터를 가져오지 못했습니다.\n\n"
                f"• DART corp_code: {corp_code}\n"
                "• 사업보고서(연간) 기준으로 조회했습니다.\n"
                "• API 키가 올바른지 확인해주세요."
            )

    # 장바구니
    st.divider()
    st.subheader("🛒 인수 장바구니")
    in_cart = selected_name in st.session_state.selected_corps
    toggle = st.checkbox(
        f"⭐ {selected_name}을 인수 후보로 장바구니에 담기",
        value=in_cart,
        key=f"cart_{corp_code}",
    )
    latest_fin = fin_rows[0] if (is_listed and fin_rows) else {}
    if toggle and not in_cart:
        st.session_state.selected_corps[selected_name] = {"corp_code": corp_code, "is_listed": is_listed, **latest_fin}
        st.toast(f"✅ {selected_name} 추가!")
    elif not toggle and in_cart:
        del st.session_state.selected_corps[selected_name]
        st.toast(f"🗑️ {selected_name} 제거")


# ════════════════════════════════════════
# 2페이지
# ════════════════════════════════════════
elif menu_tab == "2. 장바구니 통합 Valuation":
    st.title("📊 장바구니 통합 Valuation & 인수비용 시뮬레이터")

    cart = st.session_state.selected_corps
    n = len(cart)

    if n == 0:
        st.warning("1페이지에서 기업을 장바구니에 담아주세요.")
        st.stop()
    if n < 2:
        st.info(f"현재 {n}개 선택됨 — 2개 이상이면 비교 분석 활성화")

    st.write(f"**선택 기업 {n}개**: {', '.join(cart.keys())}")

    # Valuation 파라미터
    st.header("1. Valuation 배수 설정")
    c1, c2, c3, c4 = st.columns(4)
    with c1: m_ebit   = st.slider("EV/영업이익", 3.0, 40.0, 10.0, 0.5)
    with c2: m_ebitda = st.slider("EV/EBITDA",   3.0, 40.0,  8.0, 0.5)
    with c3: m_per    = st.slider("PER",          5.0, 50.0, 15.0, 1.0)
    with c4: m_psr    = st.slider("PSR",          0.5, 20.0,  1.5, 0.1)

    method = st.radio("메인 Valuation 지표", ["EV/영업이익", "EV/EBITDA", "PER", "PSR"], horizontal=True)

    val_rows = []
    for name, data in cart.items():
        rev   = data.get("매출액")
        ebit  = data.get("영업이익")
        ebitda = data.get("EBITDA(추정)")
        net   = data.get("당기순이익")

        if   method == "EV/영업이익" and ebit:    ev = round(ebit * m_ebit, 1)
        elif method == "EV/EBITDA"  and ebitda:   ev = round(ebitda * m_ebitda, 1)
        elif method == "PER"        and net:       ev = round(net * m_per, 1)
        elif method == "PSR"        and rev:       ev = round(rev * m_psr, 1)
        else:                                      ev = None

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
        st.warning(f"⚠️ EV 계산 불가 기업 ({method} 데이터 없음): {', '.join(no_data)}")

    st.subheader("2. 기업별 산출 EV")
    st.table(df_val)

    df_valid = df_val.dropna(subset=["산출 EV(억)"])
    if not df_valid.empty:
        total_ev  = df_valid["산출 EV(억)"].sum()
        total_cap = df_valid["Market Cap(억)"].sum()
        cc1, cc2 = st.columns(2)
        cc1.metric("총 EV 합계",         f"{total_ev:,.1f} 억원")
        cc2.metric("총 Market Cap 합계", f"{total_cap:,.1f} 억원")

        st.divider()
        st.header("3. 인수비용 계산기")
        a1, a2, a3 = st.columns(3)
        with a1: acq_ratio = st.number_input("인수 지분 비율 (%)", 51, 100, 100)
        with a2: cash_ratio = st.number_input("현금 지급 비율 (%)", 0, 100, 10)
        with a3: discount  = st.number_input("현금 할인율 (%)", 0, 50, 0)
        stock_ratio = 100 - cash_ratio

        acq_rows = []
        for _, r in df_valid.iterrows():
            base  = round(r["산출 EV(억)"] * acq_ratio / 100, 1)
            cash  = round(base * cash_ratio / 100 * (1 - discount / 100), 1)
            swap  = round(base * stock_ratio / 100, 1)
            acq_rows.append({"기업명": r["기업명"], "인수기준금액(억)": base, "현금지급액(억)": cash, "지분스왑액(억)": swap})

        df_acq = pd.DataFrame(acq_rows)
        st.table(df_acq)

        s_cash = df_acq["현금지급액(억)"].sum()
        s_swap = df_acq["지분스왑액(억)"].sum()
        f1, f2, f3 = st.columns(3)
        f1.metric("총 현금 소요액",   f"{s_cash:,.1f} 억원")
        f2.metric("총 지분스왑 규모", f"{s_swap:,.1f} 억원")
        f3.metric("딜 총 규모",       f"{s_cash + s_swap:,.1f} 억원")
